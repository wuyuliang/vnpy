"""Causal rules for the daily/5-minute trend strategy."""
from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
import math

import numpy as np
import pandas as pd

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.skills.trend_strategies._common import atr, ema
from cta.strategy.brooks.core.risk.sizing import calc_position_size


OHLC = ("open", "high", "low", "close")


@dataclass(frozen=True)
class SignalCandidate:
    """Completed-bar setup ready for next-event order activation."""

    setup_type: str
    direction: int
    signal_index: int
    signal_time: object
    known_at: object
    trigger: float
    stop_price: float
    target_price: float = math.nan
    pullback_start_index: int = -1


@dataclass(frozen=True)
class PullbackState:
    """Causal pullback range accumulated before the breakout bar."""

    direction: int = 0
    start_index: int = -1
    bars: int = 0
    range_high: float = math.nan
    range_low: float = math.nan


@dataclass(frozen=True)
class ObstacleDecision:
    rejected: bool
    reason: str = ""
    price: float = math.nan
    distance_atr: float = math.nan


@dataclass(frozen=True)
class RiskDecision:
    quantity: int
    risk_budget: float
    loss_per_lot: float
    reason: str = ""


def build_daily_context(
    daily_bars: pd.DataFrame,
    config: MultiTimeframeTrendConfig,
) -> pd.DataFrame:
    """Build strict EMA direction and ATR from completed daily bars."""
    _require_columns(daily_bars, (*OHLC, "bar_end"))
    result = daily_bars.copy().reset_index(drop=True)
    _validate_bar_end(result)
    close = pd.to_numeric(result["close"], errors="coerce")
    open_price = pd.to_numeric(result["open"], errors="coerce")
    high = pd.to_numeric(result["high"], errors="coerce")
    low = pd.to_numeric(result["low"], errors="coerce")
    result["daily_ema5"] = ema(close, config.daily_ema_fast)
    result["daily_ema10"] = ema(close, config.daily_ema_mid)
    result["daily_ema20"] = ema(close, config.daily_ema_slow)
    result["daily_atr14"] = atr(high, low, close, config.atr_period)
    body_high = pd.concat([open_price, close], axis=1).max(axis=1)
    body_low = pd.concat([open_price, close], axis=1).min(axis=1)
    result["prior_5d_high"] = body_high.shift(1).rolling(5, min_periods=5).max()
    result["prior_5d_low"] = body_low.shift(1).rolling(5, min_periods=5).min()
    valid = result[["daily_ema5", "daily_ema10", "daily_ema20"]].notna().all(axis=1)
    bullish = (
        (result["daily_ema5"] > result["daily_ema10"])
        & (result["daily_ema10"] > result["daily_ema20"])
    )
    bearish = (
        (result["daily_ema5"] < result["daily_ema10"])
        & (result["daily_ema10"] < result["daily_ema20"])
    )
    result["daily_direction"] = np.select(
        [valid & bullish, valid & bearish],
        [1, -1],
        default=0,
    ).astype(int)
    in_bull_trend = result["daily_direction"].eq(1)
    bull_trend_starts = in_bull_trend & ~in_bull_trend.shift(fill_value=False)
    result["daily_bull_trend_id"] = (
        bull_trend_starts.cumsum().where(in_bull_trend, 0).astype(int)
    )
    return attach_confirmed_pivots(
        result,
        left=config.daily_pivot_left,
        right=config.daily_pivot_right,
    )


def build_intraday_context(
    bars: pd.DataFrame,
    config: MultiTimeframeTrendConfig,
) -> pd.DataFrame:
    """Build 5-minute ATR, prior-volume threshold, and confirmed pivots."""
    _require_columns(bars, (*OHLC, "bar_end", "volume"))
    result = bars.copy().reset_index(drop=True)
    _validate_bar_end(result)
    high = pd.to_numeric(result["high"], errors="coerce")
    low = pd.to_numeric(result["low"], errors="coerce")
    close = pd.to_numeric(result["close"], errors="coerce")
    volume = pd.to_numeric(result["volume"], errors="coerce")
    result["atr14"] = atr(high, low, close, config.atr_period)
    result["volume_threshold"] = volume.shift(1).rolling(
        config.volume_lookback,
        min_periods=config.volume_lookback,
    ).quantile(config.volume_quantile)
    result["volume_expanded"] = (
        volume > result["volume_threshold"]
    ).fillna(False)
    attach_session_range(result, config.entry_range_lookback_days)
    return attach_confirmed_pivots(
        result,
        left=config.intraday_pivot_left,
        right=config.intraday_pivot_right,
    )


