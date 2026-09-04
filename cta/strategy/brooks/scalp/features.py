"""Causal price-action features used by the rule-only scalp strategy."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from cta.feature.price_action_bars import (
    bear_reversal_bar,
    breakout_down,
    breakout_up,
    bull_reversal_bar,
)
from cta.feature.price_action_quality import (
    breakout_body_ratio,
    breakout_close_pos,
    ema_slope,
    overlap_ratio,
    trend_bar_ratio,
)
from cta.feature.price_action_structure import (
    always_in_direction,
    buy_climax,
    momentum_decay,
    sell_climax,
)
from cta.feature.price_action_swings import (
    barb_wire,
    breakout_strength,
    follow_through,
    trend_strength_score,
)

OHLC_COLUMNS = ("open", "high", "low", "close")


def wilder_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int = 14,
) -> pd.Series:
    """Return Wilder ATR seeded by the arithmetic mean of the first TRs."""
    if period < 1:
        raise ValueError("period must be positive")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1, skipna=True)
    result = pd.Series(np.nan, index=close.index, dtype=float)
    if len(true_range) < period:
        return result
    seed = float(true_range.iloc[:period].mean())
    if not np.isfinite(seed) or seed <= 0:
        return result
    result.iloc[period - 1] = seed
    prior = seed
    for position in range(period, len(true_range)):
        value = float(true_range.iloc[position])
        if not np.isfinite(value):
            prior = np.nan
        elif np.isfinite(prior):
            prior = ((period - 1) * prior + value) / period
        result.iloc[position] = prior
    return result


def causal_percentile_rank(values: pd.Series, lookback: int = 252) -> pd.Series:
    """Rank each value only against the preceding completed window."""
    if lookback < 1:
        raise ValueError("lookback must be positive")
    out = pd.Series(np.nan, index=values.index, dtype=float)
    numeric = pd.to_numeric(values, errors="coerce")
    for position in range(lookback, len(numeric)):
        current = float(numeric.iloc[position])
        prior = numeric.iloc[position - lookback : position]
        if np.isfinite(current) and prior.notna().all():
            out.iloc[position] = 100.0 * float((prior <= current).sum()) / lookback
    return out


def confirmed_swings(frame: pd.DataFrame, *, left: int = 2, right: int = 2) -> pd.DataFrame:
    """Return pivots with their causal confirmation timestamp."""
    _require_columns(frame, (*OHLC_COLUMNS, "bar_end"))
    rows: list[dict[str, object]] = []
    for position in range(left, len(frame) - right):
        window = frame.iloc[position - left : position + right + 1]
        bar = frame.iloc[position]
        known_at = frame.iloc[position + right]["bar_end"]
        high = float(bar["high"])
        low = float(bar["low"])
        other_highs = window["high"].drop(window.index[left])
        other_lows = window["low"].drop(window.index[left])
        if bool((high > other_highs).all()):
            rows.append(
                {"kind": "HIGH", "price": high, "pivot_bar_end": bar["bar_end"], "known_at": known_at}
            )
        if bool((low < other_lows).all()):
            rows.append(
                {"kind": "LOW", "price": low, "pivot_bar_end": bar["bar_end"], "known_at": known_at}
            )
    return pd.DataFrame(rows, columns=["kind", "price", "pivot_bar_end", "known_at"])


def add_causal_features(frame: pd.DataFrame) -> pd.DataFrame:
    """Add only local, prefix-invariant features to completed bars."""
    _require_columns(frame, OHLC_COLUMNS)
    result = frame.copy().reset_index(drop=True)
    open_ = pd.to_numeric(result["open"], errors="coerce")
    high = pd.to_numeric(result["high"], errors="coerce")
    low = pd.to_numeric(result["low"], errors="coerce")
    close = pd.to_numeric(result["close"], errors="coerce")
    volume = _numeric_column(result, "volume")
    turnover = _numeric_column(result, "turnover")

    result["atr_14"] = wilder_atr(high, low, close, 14)
    result["ema_20"] = close.ewm(span=20, adjust=False).mean()
    result["ema_60"] = close.ewm(span=60, adjust=False).mean()

    result["range_high_20"] = high.rolling(20, min_periods=20).max()
    result["range_low_20"] = low.rolling(20, min_periods=20).min()
    range_width = (result["range_high_20"] - result["range_low_20"]).where(lambda value: value > 0)
    result["causal_range_over_atr_20"] = range_width / result["atr_14"].where(
        result["atr_14"] > 0
    )
    direction = np.sign(close - open_)
    direction_changes = (direction * direction.shift(1) < 0).astype(float)
    result["direction_change_ratio_20"] = direction_changes.rolling(
        19, min_periods=19
    ).sum() / 19.0
    result["pullback_depth_long_20"] = (result["range_high_20"] - close) / range_width
    result["pullback_depth_short_20"] = (close - result["range_low_20"]) / range_width

    result["volume_ratio_prev20"] = volume / volume.shift(1).rolling(20, min_periods=20).mean().where(
        lambda value: value > 0
    )
    result["volume_ratio"] = volume / volume.shift(1).rolling(20, min_periods=20).median().where(
        lambda value: value > 0
    )
    result["turnover_activity_ratio"] = turnover / turnover.shift(1).rolling(
        20, min_periods=20
    ).median().where(lambda value: value > 0)
    result["atr_pct_30m"] = result["atr_14"] / close.where(close > 0)
    result["realized_vol_pctl_252_30m"] = causal_percentile_rank(result["atr_pct_30m"], 252)

    result["pa_bull_reversal"] = bull_reversal_bar(open_, high, low, close)
    result["pa_bear_reversal"] = bear_reversal_bar(open_, high, low, close)
    result["pa_breakout_up_10"] = breakout_up(high, 10)
    result["pa_breakout_down_10"] = breakout_down(low, 10)
    result["pa_breakout_strength_10"] = breakout_strength(open_, high, low, close, 10)
    result["pa_follow_through_10"] = follow_through(close, open_, high, low, 10)
    result["pa_bo_body_ratio_10"] = breakout_body_ratio(open_, close, high, low, 10)
    result["pa_bo_close_pos_10"] = breakout_close_pos(open_, close, high, low, 10)
    result["pa_trend_strength_20"] = trend_strength_score(open_, high, low, close, 20)
    result["pa_always_in_dir"] = always_in_direction(open_, close, high, low, 20)
    result["pa_ema_slope_20"] = ema_slope(close, 20)
    result["pa_overlap_ratio_10"] = overlap_ratio(high, low, 10)
    result["pa_overlap_ratio_20"] = overlap_ratio(high, low, 20)
    trend_bars = trend_bar_ratio(open_, close, high, low, 20)
    result["pa_trend_bar_net"] = trend_bars["trend_bar_net"]
    result["pa_buy_climax"] = buy_climax(open_, high, low, close, 20)
    result["pa_sell_climax"] = sell_climax(open_, high, low, close, 20)
    result["pa_momentum_decay"] = momentum_decay(close, open_, 10)
    result["pa_barb_wire"] = barb_wire(high, low, open_, close)
    return result.replace([np.inf, -np.inf], np.nan)


def _numeric_column(frame: pd.DataFrame, name: str) -> pd.Series:
    if name not in frame:
        return pd.Series(np.nan, index=frame.index, dtype=float)
    return pd.to_numeric(frame[name], errors="coerce")


def _require_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [column for column in columns if column not in frame]
    if missing:
        raise ValueError(f"missing required columns: {','.join(missing)}")


__all__ = [
    "add_causal_features",
    "causal_percentile_rank",
    "confirmed_swings",
    "wilder_atr",
]
