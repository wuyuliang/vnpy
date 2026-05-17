"""Core bar-level price-action primitives."""
from __future__ import annotations

import numpy as np
import pandas as pd


def bar_range(high: pd.Series, low: pd.Series) -> pd.Series:
    return high - low


def bar_body(open_: pd.Series, close: pd.Series) -> pd.Series:
    return close - open_


def bar_body_abs(open_: pd.Series, close: pd.Series) -> pd.Series:
    return (close - open_).abs()


def bar_body_mid(open_: pd.Series, close: pd.Series) -> pd.Series:
    return (open_ + close) / 2


def bar_mid(high: pd.Series, low: pd.Series) -> pd.Series:
    return (high + low) / 2


def bar_range_avg(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    return bar_range(high, low).rolling(window).mean()


def bar_range_relative(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    rng = bar_range(high, low)
    avg = rng.rolling(window).mean().replace(0, np.nan)
    return rng / avg


def bar_body_relative(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    body = bar_body_abs(open_, close)
    rng = bar_range(high, low).replace(0, np.nan)
    return (body / rng).fillna(0.0)


def close_position(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    rng = (high - low).replace(0, np.nan)
    return ((close - low) / rng).fillna(0.5)


def is_trend_bar(
    open_: pd.Series,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    body_threshold: float = 0.5,
) -> pd.Series:
    ratio = bar_body_relative(open_, close, high, low)
    direction = np.sign(close - open_).fillna(0)
    return pd.Series(np.where(ratio > body_threshold, direction, 0), index=open_.index)


def is_doji(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series, threshold: float = 0.25) -> pd.Series:
    ratio = bar_body_relative(open_, close, high, low)
    return (ratio < threshold).astype(int)


def is_shaved_top(
    high: pd.Series,
    close: pd.Series,
    open_: pd.Series,
    low: pd.Series | None = None,
    tolerance: float = 0.05,
) -> pd.Series:
    upper_body = pd.concat([open_, close], axis=1).max(axis=1)
    total_rng = (
        (high - low).replace(0, np.nan)
        if low is not None
        else (high - pd.concat([open_, close], axis=1).min(axis=1)).replace(0, np.nan)
    )
    upper_shadow_pct = (high - upper_body) / total_rng
    return (upper_shadow_pct < tolerance).astype(int)


def is_shaved_bottom(
    low: pd.Series,
    close: pd.Series,
    open_: pd.Series,
    high: pd.Series | None = None,
    tolerance: float = 0.05,
) -> pd.Series:
    lower_body = pd.concat([open_, close], axis=1).min(axis=1)
    total_rng = (
        (high - low).replace(0, np.nan)
        if high is not None
        else (pd.concat([open_, close], axis=1).max(axis=1) - low).replace(0, np.nan)
    )
    lower_shadow_pct = (lower_body - low) / total_rng
    return (lower_shadow_pct < tolerance).astype(int)


def bull_reversal_bar(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    is_bull = close > open_
    pos = close_position(open_, high, low, close)
    lower_body = pd.concat([open_, close], axis=1).min(axis=1)
    lower_shadow = lower_body - low
    body = bar_body_abs(open_, close)
    return (is_bull & (pos > 0.5) & (lower_shadow > body)).astype(int)


def bear_reversal_bar(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    is_bear = close < open_
    pos = close_position(open_, high, low, close)
    upper_body = pd.concat([open_, close], axis=1).max(axis=1)
    upper_shadow = high - upper_body
    body = bar_body_abs(open_, close)
    return (is_bear & (pos < 0.5) & (upper_shadow > body)).astype(int)


def signal_bar_strength(
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    window: int = 20,
) -> pd.Series:
    direction = np.sign(close - open_)
    pos = close_position(open_, high, low, close)
    rng_rel = bar_range_relative(high, low, window)
    body_rel = bar_body_relative(open_, close, high, low)
    upper_body = pd.concat([open_, close], axis=1).max(axis=1)
    lower_body = pd.concat([open_, close], axis=1).min(axis=1)
    upper_shadow = high - upper_body
    lower_shadow = lower_body - low
    rng = bar_range(high, low).replace(0, np.nan)
    shadow_bias = ((lower_shadow - upper_shadow) / rng).fillna(0)
    score = pd.Series(0.0, index=open_.index)
    score += direction
    score += np.where(pos > 0.6, 1, np.where(pos < 0.4, -1, 0))
    score += np.where(rng_rel > 1, direction, 0)
    score += np.where(shadow_bias > 0.2, 1, np.where(shadow_bias < -0.2, -1, 0))
    score += np.where(body_rel > 0.5, direction, 0)
    return score


def consecutive_bull_bars(open_: pd.Series, close: pd.Series) -> pd.Series:
    is_bull = (close > open_).astype(int)
    groups = (is_bull != is_bull.shift()).cumsum()
    return is_bull.groupby(groups).cumsum()


def consecutive_bear_bars(open_: pd.Series, close: pd.Series) -> pd.Series:
    is_bear = (close < open_).astype(int)
    groups = (is_bear != is_bear.shift()).cumsum()
    return is_bear.groupby(groups).cumsum()


def inside_bar(high: pd.Series, low: pd.Series) -> pd.Series:
    return ((high <= high.shift(1)) & (low >= low.shift(1))).astype(int)


def outside_bar(high: pd.Series, low: pd.Series) -> pd.Series:
    return ((high >= high.shift(1)) & (low <= low.shift(1))).astype(int)


def ii_pattern(high: pd.Series, low: pd.Series) -> pd.Series:
    ib = inside_bar(high, low).astype(bool)
    return (ib & ib.shift(1).fillna(False)).astype(int)


def ioi_pattern(high: pd.Series, low: pd.Series) -> pd.Series:
    ib = inside_bar(high, low).astype(bool)
    ob = outside_bar(high, low).astype(bool)
    return (ib & ob.shift(1).fillna(False) & ib.shift(2).fillna(False)).astype(int)


def bar_overlap(high: pd.Series, low: pd.Series) -> pd.Series:
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    overlap_high = pd.concat([high, prev_high], axis=1).min(axis=1)
    overlap_low = pd.concat([low, prev_low], axis=1).max(axis=1)
    overlap = (overlap_high - overlap_low).clip(lower=0)
    total_range = pd.concat([high, prev_high], axis=1).max(axis=1) - pd.concat([low, prev_low], axis=1).min(axis=1)
    return overlap / total_range.replace(0, np.nan)


def bar_overlap_avg(high: pd.Series, low: pd.Series, window: int = 5) -> pd.Series:
    return bar_overlap(high, low).rolling(window).mean()


def gap_bar(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    gap_up = (low > prev_high).astype(int)
    gap_down = -(high < prev_low).astype(int)
    return gap_up + gap_down


def gap_bar_size(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    prev_close = close.shift(1)
    gap_up_size = np.where(low > prev_high, (low - prev_high) / prev_close * 100, 0)
    gap_down_size = np.where(high < prev_low, (high - prev_low) / prev_close * 100, 0)
    return pd.Series(gap_up_size + gap_down_size, index=open_.index)


def breakout_up(high: pd.Series, window: int = 20) -> pd.Series:
    prev_max = high.shift(1).rolling(window).max()
    return (high > prev_max).astype(int)


def breakout_down(low: pd.Series, window: int = 20) -> pd.Series:
    prev_min = low.shift(1).rolling(window).min()
    return (low < prev_min).astype(int)


__all__ = [
    "bar_range",
    "bar_body",
    "bar_body_abs",
    "bar_body_mid",
    "bar_mid",
    "bar_range_avg",
    "bar_range_relative",
    "bar_body_relative",
    "close_position",
    "is_trend_bar",
    "is_doji",
    "is_shaved_top",
    "is_shaved_bottom",
    "bull_reversal_bar",
    "bear_reversal_bar",
    "signal_bar_strength",
    "consecutive_bull_bars",
    "consecutive_bear_bars",
    "inside_bar",
    "outside_bar",
    "ii_pattern",
    "ioi_pattern",
    "bar_overlap",
    "bar_overlap_avg",
    "gap_bar",
    "gap_bar_size",
    "breakout_up",
    "breakout_down",
]