def attach_session_range(
    frame: pd.DataFrame,
    lookback_days: int,
) -> pd.DataFrame:
    """Attach the causal high/low of the prior ``lookback_days`` plus today so far.

    The window is every completed bar of the previous ``lookback_days`` exchange
    trade dates, extended with the current date's bars up to and including the
    current one. Nothing after the current bar is used. When the history is
    shorter than ``lookback_days`` the columns stay NaN so callers fail open
    rather than judging a range they cannot see.
    """
    frame["range_window_high"] = np.nan
    frame["range_window_low"] = np.nan
    if "exchange_trade_date" not in frame.columns or frame.empty:
        return frame
    dates = frame["exchange_trade_date"]
    if dates.isna().all():
        return frame
    high = pd.to_numeric(frame["high"], errors="coerce")
    low = pd.to_numeric(frame["low"], errors="coerce")
    grouped_high = high.groupby(dates, sort=False)
    grouped_low = low.groupby(dates, sort=False)
    day_high = grouped_high.max().sort_index()
    day_low = grouped_low.min().sort_index()
    window = int(lookback_days)
    prior_high = day_high.rolling(window, min_periods=window).max().shift(1)
    prior_low = day_low.rolling(window, min_periods=window).min().shift(1)
    today_high = grouped_high.cummax()
    today_low = grouped_low.cummin()
    mapped_high = dates.map(prior_high)
    mapped_low = dates.map(prior_low)
    frame["range_window_high"] = np.maximum(mapped_high, today_high)
    frame["range_window_low"] = np.minimum(mapped_low, today_low)
    return frame


def entry_range_position(
    trigger: float,
    range_high: float,
    range_low: float,
) -> float:
    """Return where ``trigger`` sits in the window, 0 at the low and 1 at the high."""
    if not all(np.isfinite(value) for value in (trigger, range_high, range_low)):
        return float("nan")
    span = range_high - range_low
    if span <= 0:
        return float("nan")
    return (trigger - range_low) / span


