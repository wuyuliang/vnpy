"""Quality-oriented context features for price action."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.feature.price_action import (
    bar_body_relative,
    bar_overlap,
    breakout_down,
    breakout_up,
    close_position,
    inside_bar,
    is_doji,
    is_trend_bar,
)


def range_over_atr(high: pd.Series, low: pd.Series, close: pd.Series, range_window: int = 20, atr_window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_window, adjust=False).mean().replace(0, np.nan)
    range_width = high.rolling(range_window).max() - low.rolling(range_window).min()
    return range_width / atr


def inside_bar_ratio(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    return inside_bar(high, low).rolling(window).mean()


def overlap_ratio(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    ov = bar_overlap(high, low)
    return (ov > 0.5).astype(int).rolling(window).mean()


def trend_bar_ratio(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.DataFrame:
    tb = is_trend_bar(open_, close, high, low)
    bull_pct = (tb == 1).astype(int).rolling(window).mean()
    bear_pct = (tb == -1).astype(int).rolling(window).mean()
    return pd.DataFrame({"trend_bar_bull_pct": bull_pct, "trend_bar_bear_pct": bear_pct, "trend_bar_net": bull_pct - bear_pct})


def doji_density(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series, window: int = 10) -> pd.Series:
    return is_doji(open_, close, high, low).rolling(window).mean()


def two_legged_pullback(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(0, index=close.index)
    leg_dir = 0
    pullback_legs = 0
    initialized = False
    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        prev_d = int(direction.iloc[i - 1])
        if d == 0:
            continue
        if not initialized:
            leg_dir = d
            initialized = True
            continue
        if d != prev_d and prev_d != 0:
            if d != leg_dir:
                pullback_legs += 1
            else:
                if pullback_legs >= 2:
                    result.iloc[i] = leg_dir
                pullback_legs = 0
        if pullback_legs == 0:
            leg_dir = d
    return result


def ema_slope(close: pd.Series, span: int = 20, slope_window: int = 5) -> pd.Series:
    ema_val = close.ewm(span=span, adjust=False).mean()
    slope = ema_val.diff(slope_window)
    avg_range = close.diff().abs().rolling(20).mean().replace(0, np.nan)
    return slope / avg_range


def ema_slope_acceleration(close: pd.Series, span: int = 20) -> pd.Series:
    return ema_slope(close, span, 5).diff(3)


def prior_leg_slope(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(np.nan, index=close.index)
    leg_start_price = close.iloc[0]
    leg_start_idx = 0
    prev_leg_slope = np.nan
    prev_dir = 0
    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        if d == 0:
            d = prev_dir
        if d != prev_dir:
            if prev_dir != 0:
                leg_bars = i - leg_start_idx
                if leg_bars > 0:
                    prev_leg_slope = (close.iloc[i - 1] - leg_start_price) / leg_bars
            leg_start_price = close.iloc[i]
            leg_start_idx = i
            prev_dir = d
        if prev_dir == 0 and d != 0:
            prev_dir = d
        result.iloc[i] = prev_leg_slope
    avg_range = close.diff().abs().rolling(20).mean().replace(0, np.nan)
    return result / avg_range


def pullback_retrace_ratio(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(np.nan, index=close.index)
    leg_high = high.iloc[0]
    leg_low = low.iloc[0]
    prev_leg_range = np.nan
    prev_dir = 0
    in_pullback = False
    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        if d == 0:
            d = prev_dir
        if d == prev_dir:
            leg_high = max(leg_high, high.iloc[i])
            leg_low = min(leg_low, low.iloc[i])
        else:
            if prev_dir != 0:
                prev_leg_range = leg_high - leg_low
                in_pullback = True
            leg_high = high.iloc[i]
            leg_low = low.iloc[i]
            prev_dir = d
        if in_pullback and prev_leg_range and prev_leg_range > 0:
            result.iloc[i] = (leg_high - leg_low) / prev_leg_range
    return result


def pullback_trend_bar_quality(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series, window: int = 5) -> pd.Series:
    return is_trend_bar(open_, close, high, low).rolling(window).mean()


def pullback_bar_count_ratio(close: pd.Series, open_: pd.Series) -> pd.Series:
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(np.nan, index=close.index)
    prev_leg_len = 0
    curr_leg_len = 0
    prev_dir = 0
    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        if d == 0:
            continue
        if prev_dir == 0:
            prev_dir = d
            curr_leg_len = 1
            continue
        if d == prev_dir:
            curr_leg_len += 1
        else:
            if prev_leg_len > 0:
                result.iloc[i] = curr_leg_len / prev_leg_len
            prev_leg_len = curr_leg_len
            curr_leg_len = 1
            prev_dir = d
    return result


def dist_to_ema(close: pd.Series, span: int = 20) -> pd.Series:
    ema_val = close.ewm(span=span, adjust=False).mean().replace(0, np.nan)
    return (close - ema_val) / ema_val * 100


def pullback_to_ema(close: pd.Series, span: int = 20, threshold: float = 0.3) -> pd.Series:
    return (dist_to_ema(close, span).abs() < threshold).astype(int)


def breakout_body_ratio(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    is_bo = (breakout_up(high, window) | breakout_down(low, window)).astype(bool)
    body_rel = bar_body_relative(open_, close, high, low)
    return pd.Series(np.where(is_bo, body_rel, 0), index=close.index)


def breakout_close_pos(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    is_bo = (breakout_up(high, window) | breakout_down(low, window)).astype(bool)
    pos = close_position(open_, high, low, close)
    return pd.Series(np.where(is_bo, pos, 0.5), index=close.index)


def breakout_volume_ratio(volume: pd.Series, high: pd.Series, low: pd.Series, window: int = 20, vol_window: int = 20) -> pd.Series:
    is_bo = (breakout_up(high, window) | breakout_down(low, window)).astype(bool)
    vol_ma = volume.rolling(vol_window).mean().replace(0, np.nan)
    vol_ratio = volume / vol_ma
    return pd.Series(np.where(is_bo, vol_ratio, 1.0), index=volume.index)


def breakout_follow_consistency(
    close: pd.Series,
    open_: pd.Series,
    high: pd.Series,
    low: pd.Series,
    window: int = 20,
    follow_bars: int = 3,
) -> pd.Series:
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(np.nan, index=close.index)
    for i in range(follow_bars, len(close)):
        if bu.iloc[i - follow_bars] == 1:
            follow_dirs = direction.iloc[i - follow_bars + 1 : i + 1]
            result.iloc[i] = (follow_dirs > 0).sum() / follow_bars
        elif bd.iloc[i - follow_bars] == 1:
            follow_dirs = direction.iloc[i - follow_bars + 1 : i + 1]
            result.iloc[i] = (follow_dirs < 0).sum() / follow_bars
    return result


def pre_breakout_range_bars(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    range_width = (hh - ll).replace(0, np.nan)
    in_range = ((high < hh - 0.2 * range_width) & (low > ll + 0.2 * range_width)).astype(int)
    groups = (in_range != in_range.shift(1)).cumsum()
    return in_range.groupby(groups).cumsum()


def pre_breakout_tightness(high: pd.Series, low: pd.Series, open_: pd.Series, close: pd.Series, window: int = 10) -> pd.Series:
    ib_pct = inside_bar_ratio(high, low, window)
    small_bar = (bar_body_relative(open_, close, high, low) < 0.4).astype(int)
    small_pct = small_bar.rolling(window).mean()
    overlap_pct = overlap_ratio(high, low, window)
    return ((ib_pct + small_pct + overlap_pct) / 3).clip(0, 1)


__all__ = [
    "range_over_atr",
    "inside_bar_ratio",
    "overlap_ratio",
    "trend_bar_ratio",
    "doji_density",
    "two_legged_pullback",
    "ema_slope",
    "ema_slope_acceleration",
    "prior_leg_slope",
    "pullback_retrace_ratio",
    "pullback_trend_bar_quality",
    "pullback_bar_count_ratio",
    "dist_to_ema",
    "pullback_to_ema",
    "breakout_body_ratio",
    "breakout_close_pos",
    "breakout_volume_ratio",
    "breakout_follow_consistency",
    "pre_breakout_range_bars",
    "pre_breakout_tightness",
]
