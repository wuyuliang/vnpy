"""Leg/channel/range advanced helpers for price action."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.feature.price_action import (
    bar_body_abs,
    bar_overlap,
    breakout_down,
    breakout_up,
    close_position,
    is_trend_bar,
)


def _compute_atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 14) -> pd.Series:
    prev_c = close.shift(1)
    tr = pd.concat([high - low, (high - prev_c).abs(), (low - prev_c).abs()], axis=1).max(axis=1)
    return tr.ewm(alpha=1 / window, adjust=False).mean()


def _direction(close: pd.Series, open_: pd.Series) -> pd.Series:
    return np.sign(close - open_).fillna(0)


def _leg_id(direction: pd.Series) -> pd.Series:
    d = direction.replace(0, np.nan).ffill().fillna(0)
    return (d != d.shift(1)).cumsum()


def leg_body_consistency(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series, window: int = 10) -> pd.Series:
    body = bar_body_abs(open_, close)
    mean = body.rolling(window, min_periods=2).mean()
    std = body.rolling(window, min_periods=2).std()
    return (std / mean.replace(0, np.nan)).fillna(1.0)


def leg_close_consistency(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 10) -> pd.Series:
    return close_position(open_, high, low, close).rolling(window, min_periods=1).mean()


def leg_gap_count(open_: pd.Series, close: pd.Series, window: int = 10) -> pd.Series:
    prev_close = close.shift(1)
    gap_up = (open_ > prev_close).astype(int)
    gap_down = (open_ < prev_close).astype(int)
    d = _direction(close, open_)
    aligned = np.where(d > 0, gap_up, np.where(d < 0, gap_down, 0))
    return pd.Series(aligned, index=close.index).rolling(window).sum()


def trend_bar_cluster(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    is_tb = (is_trend_bar(open_, close, high, low) != 0).astype(int)

    def _max_consecutive(arr: np.ndarray) -> int:
        max_run = 0
        run = 0
        for v in arr:
            if v == 1:
                run += 1
                max_run = max(max_run, run)
            else:
                run = 0
        return max_run

    return is_tb.rolling(window, min_periods=1).apply(_max_consecutive, raw=True)


def leg_ema_separation(close: pd.Series, high: pd.Series, low: pd.Series, ema_span: int = 20, window: int = 10) -> pd.Series:
    ema_val = close.ewm(span=ema_span, adjust=False).mean()
    atr_val = _compute_atr(high, low, close, 14).replace(0, np.nan)
    return ((close - ema_val).abs() / atr_val).rolling(window, min_periods=1).mean()


def leg_acceleration(open_: pd.Series, close: pd.Series, window: int = 10) -> pd.Series:
    body = bar_body_abs(open_, close)
    half = max(window // 2, 1)
    first_half = body.shift(half).rolling(half, min_periods=1).mean()
    second_half = body.rolling(half, min_periods=1).mean()
    return second_half / first_half.replace(0, np.nan)


def _linear_regression_channel(series: pd.Series, window: int) -> tuple[pd.Series, pd.Series, pd.Series]:
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()
    if x_var == 0:
        return series, series, series

    def _upper_lower(y: np.ndarray) -> tuple[float, float, float]:
        y_mean = float(y.mean())
        slope = float(np.sum((x - x_mean) * (y - y_mean)) / x_var)
        intercept = y_mean - slope * x_mean
        fitted = intercept + slope * x[-1]
        residuals = y - (intercept + slope * x)
        std_r = float(residuals.std())
        return fitted, fitted + 2 * std_r, fitted - 2 * std_r

    mid = series.rolling(window, min_periods=window).apply(lambda y: _upper_lower(y)[0], raw=True)
    upper = series.rolling(window, min_periods=window).apply(lambda y: _upper_lower(y)[1], raw=True)
    lower = series.rolling(window, min_periods=window).apply(lambda y: _upper_lower(y)[2], raw=True)
    return mid, upper, lower


def channel_overshoot(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    _, upper, lower = _linear_regression_channel(close, window)
    up_overshoot = ((high.shift(1) > upper.shift(1)).fillna(False) & (close <= upper).fillna(False)).astype(int)
    down_overshoot = ((low.shift(1) < lower.shift(1)).fillna(False) & (close >= lower).fillna(False)).astype(int)
    return up_overshoot + down_overshoot


def channel_touch_count(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    _, upper, lower = _linear_regression_channel(close, window)
    tolerance = (upper - lower).replace(0, np.nan) * 0.1
    touch_up = (high >= upper - tolerance.fillna(0)).astype(int)
    touch_down = (low <= lower + tolerance.fillna(0)).astype(int)
    return (touch_up + touch_down).rolling(window, min_periods=1).sum()


def channel_age(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    _, upper, lower = _linear_regression_channel(close, window)
    in_channel = ((close <= upper) & (close >= lower)).astype(int).fillna(0)
    groups = (in_channel != in_channel.shift(1)).cumsum()
    return in_channel.groupby(groups).cumsum()


def spike_and_channel(
    open_: pd.Series,
    close: pd.Series,
    high: pd.Series,
    low: pd.Series,
    spike_window: int = 3,
    channel_window: int = 10,
) -> pd.Series:
    body = bar_body_abs(open_, close)
    avg_body = body.rolling(20, min_periods=1).mean().replace(0, np.nan)
    d = _direction(close, open_)
    big_bar = (body > 1.5 * avg_body).astype(int)
    spike_bull = (big_bar * (d > 0).astype(int)).rolling(spike_window).sum()
    spike_bear = (big_bar * (d < 0).astype(int)).rolling(spike_window).sum()
    overlap_avg = bar_overlap(high, low).rolling(channel_window, min_periods=1).mean()
    result = pd.Series(0, index=close.index)
    for i in range(spike_window + channel_window, len(close)):
        spike_start = i - channel_window
        if spike_bull.iloc[spike_start] >= spike_window and overlap_avg.iloc[i] > 0.4:
            result.iloc[i] = 1
        elif spike_bear.iloc[spike_start] >= spike_window and overlap_avg.iloc[i] > 0.4:
            result.iloc[i] = -1
    return result


def range_maturity(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    range_w = (hh - ll).replace(0, np.nan)
    in_range = ((close < hh - 0.2 * range_w) & (close > ll + 0.2 * range_w)).astype(int).fillna(0)
    groups = (in_range != in_range.shift(1)).cumsum()
    return in_range.groupby(groups).cumsum() / window


def range_shrinking(high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    half = max(window // 2, 2)
    first_range = high.shift(half).rolling(half).max() - low.shift(half).rolling(half).min()
    second_range = high.rolling(half).max() - low.rolling(half).min()
    return second_range / first_range.replace(0, np.nan)


def range_false_bo_count(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    prev_hh = high.shift(1).rolling(window - 1, min_periods=1).max()
    prev_ll = low.shift(1).rolling(window - 1, min_periods=1).min()
    fail_up = (bu.shift(1).fillna(0).astype(bool) & (close < prev_hh)).astype(int)
    fail_down = (bd.shift(1).fillna(0).astype(bool) & (close > prev_ll)).astype(int)
    return (fail_up + fail_down).rolling(window * 2, min_periods=1).sum()


def mid_range_bounce(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    mid = (hh + ll) / 2
    range_w = (hh - ll).replace(0, np.nan)
    near_mid = ((close - mid).abs() / range_w) < 0.1
    from_below = close.shift(1) < mid.shift(1)
    from_above = close.shift(1) > mid.shift(1)
    result = pd.Series(0, index=close.index)
    result = result.where(~(near_mid & from_below & (close > close.shift(1))), 1)
    result = result.where(~(near_mid & from_above & (close < close.shift(1))), -1)
    return result


__all__ = [
    "_compute_atr",
    "_direction",
    "_leg_id",
    "leg_body_consistency",
    "leg_close_consistency",
    "leg_gap_count",
    "trend_bar_cluster",
    "leg_ema_separation",
    "leg_acceleration",
    "channel_overshoot",
    "channel_touch_count",
    "channel_age",
    "spike_and_channel",
    "range_maturity",
    "range_shrinking",
    "range_false_bo_count",
    "mid_range_bounce",
]