def detect_always_in(
    bars: pd.DataFrame,
    *,
    index: int,
    direction: int,
    config: MultiTimeframeTrendConfig,
    tick_size: float,
) -> SignalCandidate | None:
    """Detect persistent 5-minute progression in the permitted direction."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    tick = _positive_tick(tick_size)
    window_size = config.always_in_window
    if index < window_size - 1 or index >= len(bars):
        return None
    swing_column = "latest_swing_low" if direction > 0 else "latest_swing_high"
    _require_columns(
        bars,
        ("bar_end", "high", "low", "close", "atr14", swing_column),
    )
    window = bars.iloc[index - window_size + 1 : index + 1]
    high = pd.to_numeric(window["high"], errors="coerce").to_numpy(float)
    low = pd.to_numeric(window["low"], errors="coerce").to_numpy(float)
    close = pd.to_numeric(window["close"], errors="coerce").to_numpy(float)
    atr_value = float(bars.iloc[index]["atr14"])
    if not all(np.isfinite(values).all() for values in (high, low, close)):
        return None
    if not np.isfinite(atr_value) or atr_value <= 0:
        return None

    row = bars.iloc[index]
    signal_time = row["bar_end"]
    if direction > 0:
        swing = float(row["latest_swing_low"])
        progresses = int((np.diff(high) > 0).sum())
        if (
            not np.isfinite(swing)
            or progresses < config.always_in_min_progress
            or close[-1] <= close[0]
            or float(np.min(low)) < swing
        ):
            return None
        trigger = _round_up(float(np.max(high)) + config.entry_buffer_ticks * tick, tick)
        stop = _round_down(swing - config.trailing_buffer_atr * atr_value, tick)
        known_at = row.get("latest_swing_low_known_at", signal_time)
        if stop >= trigger:
            return None
    else:
        swing = float(row["latest_swing_high"])
        progresses = int((np.diff(low) < 0).sum())
        if (
            not np.isfinite(swing)
            or progresses < config.always_in_min_progress
            or close[-1] >= close[0]
            or float(np.max(high)) > swing
        ):
            return None
        trigger = _round_down(float(np.min(low)) - config.entry_buffer_ticks * tick, tick)
        stop = _round_up(swing + config.trailing_buffer_atr * atr_value, tick)
        known_at = row.get("latest_swing_high_known_at", signal_time)
        if stop <= trigger:
            return None
    return SignalCandidate(
        setup_type="always_in",
        direction=direction,
        signal_index=index,
        signal_time=signal_time,
        known_at=known_at,
        trigger=trigger,
        stop_price=stop,
    )


def advance_pullback_state(
    bars: pd.DataFrame,
    *,
    index: int,
    direction: int,
    state: PullbackState,
    config: MultiTimeframeTrendConfig,
    tick_size: float,
) -> tuple[PullbackState, SignalCandidate | None]:
    """Advance one direction's pullback range and emit a confirmed breakout."""
    if direction not in (-1, 0, 1):
        raise ValueError("direction must be -1, 0, or 1")
    if index < 0 or index >= len(bars):
        raise IndexError("index outside bars")
    if direction == 0 or index == 0:
        return PullbackState(), None
    _require_columns(
        bars,
        ("bar_end", "high", "low", "close", "volume_expanded"),
    )
    tick = _positive_tick(tick_size)
    row = bars.iloc[index]
    previous = bars.iloc[index - 1]
    close = float(row["close"])
    previous_close = float(previous["close"])
    high = float(row["high"])
    low = float(row["low"])
    if not all(np.isfinite(value) for value in (close, previous_close, high, low)):
        return PullbackState(), None

    current = state if state.direction == direction else PullbackState()
    if current.bars == 0:
        starts = close < previous_close if direction > 0 else close > previous_close
        if not starts:
            return current, None
        return PullbackState(
            direction=direction,
            start_index=index,
            bars=1,
            range_high=high,
            range_low=low,
        ), None

    epsilon = tick * 1e-9
    is_breakout = close > current.range_high + epsilon if direction > 0 else close < current.range_low - epsilon
    if is_breakout and (
        config.pullback_min_bars <= current.bars <= config.pullback_max_bars
        and bool(row["volume_expanded"])
    ):
        if direction > 0:
            trigger = _round_up(high + config.entry_buffer_ticks * tick, tick)
            stop = _round_down(current.range_low - tick, tick)
        else:
            trigger = _round_down(low - config.entry_buffer_ticks * tick, tick)
            stop = _round_up(current.range_high + tick, tick)
        candidate = SignalCandidate(
            setup_type="pullback_breakout",
            direction=direction,
            signal_index=index,
            signal_time=row["bar_end"],
            known_at=row["bar_end"],
            trigger=trigger,
            stop_price=stop,
            pullback_start_index=current.start_index,
        )
        return PullbackState(), candidate

    if is_breakout:
        return PullbackState(), None
    if current.bars >= config.pullback_max_bars:
        return PullbackState(), None
    return PullbackState(
        direction=direction,
        start_index=current.start_index,
        bars=current.bars + 1,
        range_high=max(current.range_high, high),
        range_low=min(current.range_low, low),
    ), None


