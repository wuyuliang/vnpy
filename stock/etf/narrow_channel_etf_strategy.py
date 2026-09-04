"""Research-only weekly narrow-channel strategy for 159915.SZ."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

R_ZERO_TOLERANCE = 1e-12
BAR_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume"]
WEEKLY_COLUMNS = [
    "datetime",
    "source_max_date",
    "open",
    "high",
    "low",
    "close",
    "volume",
]
TRADE_COLUMNS = [
    "trade_id",
    "symbol",
    "side",
    "signal_type",
    "signal_date",
    "entry_date",
    "entry_reference",
    "entry_fill",
    "initial_stop",
    "exit_date",
    "exit_reference",
    "exit_fill",
    "exit_reason",
    "status",
    "entry_day_stop_breached",
    "holding_days",
    "borrow_cost",
    "net_pnl",
    "r_value",
    "mfe_r",
    "mae_r",
    "net_r",
    "unrealized_r",
    "profit_capture_ratio",
]


@dataclass(frozen=True)
class NarrowChannelConfig:
    """Fixed parameters for the single-ETF quick validation."""

    symbol: str = "159915.SZ"
    channel_weeks: int = 6
    confirmation_windows: int = 2
    weekly_atr_period: int = 14
    daily_atr_period: int = 14
    one_way_cost_rate: float = 0.0008
    short_borrow_rate: float = 0.08

    def __post_init__(self) -> None:
        if self.symbol != "159915.SZ":
            raise ValueError("quick validation only supports 159915.SZ")
        if self.channel_weeks != 6 or self.confirmation_windows != 2:
            raise ValueError("quick validation channel parameters are fixed")
        if self.weekly_atr_period != 14 or self.daily_atr_period != 14:
            raise ValueError("quick validation ATR periods are fixed at 14")
        if not 0 <= self.one_way_cost_rate < 1:
            raise ValueError("one_way_cost_rate must be within [0, 1)")
        if self.short_borrow_rate < 0:
            raise ValueError("short_borrow_rate must not be negative")


@dataclass
class NarrowChannelResult:
    """Auditable daily signals, normalized trades, and R-based statistics."""

    signals: pd.DataFrame
    trades: pd.DataFrame
    summary: dict[str, Any]


@dataclass
class _OpenPosition:
    trade_id: int
    side: str
    signal_type: str
    signal_date: pd.Timestamp
    entry_date: pd.Timestamp
    entry_index: int
    entry_reference: float
    entry_fill: float
    initial_stop: float
    active_stop: float
    r_value: float
    max_high: float
    min_low: float
    entry_day_stop_breached: bool = False
    pending_t1_stop: bool = False
    pending_exit_reason: str = ""
    trailing_active: bool = False


def prepare_etf_bars(
    daily: pd.DataFrame,
    config: NarrowChannelConfig,
) -> pd.DataFrame:
    """Validate and return date-ordered OHLCV rows for the configured ETF."""
    missing = set(BAR_COLUMNS) - set(daily.columns)
    if missing:
        raise ValueError(f"ETF daily data missing columns: {sorted(missing)}")
    bars = daily.loc[daily["symbol"].astype(str).eq(config.symbol), BAR_COLUMNS].copy()
    if bars.empty:
        raise ValueError(f"ETF daily data missing symbol: {config.symbol}")
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="raise")
    if bars["datetime"].isna().any():
        raise ValueError("ETF daily data contains missing dates")
    if bars["datetime"].dt.tz is not None:
        raise ValueError("ETF daily dates must be timezone-naive")
    bars["datetime"] = bars["datetime"].dt.normalize()
    if bars["datetime"].duplicated().any():
        raise ValueError("ETF daily data contains duplicate dates")
    for column in BAR_COLUMNS[2:]:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    prices = bars[["open", "high", "low", "close"]]
    valid = (
        np.isfinite(prices).all(axis=1)
        & (prices > 0).all(axis=1)
        & (bars["high"] >= bars[["open", "close"]].max(axis=1))
        & (bars["low"] <= bars[["open", "close"]].min(axis=1))
        & (bars["high"] >= bars["low"])
        & np.isfinite(bars["volume"])
        & (bars["volume"] >= 0)
    )
    if not valid.all():
        raise ValueError("ETF daily data contains invalid OHLCV rows")
    return bars.sort_values("datetime", ignore_index=True)


def calculate_wilder_atr(frame: pd.DataFrame, period: int) -> pd.Series:
    """Calculate Wilder ATR with a simple-average seed and recursive updates."""
    previous_close = frame["close"].shift(1)
    true_range = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    atr = pd.Series(np.nan, index=frame.index, dtype=float)
    if len(true_range) < period:
        return atr
    first_position = period - 1
    atr.iloc[first_position] = float(true_range.iloc[:period].mean())
    for position in range(period, len(true_range)):
        atr.iloc[position] = (
            atr.iloc[position - 1] * (period - 1) + true_range.iloc[position]
        ) / period
    return atr


def aggregate_complete_weeks(bars: pd.DataFrame) -> pd.DataFrame:
    """Aggregate Friday-ending weeks and conservatively drop an unfinished tail."""
    if bars.empty:
        return pd.DataFrame(columns=WEEKLY_COLUMNS)
    frame = bars.copy()
    frame["week_end"] = (
        frame["datetime"].dt.to_period("W-FRI").dt.end_time.dt.normalize()
    )
    weekly = (
        frame.groupby("week_end", sort=True)
        .agg(
            source_max_date=("datetime", "max"),
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .reset_index()
        .rename(columns={"week_end": "datetime"})
    )
    data_end = pd.Timestamp(frame["datetime"].max()).normalize()
    weekly = weekly.loc[weekly["datetime"] <= data_end]
    return weekly.loc[:, WEEKLY_COLUMNS].reset_index(drop=True)


def classify_weekly_channels(
    weekly: pd.DataFrame,
    config: NarrowChannelConfig,
) -> pd.DataFrame:
    """Attach fixed weekly narrow-channel qualifications and confirmed states."""
    required = {"datetime", "open", "high", "low", "close", "volume"}
    missing = required - set(weekly.columns)
    if missing:
        raise ValueError(f"weekly data missing columns: {sorted(missing)}")
    result = weekly.copy().sort_values("datetime", ignore_index=True)
    result["ema10w"] = result["close"].ewm(
        span=10,
        adjust=False,
        min_periods=10,
    ).mean()
    result["atr14w"] = calculate_wilder_atr(result, config.weekly_atr_period)
    up_qualified = np.zeros(len(result), dtype=bool)
    down_qualified = np.zeros(len(result), dtype=bool)

    for end in range(config.channel_weeks - 1, len(result)):
        start = end - config.channel_weeks + 1
        window = result.iloc[start : end + 1]
        atr = float(result.loc[end, "atr14w"])
        ema = float(result.loc[end, "ema10w"])
        ema_before = (
            float(result.loc[end - 2, "ema10w"])
            if end >= 2
            else float("nan")
        )
        if not np.isfinite([atr, ema, ema_before]).all() or atr <= 0:
            continue
        closes = window["close"].to_numpy(dtype=float)
        changes = np.diff(closes)
        max_drawdown = float(np.max(np.maximum.accumulate(closes) - closes))
        max_rebound = float(np.max(closes - np.minimum.accumulate(closes)))
        up_qualified[end] = bool(
            np.count_nonzero(changes > 0) >= 4
            and closes[-1] - closes[0] >= atr
            and max_drawdown <= atr
            and closes[-1] > ema
            and ema > ema_before
        )
        down_qualified[end] = bool(
            np.count_nonzero(changes < 0) >= 4
            and closes[0] - closes[-1] >= atr
            and max_rebound <= atr
            and closes[-1] < ema
            and ema < ema_before
        )

    result["up_qualified"] = up_qualified
    result["down_qualified"] = down_qualified
    up_confirmed = (
        result["up_qualified"]
        .rolling(config.confirmation_windows, min_periods=config.confirmation_windows)
        .sum()
        .eq(config.confirmation_windows)
    )
    down_confirmed = (
        result["down_qualified"]
        .rolling(config.confirmation_windows, min_periods=config.confirmation_windows)
        .sum()
        .eq(config.confirmation_windows)
    )
    result["weekly_state"] = "NEUTRAL"
    result.loc[up_confirmed, "weekly_state"] = "UP_CHANNEL"
    result.loc[down_confirmed, "weekly_state"] = "DOWN_CHANNEL"
    weekly_body = (result["close"] - result["open"]).abs()
    result["strong_bear_week"] = (
        (result["close"] < result["open"])
        & (weekly_body >= 0.80 * result["atr14w"])
        & (result["close"] < result["low"].shift(1))
    )
    result["strong_bull_week"] = (
        (result["close"] > result["open"])
        & (weekly_body >= 0.80 * result["atr14w"])
        & (result["close"] > result["high"].shift(1))
    )
    result["weekly_trail_long"] = (
        result["low"].rolling(2, min_periods=2).min()
        - 0.25 * result["atr14w"]
    )
    result["weekly_trail_short"] = (
        result["high"].rolling(2, min_periods=2).max()
        + 0.25 * result["atr14w"]
    )
    return result


def evaluate_daily_entries(features: pd.DataFrame) -> pd.DataFrame:
    """Evaluate the two mirrored continuation signals on prepared features."""
    required = {
        "open",
        "high",
        "low",
        "close",
        "ema5",
        "ema10",
        "ema20",
        "atr14d",
        "weekly_state",
    }
    missing = required - set(features.columns)
    if missing:
        raise ValueError(f"daily features missing columns: {sorted(missing)}")
    result = features.copy()
    down_count = result["close"].lt(result["close"].shift(1)).rolling(5).sum()
    up_count = result["close"].gt(result["close"].shift(1)).rolling(5).sum()
    recent_low = result["low"].rolling(5, min_periods=5).min()
    recent_high = result["high"].rolling(5, min_periods=5).max()
    ema20_margin = result["close"] - result["ema20"]
    minimum_margin = ema20_margin.rolling(5, min_periods=5).min()
    maximum_margin = ema20_margin.rolling(5, min_periods=5).max()
    ready = result[["ema5", "ema10", "ema20", "atr14d"]].notna().all(axis=1)

    result["long_signal"] = (
        ready
        & result["weekly_state"].eq("UP_CHANNEL")
        & result["ema5"].gt(result["ema10"])
        & result["ema10"].gt(result["ema20"])
        & down_count.ge(2)
        & recent_low.le(result["ema10"] + 0.25 * result["atr14d"])
        & minimum_margin.ge(-0.50 * result["atr14d"])
        & result["close"].gt(result["open"])
        & result["close"].gt(result["high"].shift(1))
    )
    result["short_signal"] = (
        ready
        & result["weekly_state"].eq("DOWN_CHANNEL")
        & result["ema5"].lt(result["ema10"])
        & result["ema10"].lt(result["ema20"])
        & up_count.ge(2)
        & recent_high.ge(result["ema10"] - 0.25 * result["atr14d"])
        & maximum_margin.le(0.50 * result["atr14d"])
        & result["close"].lt(result["open"])
        & result["close"].lt(result["low"].shift(1))
    )
    if (result["long_signal"] & result["short_signal"]).any():
        raise RuntimeError("one day cannot contain both long and short signals")
    result["signal_type"] = ""
    result.loc[result["long_signal"], "signal_type"] = (
        "up_channel_pullback_long"
    )
    result.loc[result["short_signal"], "signal_type"] = (
        "down_channel_rally_short"
    )
    return result


def build_narrow_channel_signals(
    bars: pd.DataFrame,
    weekly: pd.DataFrame,
    config: NarrowChannelConfig,
) -> pd.DataFrame:
    """Build point-in-time daily features and entry/exit signals."""
    daily = bars.copy().sort_values("datetime", ignore_index=True)
    daily["ema5"] = daily["close"].ewm(
        span=5,
        adjust=False,
        min_periods=5,
    ).mean()
    daily["ema10"] = daily["close"].ewm(
        span=10,
        adjust=False,
        min_periods=10,
    ).mean()
    daily["ema20"] = daily["close"].ewm(
        span=20,
        adjust=False,
        min_periods=20,
    ).mean()
    daily["atr14d"] = calculate_wilder_atr(daily, config.daily_atr_period)

    weekly_columns = [
        "datetime",
        "weekly_state",
        "strong_bear_week",
        "strong_bull_week",
        "weekly_trail_long",
        "weekly_trail_short",
    ]
    if "source_max_date" in weekly:
        weekly_columns.append("source_max_date")
    weekly_fields = weekly[weekly_columns].rename(
        columns={
            "datetime": "weekly_event_date",
            "source_max_date": "weekly_available_date",
        }
    )
    if "weekly_available_date" not in weekly_fields:
        weekly_fields["weekly_available_date"] = weekly_fields["weekly_event_date"]
    result = pd.merge_asof(
        daily,
        weekly_fields.sort_values("weekly_available_date"),
        left_on="datetime",
        right_on="weekly_available_date",
        direction="backward",
        allow_exact_matches=True,
    )
    result["weekly_state"] = result["weekly_state"].fillna("NEUTRAL")
    changed_week = result["weekly_event_date"].ne(result["weekly_event_date"].shift(1))
    result["strong_bear_event"] = (
        result["strong_bear_week"].fillna(False).astype(bool) & changed_week
    )
    result["strong_bull_event"] = (
        result["strong_bull_week"].fillna(False).astype(bool) & changed_week
    )
    result = evaluate_daily_entries(result)
    result["long_structure_stop"] = (
        result["low"].rolling(6, min_periods=6).min()
        - 0.25 * result["atr14d"]
    )
    result["short_structure_stop"] = (
        result["high"].rolling(6, min_periods=6).max()
        + 0.25 * result["atr14d"]
    )
    result["max_entry"] = result["close"] + 0.75 * result["atr14d"]
    result["min_entry"] = result["close"] - 0.75 * result["atr14d"]
    long_failure = result["close"].lt(result["ema20"]) & result["ema5"].lt(
        result["ema10"]
    )
    short_failure = result["close"].gt(result["ema20"]) & result["ema5"].gt(
        result["ema10"]
    )
    result["long_trend_exit"] = long_failure & long_failure.shift(1, fill_value=False)
    result["short_trend_exit"] = short_failure & short_failure.shift(
        1,
        fill_value=False,
    )
    result["max_feature_source_date"] = result["datetime"]
    result["entry_status"] = np.where(
        result["long_signal"] | result["short_signal"],
        "pending",
        "",
    )
    result["entry_date"] = pd.NaT
    result["entry_fill"] = np.nan
    result["position_side"] = ""
    result["active_stop"] = np.nan
    return result


def _exit_fill(reference: float, side: str, cost_rate: float) -> float:
    if side == "LONG":
        return reference * (1 - cost_rate)
    return reference * (1 + cost_rate)


def _borrow_cost(
    position: _OpenPosition,
    date: pd.Timestamp,
    config: NarrowChannelConfig,
) -> float:
    if position.side != "SHORT":
        return 0.0
    calendar_days = max((date - position.entry_date).days, 0)
    return (
        position.entry_reference
        * config.short_borrow_rate
        * calendar_days
        / 365.0
    )


def _position_excursions(position: _OpenPosition) -> tuple[float, float]:
    if position.side == "LONG":
        mfe = (position.max_high - position.entry_fill) / position.r_value
        mae = (position.entry_fill - position.min_low) / position.r_value
    else:
        mfe = (position.entry_fill - position.min_low) / position.r_value
        mae = (position.max_high - position.entry_fill) / position.r_value
    return float(mfe), float(mae)


def _include_position_range(
    position: _OpenPosition,
    *,
    low: float,
    high: float,
) -> None:
    position.max_high = max(position.max_high, high)
    position.min_low = min(position.min_low, low)


def _closed_trade(
    position: _OpenPosition,
    *,
    exit_date: pd.Timestamp,
    exit_index: int,
    exit_reference: float,
    exit_reason: str,
    config: NarrowChannelConfig,
) -> dict[str, Any]:
    exit_fill = _exit_fill(exit_reference, position.side, config.one_way_cost_rate)
    borrow_cost = _borrow_cost(position, exit_date, config)
    if position.side == "LONG":
        net_pnl = exit_fill - position.entry_fill
    else:
        net_pnl = position.entry_fill - exit_fill - borrow_cost
    net_r = net_pnl / position.r_value
    if abs(net_r) <= R_ZERO_TOLERANCE:
        net_pnl = 0.0
        net_r = 0.0
    mfe_r, mae_r = _position_excursions(position)
    return {
        "trade_id": position.trade_id,
        "symbol": config.symbol,
        "side": position.side,
        "signal_type": position.signal_type,
        "signal_date": position.signal_date,
        "entry_date": position.entry_date,
        "entry_reference": position.entry_reference,
        "entry_fill": position.entry_fill,
        "initial_stop": position.initial_stop,
        "exit_date": exit_date,
        "exit_reference": exit_reference,
        "exit_fill": exit_fill,
        "exit_reason": exit_reason,
        "status": "CLOSED",
        "entry_day_stop_breached": position.entry_day_stop_breached,
        "holding_days": exit_index - position.entry_index,
        "borrow_cost": borrow_cost,
        "net_pnl": net_pnl,
        "r_value": position.r_value,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "net_r": net_r,
        "unrealized_r": np.nan,
        "profit_capture_ratio": net_r / mfe_r if mfe_r > 0 else np.nan,
    }


def _open_trade(
    position: _OpenPosition,
    *,
    final_date: pd.Timestamp,
    final_close: float,
    final_index: int,
    config: NarrowChannelConfig,
) -> dict[str, Any]:
    mark_fill = _exit_fill(final_close, position.side, config.one_way_cost_rate)
    borrow_cost = _borrow_cost(position, final_date, config)
    if position.side == "LONG":
        unrealized_pnl = mark_fill - position.entry_fill
    else:
        unrealized_pnl = position.entry_fill - mark_fill - borrow_cost
    mfe_r, mae_r = _position_excursions(position)
    return {
        "trade_id": position.trade_id,
        "symbol": config.symbol,
        "side": position.side,
        "signal_type": position.signal_type,
        "signal_date": position.signal_date,
        "entry_date": position.entry_date,
        "entry_reference": position.entry_reference,
        "entry_fill": position.entry_fill,
        "initial_stop": position.initial_stop,
        "exit_date": pd.NaT,
        "exit_reference": np.nan,
        "exit_fill": np.nan,
        "exit_reason": "",
        "status": "OPEN",
        "entry_day_stop_breached": position.entry_day_stop_breached,
        "holding_days": final_index - position.entry_index,
        "borrow_cost": borrow_cost,
        "net_pnl": np.nan,
        "r_value": position.r_value,
        "mfe_r": mfe_r,
        "mae_r": mae_r,
        "net_r": np.nan,
        "unrealized_r": unrealized_pnl / position.r_value,
        "profit_capture_ratio": np.nan,
    }


def _direction_summary(
    trades: pd.DataFrame,
    signals: pd.DataFrame,
    side: str | None,
) -> dict[str, Any]:
    selected = trades if side is None else trades.loc[trades["side"].eq(side)]
    completed = selected.loc[selected["status"].eq("CLOSED")]
    signal_mask = signals["long_signal"] | signals["short_signal"]
    if side == "LONG":
        signal_mask = signals["long_signal"]
    elif side == "SHORT":
        signal_mask = signals["short_signal"]
    normalized_net_r = completed["net_r"].where(
        completed["net_r"].abs() > R_ZERO_TOLERANCE,
        0.0,
    )
    gains = float(normalized_net_r.loc[normalized_net_r > 0].sum())
    losses = float(-normalized_net_r.loc[normalized_net_r < 0].sum())
    profit_factor = gains / losses if losses > 0 else None
    if completed.empty:
        profit_factor_status = "NO_COMPLETED_TRADES"
    elif losses == 0 and gains > 0:
        profit_factor_status = "INFINITE_NO_LOSSES"
    elif losses > 0:
        profit_factor_status = "FINITE"
    else:
        profit_factor_status = "NO_GAIN_OR_LOSS"
    exit_order = completed.sort_values("exit_date").index
    cumulative = normalized_net_r.loc[exit_order].cumsum().to_numpy(float)
    if cumulative.size:
        path = np.concatenate(([0.0], cumulative))
        max_r_drawdown = float(np.max(np.maximum.accumulate(path) - path))
    else:
        max_r_drawdown = 0.0
    big_trends = completed.loc[completed["mfe_r"] >= 4.0]
    capture = big_trends["profit_capture_ratio"].dropna()
    capture_median = float(capture.median()) if not capture.empty else None
    big_trend_capture_status = (
        "SUFFICIENT" if len(capture) >= 5 else "INSUFFICIENT_SAMPLE"
    )
    completed_count = int(len(completed))
    total_net_r = float(normalized_net_r.sum())
    pf_pass = profit_factor is None and gains > 0 or (
        profit_factor is not None and profit_factor > 1.10
    )
    capture_pass = len(capture) < 5 or (
        capture_median is not None and capture_median >= 0.35
    )
    if side is None:
        verdict = "NOT_APPLICABLE"
    elif completed_count < 10:
        verdict = "INCONCLUSIVE"
    elif total_net_r > 0 and pf_pass and max_r_drawdown <= 12.0 and capture_pass:
        verdict = "PROMISING"
    else:
        verdict = "REJECTED"
    return {
        "signal_count": int(signal_mask.sum()),
        "filled_count": int(
            signals.loc[signal_mask, "entry_status"].eq("filled").sum()
        ),
        "skipped_count": int(
            signals.loc[signal_mask, "entry_status"].ne("filled").sum()
        ),
        "completed_trade_count": completed_count,
        "open_trade_count": int(selected["status"].eq("OPEN").sum()),
        "win_rate": (
            float(normalized_net_r.gt(0).mean()) if completed_count else None
        ),
        "total_net_r": total_net_r,
        "average_net_r": (
            float(normalized_net_r.mean()) if completed_count else None
        ),
        "profit_factor": profit_factor,
        "profit_factor_status": profit_factor_status,
        "max_cumulative_r_drawdown": max_r_drawdown,
        "median_holding_days": (
            float(completed["holding_days"].median()) if completed_count else None
        ),
        "borrow_cost": float(selected["borrow_cost"].sum()),
        "big_trend_trade_count": int(len(big_trends)),
        "median_profit_capture_ratio": capture_median,
        "big_trend_capture_status": big_trend_capture_status,
        "verdict": verdict,
    }


def _build_summary(
    signals: pd.DataFrame,
    trades: pd.DataFrame,
    config: NarrowChannelConfig,
) -> dict[str, Any]:
    return {
        "symbol": config.symbol,
        "execution_mode": "research_only",
        "actual_start_date": str(pd.Timestamp(signals.iloc[0]["datetime"]).date()),
        "actual_end_date": str(pd.Timestamp(signals.iloc[-1]["datetime"]).date()),
        "bar_count": int(len(signals)),
        "long": _direction_summary(trades, signals, "LONG"),
        "short": _direction_summary(trades, signals, "SHORT"),
        "combined": _direction_summary(trades, signals, None),
    }


def run_narrow_channel_backtest(
    signals: pd.DataFrame,
    config: NarrowChannelConfig,
) -> NarrowChannelResult:
    """Run a one-unit, one-position-at-a-time T+1 event simulation."""
    required = {
        "datetime",
        "open",
        "high",
        "low",
        "close",
        "atr14d",
        "long_signal",
        "short_signal",
        "signal_type",
        "long_structure_stop",
        "short_structure_stop",
        "max_entry",
        "min_entry",
        "long_trend_exit",
        "short_trend_exit",
        "strong_bear_event",
        "strong_bull_event",
        "weekly_trail_long",
        "weekly_trail_short",
    }
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"backtest signals missing columns: {sorted(missing)}")
    result = signals.copy().sort_values("datetime", ignore_index=True)
    result["datetime"] = pd.to_datetime(result["datetime"], errors="raise").dt.normalize()
    if result.empty:
        raise ValueError("backtest signals must not be empty")
    if result["datetime"].duplicated().any():
        raise ValueError("backtest signals contain duplicate dates")
    if (result["long_signal"] & result["short_signal"]).any():
        raise ValueError("one row cannot contain both long and short signals")
    result["entry_status"] = np.where(
        result["long_signal"] | result["short_signal"],
        "pending",
        "",
    )
    result["entry_date"] = pd.NaT
    result["entry_fill"] = np.nan
    result["position_side"] = ""
    result["active_stop"] = np.nan
    position: _OpenPosition | None = None
    trade_rows: list[dict[str, Any]] = []
    next_trade_id = 1

    for index, row in result.iterrows():
        date = pd.Timestamp(row["datetime"])
        had_position_at_open = position is not None

        if position is not None:
            if position.side == "SHORT" and position.trailing_active:
                break_even = (
                    position.entry_fill - _borrow_cost(position, date, config)
                ) / (1 + config.one_way_cost_rate)
                position.active_stop = min(position.active_stop, break_even)
            open_price = float(row["open"])
            _include_position_range(
                position,
                low=open_price,
                high=open_price,
            )
            gap_stop = (
                position.side == "LONG" and open_price <= position.active_stop
            ) or (
                position.side == "SHORT" and open_price >= position.active_stop
            )
            if position.pending_t1_stop:
                trade_rows.append(
                    _closed_trade(
                        position,
                        exit_date=date,
                        exit_index=index,
                        exit_reference=open_price,
                        exit_reason="deferred_t1_stop",
                        config=config,
                    )
                )
                position = None
            elif gap_stop:
                trade_rows.append(
                    _closed_trade(
                        position,
                        exit_date=date,
                        exit_index=index,
                        exit_reference=open_price,
                        exit_reason="hard_stop_gap",
                        config=config,
                    )
                )
                position = None
            elif position.pending_exit_reason:
                trade_rows.append(
                    _closed_trade(
                        position,
                        exit_date=date,
                        exit_index=index,
                        exit_reference=open_price,
                        exit_reason=position.pending_exit_reason,
                        config=config,
                    )
                )
                position = None

        if index > 0 and not had_position_at_open and position is None:
            signal_index = index - 1
            signal_row = result.iloc[signal_index]
            if bool(signal_row["long_signal"]):
                valid = (
                    float(row["open"]) > float(signal_row["long_structure_stop"])
                    and float(row["open"]) <= float(signal_row["max_entry"])
                )
                side = "LONG"
            elif bool(signal_row["short_signal"]):
                valid = (
                    float(row["open"]) >= float(signal_row["min_entry"])
                    and float(row["open"]) < float(signal_row["short_structure_stop"])
                )
                side = "SHORT"
            else:
                valid = False
                side = ""
            if side and not valid:
                result.loc[signal_index, "entry_status"] = (
                    "skipped_gap_or_invalidated"
                )
            elif side:
                reference = float(row["open"])
                if side == "LONG":
                    entry_fill = reference * (1 + config.one_way_cost_rate)
                    initial_stop = min(
                        float(signal_row["long_structure_stop"]),
                        entry_fill - 1.50 * float(signal_row["atr14d"]),
                    )
                    r_value = entry_fill - initial_stop
                else:
                    entry_fill = reference * (1 - config.one_way_cost_rate)
                    initial_stop = max(
                        float(signal_row["short_structure_stop"]),
                        entry_fill + 1.50 * float(signal_row["atr14d"]),
                    )
                    r_value = initial_stop - entry_fill
                if not np.isfinite(r_value) or r_value <= 0:
                    raise RuntimeError("filled trade must have positive R")
                position = _OpenPosition(
                    trade_id=next_trade_id,
                    side=side,
                    signal_type=str(signal_row["signal_type"]),
                    signal_date=pd.Timestamp(signal_row["datetime"]),
                    entry_date=date,
                    entry_index=index,
                    entry_reference=reference,
                    entry_fill=entry_fill,
                    initial_stop=initial_stop,
                    active_stop=initial_stop,
                    r_value=r_value,
                    max_high=reference,
                    min_low=reference,
                )
                next_trade_id += 1
                result.loc[signal_index, "entry_status"] = "filled"
                result.loc[signal_index, "entry_date"] = date
                result.loc[signal_index, "entry_fill"] = entry_fill

        if index > 0 and had_position_at_open:
            signal_index = index - 1
            if bool(result.loc[signal_index, "long_signal"]) or bool(
                result.loc[signal_index, "short_signal"]
            ):
                result.loc[signal_index, "entry_status"] = "ignored_position"

        if position is not None:
            is_entry_day = index == position.entry_index
            stop_touched = (
                position.side == "LONG" and float(row["low"]) <= position.active_stop
            ) or (
                position.side == "SHORT" and float(row["high"]) >= position.active_stop
            )
            if is_entry_day:
                _include_position_range(
                    position,
                    low=float(row["low"]),
                    high=float(row["high"]),
                )
                if stop_touched:
                    position.entry_day_stop_breached = True
                    position.pending_t1_stop = True
            elif stop_touched:
                _include_position_range(
                    position,
                    low=min(float(row["open"]), position.active_stop),
                    high=max(float(row["open"]), position.active_stop),
                )
                trade_rows.append(
                    _closed_trade(
                        position,
                        exit_date=date,
                        exit_index=index,
                        exit_reference=position.active_stop,
                        exit_reason="hard_stop",
                        config=config,
                    )
                )
                position = None
            else:
                _include_position_range(
                    position,
                    low=float(row["low"]),
                    high=float(row["high"]),
                )

        if position is not None and not position.pending_t1_stop:
            if position.side == "LONG":
                if bool(row["strong_bear_event"]):
                    position.pending_exit_reason = "strong_opposite_week"
                elif bool(row["long_trend_exit"]):
                    position.pending_exit_reason = "daily_trend_exit"
            else:
                if bool(row["strong_bull_event"]):
                    position.pending_exit_reason = "strong_opposite_week"
                elif bool(row["short_trend_exit"]):
                    position.pending_exit_reason = "daily_trend_exit"

            mfe_r, _ = _position_excursions(position)
            if mfe_r >= 2.0:
                position.trailing_active = True
                borrow_to_date = _borrow_cost(position, date, config)
                if position.side == "LONG":
                    break_even = position.entry_fill / (1 - config.one_way_cost_rate)
                    weekly_stop = float(row["weekly_trail_long"])
                    candidates = [position.active_stop, break_even]
                    if np.isfinite(weekly_stop):
                        candidates.append(weekly_stop)
                    position.active_stop = max(candidates)
                else:
                    break_even = (
                        position.entry_fill - borrow_to_date
                    ) / (1 + config.one_way_cost_rate)
                    weekly_stop = float(row["weekly_trail_short"])
                    candidates = [position.active_stop, break_even]
                    if np.isfinite(weekly_stop):
                        candidates.append(weekly_stop)
                    position.active_stop = min(candidates)

        if position is not None:
            result.loc[index, "position_side"] = position.side
            result.loc[index, "active_stop"] = position.active_stop

    pending = result["entry_status"].eq("pending")
    result.loc[pending, "entry_status"] = "expired_end"
    if position is not None:
        final = result.iloc[-1]
        trade_rows.append(
            _open_trade(
                position,
                final_date=pd.Timestamp(final["datetime"]),
                final_close=float(final["close"]),
                final_index=len(result) - 1,
                config=config,
            )
        )
    trades = pd.DataFrame(trade_rows, columns=TRADE_COLUMNS)
    summary = _build_summary(result, trades, config)
    return NarrowChannelResult(signals=result, trades=trades, summary=summary)
