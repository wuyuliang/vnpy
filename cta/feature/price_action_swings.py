"""Swing/channel/breakout level price-action helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.feature.price_action_bars import (
    bar_body_abs,
    bar_body_relative,
    bar_overlap_avg,
    bar_range,
    close_position,
    breakout_down,
    breakout_up,
    is_trend_bar,
)


def higher_high(high: pd.Series) -> pd.Series:
    return (high > high.shift(1)).astype(int)


def lower_low(low: pd.Series) -> pd.Series:
    return (low < low.shift(1)).astype(int)


def higher_low(low: pd.Series) -> pd.Series:
    return (low > low.shift(1)).astype(int)


def lower_high(high: pd.Series) -> pd.Series:
    return (high < high.shift(1)).astype(int)


def hh_hl_count(high: pd.Series, low: pd.Series, window: int = 10) -> pd.DataFrame:
    hh = higher_high(high)
    hl = higher_low(low)
    lh = lower_high(high)
    ll = lower_low(low)
    return pd.DataFrame({"hh_hl_count": (hh + hl).rolling(window).sum(), "lh_ll_count": (lh + ll).rolling(window).sum()})


def swing_high(high: pd.Series, left: int = 2, right: int = 2) -> pd.Series:
    result = pd.Series(np.nan, index=high.index)
    for i in range(left, len(high) - right):
        is_swing = True
        for j in range(1, left + 1):
            if high.iloc[i] < high.iloc[i - j]:
                is_swing = False
                break
        if is_swing:
            for j in range(1, right + 1):
                if high.iloc[i] < high.iloc[i + j]:
                    is_swing = False
                    break
        if is_swing:
            result.iloc[i] = high.iloc[i]
    return result


def swing_low(low: pd.Series, left: int = 2, right: int = 2) -> pd.Series:
    result = pd.Series(np.nan, index=low.index)
    for i in range(left, len(low) - right):
        is_swing = True
        for j in range(1, left + 1):
            if low.iloc[i] > low.iloc[i - j]:
                is_swing = False
                break
        if is_swing:
            for j in range(1, right + 1):
                if low.iloc[i] > low.iloc[i + j]:
                    is_swing = False
                    break
        if is_swing:
            result.iloc[i] = low.iloc[i]
    return result


def dist_to_last_swing_high(high: pd.Series, close: pd.Series, left: int = 2, right: int = 2) -> pd.Series:
    sh = swing_high(high, left, right)
    return (close - sh.ffill()) / sh.ffill() * 100


def dist_to_last_swing_low(low: pd.Series, close: pd.Series, left: int = 2, right: int = 2) -> pd.Series:
    sl = swing_low(low, left, right)
    return (close - sl.ffill()) / sl.ffill() * 100


def pullback_depth(close: pd.Series, window: int = 20) -> pd.Series:
    rolling_high = close.rolling(window).max()
    rolling_low = close.rolling(window).min()
    total_range = (rolling_high - rolling_low).replace(0, np.nan)
    return (rolling_high - close) / total_range


def trend_leg_count(close: pd.Series, open_: pd.Series, window: int = 20) -> pd.DataFrame:
    direction = np.sign(close - open_).fillna(0)
    leg_change = (direction != direction.shift(1)).astype(int)
    bull_legs = pd.Series(0, index=close.index)
    bear_legs = pd.Series(0, index=close.index)
    for i in range(1, len(close)):
        if leg_change.iloc[i] == 1:
            if direction.iloc[i] > 0:
                bull_legs.iloc[i] = 1
            elif direction.iloc[i] < 0:
                bear_legs.iloc[i] = 1
    return pd.DataFrame({"bull_legs": bull_legs.rolling(window).sum(), "bear_legs": bear_legs.rolling(window).sum()})


def current_leg_length(close: pd.Series, open_: pd.Series) -> pd.Series:
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(1, index=close.index)
    for i in range(1, len(direction)):
        if direction.iloc[i] != 0 and direction.iloc[i] == direction.iloc[i - 1]:
            result.iloc[i] = result.iloc[i - 1] + 1
        else:
            result.iloc[i] = 1
    return result


def current_leg_range(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(0.0, index=close.index)
    leg_start_high = high.iloc[0]
    leg_start_low = low.iloc[0]
    for i in range(1, len(direction)):
        if direction.iloc[i] != 0 and direction.iloc[i] == direction.iloc[i - 1]:
            if direction.iloc[i] > 0:
                result.iloc[i] = high.iloc[i] - leg_start_low
            else:
                result.iloc[i] = leg_start_high - low.iloc[i]
        else:
            leg_start_high = high.iloc[i]
            leg_start_low = low.iloc[i]
            result.iloc[i] = high.iloc[i] - low.iloc[i]
    return result


def micro_channel(close: pd.Series, open_: pd.Series, min_bars: int = 3) -> pd.Series:
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(0, index=close.index)
    count = 1
    last_dir = 0
    for i in range(1, len(direction)):
        d = int(direction.iloc[i])
        if d == 0:
            pass
        elif d == last_dir:
            count += 1
        elif last_dir == 0:
            last_dir = d
            count = 1
        else:
            last_dir = d
            count = 1
        if count >= min_bars and last_dir != 0:
            result.iloc[i] = last_dir
    return result


def tight_channel_score(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 10) -> pd.Series:
    overlap = bar_overlap_avg(high, low, window)
    rng = bar_range(high, low)
    rng_std = rng.rolling(window).std() / rng.rolling(window).mean()
    tightness = 1 - rng_std.clip(0, 1)
    return (overlap * 0.5 + tightness * 0.5).clip(0, 1)


def trading_range_score(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    mid = (hh + ll) / 2
    range_pct = (hh - ll) / mid
    direction = np.sign(close.diff())
    dir_change = (direction != direction.shift(1)).astype(int)
    alternation = dir_change.rolling(window).mean()
    narrowness = 1 - range_pct.rank(pct=True)
    return (narrowness * 0.5 + alternation * 0.5).clip(0, 1)


def barb_wire(high: pd.Series, low: pd.Series, open_: pd.Series, close: pd.Series, window: int = 5) -> pd.Series:
    is_small = bar_body_relative(open_, close, high, low) < 0.35
    overlap = bar_overlap_avg(high, low, 2) > 0.5
    barb = (is_small & overlap).astype(int)
    return (barb.rolling(window).sum() >= 3).astype(int)


def breakout_strength(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    body_rel = bar_body_relative(open_, close, high, low)
    direction = np.sign(close - open_).fillna(0)
    strength = direction * body_rel
    return pd.Series(np.where(bu | bd, strength, 0), index=open_.index)


def follow_through(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    next_dir = np.sign(close - open_).fillna(0)
    ft = pd.Series(0, index=close.index)
    bu_prev = bu.shift(1).fillna(0)
    bd_prev = bd.shift(1).fillna(0)
    ft = np.where(bu_prev == 1, np.where(next_dir > 0, 1, -1), ft)
    ft = np.where(bd_prev == 1, np.where(next_dir < 0, -1, 1), ft)
    return pd.Series(ft, index=close.index)


def breakout_failure(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    prev_max = high.shift(1).rolling(window).max()
    prev_min = low.shift(1).rolling(window).min()
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    bu_fail = bu.shift(1).fillna(0).astype(bool) & (close < prev_max.shift(1))
    bd_fail = bd.shift(1).fillna(0).astype(bool) & (close > prev_min.shift(1))
    return bu_fail.astype(int) - bd_fail.astype(int)


def breakout_pullback(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20, lookback: int = 5) -> pd.Series:
    prev_max = high.shift(1).rolling(window).max()
    prev_min = low.shift(1).rolling(window).min()
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    result = pd.Series(0, index=close.index)
    for lag in range(2, lookback + 1):
        pullback_to_high = bu.shift(lag).fillna(0).astype(bool) & (low <= prev_max.shift(lag))
        pullback_to_low = bd.shift(lag).fillna(0).astype(bool) & (high >= prev_min.shift(lag))
        result = result + pullback_to_high.astype(int) - pullback_to_low.astype(int)
    return result.clip(-1, 1)


def leg_ratio(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    leg_rng = current_leg_range(close, open_, high, low)
    avg_leg = leg_rng.rolling(window).mean().replace(0, np.nan)
    return leg_rng / avg_leg


def close_to_measured_move(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 40) -> pd.Series:
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    rng = hh - ll
    up_target = hh + rng
    down_target = ll - rng
    dist_up = (up_target - close) / close * 100
    dist_down = (close - down_target) / close * 100
    return pd.concat([dist_up.abs(), dist_down.abs()], axis=1).min(axis=1)


def double_top_proximity(high: pd.Series, window: int = 20, tolerance: float = 0.005) -> pd.Series:
    prev_max = high.shift(1).rolling(window - 1).max()
    diff_pct = (prev_max - high).abs() / prev_max.replace(0, np.nan)
    return (1 - diff_pct / tolerance).clip(0, 1)


def double_bottom_proximity(low: pd.Series, window: int = 20, tolerance: float = 0.005) -> pd.Series:
    prev_min = low.shift(1).rolling(window - 1).min()
    diff_pct = (low - prev_min).abs() / prev_min.replace(0, np.nan)
    return (1 - diff_pct / tolerance).clip(0, 1)


def wedge_pattern(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _slope(y: np.ndarray) -> float:
        y_mean = y.mean()
        return np.sum((x - x_mean) * (y - y_mean)) / x_var

    high_slope = high.rolling(window).apply(_slope, raw=True)
    low_slope = low.rolling(window).apply(_slope, raw=True)
    rising_wedge = (high_slope > 0) & (low_slope > 0) & (high_slope < low_slope)
    falling_wedge = (high_slope < 0) & (low_slope < 0) & (low_slope > high_slope)
    result = pd.Series(0, index=high.index)
    result[rising_wedge] = 1
    result[falling_wedge] = -1
    return result


def channel_slope(high: pd.Series, low: pd.Series, window: int = 20) -> pd.DataFrame:
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _slope(y: np.ndarray) -> float:
        y_mean = y.mean()
        return np.sum((x - x_mean) * (y - y_mean)) / x_var

    return pd.DataFrame({"high_slope": high.rolling(window).apply(_slope, raw=True), "low_slope": low.rolling(window).apply(_slope, raw=True)})


def channel_width_change(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    width = high.rolling(window).max() - low.rolling(window).min()
    return width.pct_change(5)


def trend_strength_score(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    hh_hl = hh_hl_count(high, low, window)
    bull_score = hh_hl["hh_hl_count"]
    bear_score = hh_hl["lh_ll_count"]
    structure = (bull_score - bear_score) / window * 50
    trend_bars = is_trend_bar(open_, close, high, low)
    bull_trend_pct = (trend_bars == 1).astype(int).rolling(window).mean()
    bear_trend_pct = (trend_bars == -1).astype(int).rolling(window).mean()
    trend_score = (bull_trend_pct - bear_trend_pct) * 25
    pos = close_position(open_, high, low, close)
    pos_bias = (pos.rolling(window).mean() - 0.5) * 50
    overlap = bar_overlap_avg(high, low, min(window, 10))
    trend_clarity = (1 - overlap) * 25 * np.sign(structure)
    return (structure + trend_score + pos_bias + trend_clarity).clip(-100, 100)


__all__ = [
    "higher_high",
    "lower_low",
    "higher_low",
    "lower_high",
    "hh_hl_count",
    "swing_high",
    "swing_low",
    "dist_to_last_swing_high",
    "dist_to_last_swing_low",
    "pullback_depth",
    "trend_leg_count",
    "current_leg_length",
    "current_leg_range",
    "micro_channel",
    "tight_channel_score",
    "trading_range_score",
    "barb_wire",
    "breakout_strength",
    "follow_through",
    "breakout_failure",
    "breakout_pullback",
    "leg_ratio",
    "close_to_measured_move",
    "double_top_proximity",
    "double_bottom_proximity",
    "wedge_pattern",
    "channel_slope",
    "channel_width_change",
    "trend_strength_score",
]