def assess_obstacle(
    candidate: SignalCandidate,
    daily_context: pd.DataFrame,
    config: MultiTimeframeTrendConfig,
) -> ObstacleDecision:
    """Reject a candidate when the nearest daily obstacle leaves too little room."""
    _require_columns(
        daily_context,
        ("bar_end", "high", "low", "daily_atr14", "latest_swing_high", "latest_swing_low"),
    )
    if candidate.direction not in (-1, 1):
        raise ValueError("candidate direction must be -1 or 1")
    timestamps = pd.to_datetime(daily_context["bar_end"], errors="coerce")
    signal_time = pd.Timestamp(candidate.signal_time)
    visible = daily_context.loc[timestamps <= signal_time].tail(
        config.daily_obstacle_lookback
    )
    if visible.empty:
        return ObstacleDecision(True, "DAILY_CONTEXT_UNAVAILABLE")
    daily_atr = float(visible.iloc[-1]["daily_atr14"])
    if not np.isfinite(daily_atr) or daily_atr <= 0:
        return ObstacleDecision(True, "DAILY_ATR_UNAVAILABLE")

    if candidate.direction > 0:
        raw = [float(pd.to_numeric(visible["high"], errors="coerce").max())]
        raw.extend(_visible_pivot_prices(visible, "high"))
        obstacles = [value for value in raw if value >= candidate.trigger]
        if not obstacles:
            return ObstacleDecision(False)
        nearest = min(obstacles)
        distance = nearest - candidate.trigger
    else:
        raw = [float(pd.to_numeric(visible["low"], errors="coerce").min())]
        raw.extend(_visible_pivot_prices(visible, "low"))
        obstacles = [value for value in raw if value <= candidate.trigger]
        if not obstacles:
            return ObstacleDecision(False)
        nearest = max(obstacles)
        distance = candidate.trigger - nearest

    normalized = distance / daily_atr
    if normalized <= config.obstacle_buffer_atr:
        return ObstacleDecision(True, "HTF_OBSTACLE_NEAR", nearest, normalized)
    return ObstacleDecision(False, price=nearest, distance_atr=normalized)


def size_for_risk(
    *,
    equity: float,
    entry: float,
    stop: float,
    multiplier: float,
    stressed_round_trip_cost: float,
    risk_per_trade: float,
) -> RiskDecision:
    """Size from structural-stop loss plus stressed round-trip cost per lot."""
    values = (equity, entry, stop, multiplier)
    if not all(np.isfinite(value) and value > 0 for value in values):
        raise ValueError("equity, prices, and multiplier must be finite and positive")
    if not np.isfinite(stressed_round_trip_cost) or stressed_round_trip_cost < 0:
        raise ValueError("stressed_round_trip_cost must be finite and nonnegative")
    if not 0.01 <= risk_per_trade <= 0.02:
        raise ValueError("risk_per_trade must be in [0.01, 0.02]")
    raw_distance = abs(entry - stop)
    if raw_distance <= 0:
        raise ValueError("entry and stop must differ")
    loss_per_lot = raw_distance * multiplier + stressed_round_trip_cost
    effective_distance = loss_per_lot / multiplier
    quantity = calc_position_size(
        equity=equity,
        stop_distance=effective_distance,
        contract_size=multiplier,
        risk_pct=risk_per_trade,
        min_qty=0,
    )
    return RiskDecision(
        quantity=quantity,
        risk_budget=equity * risk_per_trade,
        loss_per_lot=loss_per_lot,
        reason="" if quantity > 0 else "RISK_BELOW_ONE_LOT",
    )


def two_r_target(entry: float, stop: float, direction: int, target_r: float) -> float:
    """Return the fixed target from actual entry and structural stop."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if not all(np.isfinite(value) for value in (entry, stop, target_r)) or target_r <= 0:
        raise ValueError("entry, stop, and target_r must be valid")
    risk = direction * (entry - stop)
    if risk <= 0:
        raise ValueError("stop must be on the loss side of entry")
    return float(entry + direction * target_r * risk)


def advance_trailing_stop(
    *,
    current_stop: float,
    direction: int,
    confirmed_swing: float,
    atr_value: float,
    buffer_atr: float,
    tick_size: float,
) -> float:
    """Move a swing-based stop only in the position's favorable direction."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if not all(
        np.isfinite(value)
        for value in (current_stop, confirmed_swing, atr_value, buffer_atr)
    ):
        raise ValueError("trailing inputs must be finite")
    if atr_value <= 0 or buffer_atr < 0:
        raise ValueError("ATR must be positive and buffer nonnegative")
    tick = _positive_tick(tick_size)
    if direction > 0:
        candidate = _round_down(confirmed_swing - buffer_atr * atr_value, tick)
        return max(float(current_stop), candidate)
    candidate = _round_up(confirmed_swing + buffer_atr * atr_value, tick)
    return min(float(current_stop), candidate)


