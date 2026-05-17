"""Structure/risk/pressure context features for price action."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.feature.price_action import (
    bar_body_abs,
    bar_body_relative,
    bar_range,
    bar_range_avg,
    close_position,
    is_trend_bar,
)


def space_to_prev_high(close: pd.Series, high: pd.Series, window: int = 20) -> pd.Series:
    prev_max = high.shift(1).rolling(window - 1).max()
    return (prev_max - close) / close.replace(0, np.nan) * 100


def space_to_prev_low(close: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    prev_min = low.shift(1).rolling(window - 1).min()
    return (close - prev_min) / close.replace(0, np.nan) * 100


def range_position_zone(close: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    pos = ((close - ll) / (hh - ll).replace(0, np.nan)).clip(0, 1)
    return pd.Series(np.where(pos < 1 / 3, -1, np.where(pos > 2 / 3, 1, 0)), index=close.index)


def space_ratio_up_down(close: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    hh = high.rolling(window).max()
    ll = low.rolling(window).min()
    up_space = (hh - close).clip(lower=0)
    down_space = (close - ll).clip(lower=0)
    return up_space / down_space.replace(0, np.nan)


def resistance_density(high: pd.Series, close: pd.Series, window: int = 40, band_pct: float = 0.5) -> pd.Series:
    result = pd.Series(0.0, index=close.index)
    upper_band = close * (1 + band_pct / 100)
    for lag in range(1, window + 1):
        shifted_high = high.shift(lag)
        result += ((shifted_high >= close) & (shifted_high <= upper_band)).astype(int)
    return result / window


def support_density(low: pd.Series, close: pd.Series, window: int = 40, band_pct: float = 0.5) -> pd.Series:
    result = pd.Series(0.0, index=close.index)
    lower_band = close * (1 - band_pct / 100)
    for lag in range(1, window + 1):
        shifted_low = low.shift(lag)
        result += ((shifted_low <= close) & (shifted_low >= lower_band)).astype(int)
    return result / window


def signal_bar_risk(high: pd.Series, low: pd.Series, close: pd.Series, atr_window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_window, adjust=False).mean().replace(0, np.nan)
    return (high - low) / atr


def expected_reward(close: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / 14, adjust=False).mean().replace(0, np.nan)
    range_width = high.rolling(window).max() - low.rolling(window).min()
    return range_width / atr


def expected_rr_ratio(close: pd.Series, high: pd.Series, low: pd.Series, atr_window: int = 14, range_window: int = 20) -> pd.Series:
    risk = signal_bar_risk(high, low, close, atr_window)
    reward = expected_reward(close, high, low, range_window)
    return reward / risk.replace(0, np.nan)


def scalp_feasibility(close: pd.Series, high: pd.Series, low: pd.Series, open_: pd.Series, atr_window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_window, adjust=False).mean().replace(0, np.nan)
    risk_ok = ((high - low) / atr < 1.2).astype(float) * 25
    ema_val = close.ewm(span=20, adjust=False).mean()
    trend_clear = (np.sign(ema_val.diff(5)) != 0).astype(float) * 25
    pos = close_position(open_, high, low, close)
    not_middle = ((pos < 0.35) | (pos > 0.65)).astype(float) * 25
    space_up = high.rolling(20).max() - close
    space_down = close - low.rolling(20).min()
    has_space = (pd.concat([space_up, space_down], axis=1).max(axis=1) / atr > 1).astype(float) * 25
    return (risk_ok + trend_clear + not_middle + has_space).clip(0, 100)


def swing_feasibility(close: pd.Series, high: pd.Series, low: pd.Series, open_: pd.Series, atr_window: int = 14) -> pd.Series:
    prev_close = close.shift(1)
    tr = pd.concat([high - low, (high - prev_close).abs(), (low - prev_close).abs()], axis=1).max(axis=1)
    atr = tr.ewm(alpha=1 / atr_window, adjust=False).mean().replace(0, np.nan)
    range_width = high.rolling(40).max() - low.rolling(40).min()
    big_range = (range_width / atr > 3).astype(float) * 25
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema60 = close.ewm(span=60, adjust=False).mean()
    trend = (np.sign(ema20 - ema60) != 0).astype(float) * 25
    rr = expected_rr_ratio(close, high, low, atr_window, 40)
    good_rr = (rr > 2).astype(float) * 25
    hh = high.rolling(40).max()
    ll = low.rolling(40).min()
    pos_in_range = (close - ll) / (hh - ll).replace(0, np.nan)
    at_edge = ((pos_in_range < 0.3) | (pos_in_range > 0.7)).astype(float) * 25
    return (big_range + trend + good_rr + at_edge).clip(0, 100)


def close_position_bias(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 10) -> pd.Series:
    return close_position(open_, high, low, close).rolling(window).mean()


def shadow_pressure(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 10) -> pd.Series:
    upper_body = pd.concat([open_, close], axis=1).max(axis=1)
    lower_body = pd.concat([open_, close], axis=1).min(axis=1)
    upper_shadow = high - upper_body
    lower_shadow = lower_body - low
    upper_sum = upper_shadow.rolling(window).sum().replace(0, np.nan)
    lower_sum = lower_shadow.rolling(window).sum()
    return lower_sum / upper_sum


def buy_climax(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    rng_rel = bar_range(high, low) / bar_range_avg(high, low, window).replace(0, np.nan)
    body_rel = bar_body_relative(open_, close, high, low)
    pos = close_position(open_, high, low, close)
    is_bull = close > open_
    return (is_bull & (rng_rel > 1.5) & (body_rel > 0.6) & (pos > 0.8)).astype(int)


def sell_climax(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    rng_rel = bar_range(high, low) / bar_range_avg(high, low, window).replace(0, np.nan)
    body_rel = bar_body_relative(open_, close, high, low)
    pos = close_position(open_, high, low, close)
    is_bear = close < open_
    return (is_bear & (rng_rel > 1.5) & (body_rel > 0.6) & (pos < 0.2)).astype(int)


def momentum_decay(close: pd.Series, open_: pd.Series, window: int = 10) -> pd.Series:
    body = bar_body_abs(open_, close)
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    x_var = ((x - x_mean) ** 2).sum()

    def _slope(y: np.ndarray) -> float:
        if x_var == 0:
            return 0.0
        y_mean = y.mean()
        return float(np.sum((x - x_mean) * (y - y_mean)) / x_var)

    body_slope = body.rolling(window).apply(_slope, raw=True)
    return -body_slope / body.rolling(window).mean().replace(0, np.nan)


def exhaustion_gap(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series, window: int = 20) -> pd.Series:
    prev_high = high.shift(1)
    prev_low = low.shift(1)
    gap_up = low > prev_high
    bear_close = (close < open_) & (close_position(open_, high, low, close) < 0.4)
    ema = close.ewm(span=window, adjust=False).mean()
    uptrend = close.shift(1) > ema.shift(1)
    gap_down = high < prev_low
    bull_close = (close > open_) & (close_position(open_, high, low, close) > 0.6)
    downtrend = close.shift(1) < ema.shift(1)
    return (gap_up & bear_close & uptrend).astype(int) - (gap_down & bull_close & downtrend).astype(int)


def trend_bar_streak_strength(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    tb = is_trend_bar(open_, close, high, low)
    body = bar_body_abs(open_, close).fillna(0)
    direction = np.sign(close - open_).fillna(0)
    result = pd.Series(0.0, index=close.index)
    for i in range(1, len(tb)):
        body_val = float(body.iloc[i])
        if int(tb.iloc[i]) != 0 and direction.iloc[i] == direction.iloc[i - 1]:
            result.iloc[i] = result.iloc[i - 1] + body_val
        else:
            result.iloc[i] = body_val if int(tb.iloc[i]) != 0 else 0.0
    return result / bar_range_avg(high, low, 20).replace(0, np.nan)


def always_in_direction(open_: pd.Series, close: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    ema = close.ewm(span=window, adjust=False).mean()
    ema_dir = np.sign(ema.diff(3))
    tb = is_trend_bar(open_, close, high, low)
    bull_pct = (tb == 1).astype(int).rolling(window).mean()
    bear_pct = (tb == -1).astype(int).rolling(window).mean()
    pos_bias = close_position(open_, high, low, close).rolling(window).mean() - 0.5
    score = ema_dir * 0.3 + (bull_pct - bear_pct) * 0.3 + pos_bias * 0.4
    return score.clip(-1, 1)


__all__ = [
    "space_to_prev_high",
    "space_to_prev_low",
    "range_position_zone",
    "space_ratio_up_down",
    "resistance_density",
    "support_density",
    "signal_bar_risk",
    "expected_reward",
    "expected_rr_ratio",
    "scalp_feasibility",
    "swing_feasibility",
    "close_position_bias",
    "shadow_pressure",
    "buy_climax",
    "sell_climax",
    "momentum_decay",
    "exhaustion_gap",
    "trend_bar_streak_strength",
    "always_in_direction",
]