def attach_confirmed_pivots(
    bars: pd.DataFrame,
    *,
    left: int,
    right: int,
) -> pd.DataFrame:
    """Attach the latest pivots only from each pivot's confirmation bar onward."""
    _require_columns(bars, ("bar_end", "high", "low"))
    if left < 1 or right < 1:
        raise ValueError("pivot left/right must be positive")
    result = bars.copy().reset_index(drop=True)
    _validate_bar_end(result)
    high = pd.to_numeric(result["high"], errors="coerce")
    low = pd.to_numeric(result["low"], errors="coerce")
    events: dict[int, list[tuple[str, float, object, object]]] = {}

    for position in range(left, len(result) - right):
        start = position - left
        stop = position + right + 1
        other_positions = [idx for idx in range(start, stop) if idx != position]
        known_position = position + right
        pivot_time = result.loc[position, "bar_end"]
        known_at = result.loc[known_position, "bar_end"]
        if high.iloc[position] > high.iloc[other_positions].max():
            events.setdefault(known_position, []).append(
                ("high", float(high.iloc[position]), pivot_time, known_at)
            )
        if low.iloc[position] < low.iloc[other_positions].min():
            events.setdefault(known_position, []).append(
                ("low", float(low.iloc[position]), pivot_time, known_at)
            )

    latest: dict[str, tuple[float, object, object] | None] = {"high": None, "low": None}
    values: dict[str, list[object]] = {
        "latest_swing_high": [],
        "latest_swing_high_pivot_time": [],
        "latest_swing_high_known_at": [],
        "latest_swing_low": [],
        "latest_swing_low_pivot_time": [],
        "latest_swing_low_known_at": [],
    }
    for position in range(len(result)):
        for kind, price, pivot_time, known_at in events.get(position, []):
            latest[kind] = (price, pivot_time, known_at)
        for kind in ("high", "low"):
            event = latest[kind]
            prefix = f"latest_swing_{kind}"
            values[prefix].append(np.nan if event is None else event[0])
            values[f"{prefix}_pivot_time"].append(None if event is None else event[1])
            values[f"{prefix}_known_at"].append(None if event is None else event[2])
    for column, column_values in values.items():
        result[column] = column_values
    return result


def _validate_bar_end(frame: pd.DataFrame) -> None:
    timestamps = pd.to_datetime(frame["bar_end"], errors="coerce")
    if timestamps.isna().any():
        raise ValueError("bar_end contains invalid timestamps")
    if not timestamps.is_monotonic_increasing:
        raise ValueError("bar_end must be increasing")


def _positive_tick(tick_size: float) -> float:
    tick = float(tick_size)
    if not np.isfinite(tick) or tick <= 0:
        raise ValueError("tick_size must be finite and positive")
    return tick


def _visible_pivot_prices(frame: pd.DataFrame, kind: str) -> list[float]:
    price_column = f"latest_swing_{kind}"
    pivot_time_column = f"{price_column}_pivot_time"
    selected = frame
    if pivot_time_column in frame:
        valid = frame[pivot_time_column].notna()
        if not valid.any():
            return []
        selected = frame.loc[valid]
        pivot_times = pd.to_datetime(selected[pivot_time_column], errors="coerce")
        window_start = pd.Timestamp(frame.iloc[0]["bar_end"])
        selected = selected.loc[pivot_times >= window_start]
    return pd.to_numeric(selected[price_column], errors="coerce").dropna().astype(float).drop_duplicates().tolist()


def _round_up(price: float, tick: float) -> float:
    return float(math.ceil(price / tick - 1e-12) * tick)


def _round_down(price: float, tick: float) -> float:
    return float(math.floor(price / tick + 1e-12) * tick)


def _require_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [column for column in columns if column not in frame]
    if missing:
        raise KeyError(f"missing required columns: {','.join(missing)}")


__all__ = [
    "attach_session_range",
    "entry_range_position",
    "ObstacleDecision",
    "PullbackState",
    "RiskDecision",
    "SignalCandidate",
    "advance_pullback_state",
    "advance_trailing_stop",
    "assess_obstacle",
    "attach_confirmed_pivots",
    "build_daily_context",
    "build_intraday_context",
    "detect_always_in",
    "size_for_risk",
    "two_r_target",
]
