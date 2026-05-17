"""Al Brooks advanced feature entrypoint."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.feature.price_action import (
    bar_body_relative,
    bear_reversal_bar,
    breakout_down,
    breakout_up,
    bull_reversal_bar,
    close_position,
)
from cta.feature.price_action_legs import (
    _direction,
    channel_age,
    channel_overshoot,
    channel_touch_count,
    leg_acceleration,
    leg_body_consistency,
    leg_close_consistency,
    leg_ema_separation,
    leg_gap_count,
    mid_range_bounce,
    range_false_bo_count,
    range_maturity,
    range_shrinking,
    spike_and_channel,
    trend_bar_cluster,
)


def pullback_type(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    d = _direction(close, open_)
    result = pd.Series(0, index=close.index)
    prev_main_dir = 0
    pullback_leg_count = 0
    in_pullback = False
    for i in range(1, len(d)):
        cur_d = int(d.iloc[i])
        prev_d_val = int(d.iloc[i - 1])
        if cur_d == 0:
            continue
        if prev_main_dir == 0:
            prev_main_dir = cur_d
            continue
        if cur_d != prev_d_val and prev_d_val != 0:
            if cur_d == -prev_main_dir:
                pullback_leg_count = 1 if not in_pullback else pullback_leg_count + 1
                in_pullback = True
            elif cur_d == prev_main_dir and in_pullback:
                result.iloc[i] = min(pullback_leg_count, 3)
                in_pullback = False
                pullback_leg_count = 0
                prev_main_dir = cur_d
        if not in_pullback and cur_d != 0:
            prev_main_dir = cur_d
    return result


def pullback_overlap_with_leg(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    _ = _direction(close, open_)
    pb_depth = (high.rolling(window).max() - close) / (
        high.rolling(window).max() - low.rolling(window).min()
    ).replace(0, np.nan)
    return pb_depth.clip(0, 1)


def pullback_close_vs_entry(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    return close_position(open_, high, low, close)


def first_pullback(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series, window: int = 20) -> pd.Series:
    bu = breakout_up(high, window)
    bd = breakout_down(low, window)
    d = _direction(close, open_)
    result = pd.Series(0, index=close.index)
    last_bo_idx = -window - 1
    last_bo_dir = 0
    had_pullback = False
    for i in range(len(close)):
        if bu.iloc[i] == 1:
            last_bo_idx = i
            last_bo_dir = 1
            had_pullback = False
        elif bd.iloc[i] == 1:
            last_bo_idx = i
            last_bo_dir = -1
            had_pullback = False
        if 0 < (i - last_bo_idx) <= window and not had_pullback:
            cur_d = int(d.iloc[i])
            if cur_d != 0 and cur_d == -last_bo_dir:
                result.iloc[i] = 1
                had_pullback = True
    return result


def high_1_2_3(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    d = _direction(close, open_)
    result = pd.Series(0, index=close.index)
    hcount = 0
    prev_d = 0
    in_pullback = False
    for i in range(1, len(d)):
        cur_d = int(d.iloc[i])
        if cur_d == 0:
            continue
        if cur_d < 0 and prev_d >= 0:
            in_pullback = True
        elif cur_d > 0 and prev_d <= 0 and in_pullback:
            hcount += 1
            result.iloc[i] = min(hcount, 3)
            in_pullback = False
        if cur_d != 0:
            prev_d = cur_d
        if i >= 2 and low.iloc[i] < low.iloc[i - 1] and low.iloc[i - 1] < low.iloc[i - 2]:
            hcount = 0
    return result


def low_1_2_3(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    d = _direction(close, open_)
    result = pd.Series(0, index=close.index)
    lcount = 0
    prev_d = 0
    in_pullback = False
    for i in range(1, len(d)):
        cur_d = int(d.iloc[i])
        if cur_d == 0:
            continue
        if cur_d > 0 and prev_d <= 0:
            in_pullback = True
        elif cur_d < 0 and prev_d >= 0 and in_pullback:
            lcount += 1
            result.iloc[i] = min(lcount, 3)
            in_pullback = False
        if cur_d != 0:
            prev_d = cur_d
        if i >= 2 and high.iloc[i] > high.iloc[i - 1] and high.iloc[i - 1] > high.iloc[i - 2]:
            lcount = 0
    return result


def second_entry(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    bull_sig = bull_reversal_bar(open_, high, low, close)
    bear_sig = bear_reversal_bar(open_, high, low, close)
    result = pd.Series(0, index=close.index)
    last_bull_sig_idx = -100
    last_bear_sig_idx = -100
    for i in range(1, len(close)):
        if bull_sig.iloc[i] == 1:
            if 0 < (i - last_bull_sig_idx) <= 10:
                result.iloc[i] = 1
            last_bull_sig_idx = i
        if bear_sig.iloc[i] == 1:
            if 0 < (i - last_bear_sig_idx) <= 10:
                result.iloc[i] = 1
            last_bear_sig_idx = i
    return result


def failed_signal_reversal(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    bull_sig = bull_reversal_bar(open_, high, low, close)
    bear_sig = bear_reversal_bar(open_, high, low, close)
    d = _direction(close, open_)
    bull_fail = (bull_sig.shift(1).fillna(0).astype(bool) & (d < 0)).astype(int)
    bear_fail = (bear_sig.shift(1).fillna(0).astype(bool) & (d > 0)).astype(int)
    return bull_fail + bear_fail


def trail_stop_level(high: pd.Series, low: pd.Series, close: pd.Series, window: int = 10) -> pd.Series:
    recent_low = low.rolling(window, min_periods=1).min()
    recent_high = high.rolling(window, min_periods=1).max()
    ema = close.ewm(span=20, adjust=False).mean()
    uptrend = close > ema
    stop_dist = np.where(
        uptrend,
        (close - recent_low) / close.replace(0, np.nan) * 100,
        (recent_high - close) / close.replace(0, np.nan) * 100,
    )
    return pd.Series(stop_dist, index=close.index)


def bar_since_signal(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    any_sig = ((bull_reversal_bar(open_, high, low, close) == 1) | (bear_reversal_bar(open_, high, low, close) == 1)).astype(int)
    result = pd.Series(np.nan, index=close.index)
    count = np.nan
    for i in range(len(close)):
        if any_sig.iloc[i] == 1:
            count = 0
        elif not np.isnan(count):
            count += 1
        result.iloc[i] = count
    return result


def htf_trend_alignment(close: pd.Series, open_: pd.Series, high: pd.Series, low: pd.Series) -> pd.Series:
    _ = (open_, high, low)
    ema20 = close.ewm(span=20, adjust=False).mean()
    ema60 = close.ewm(span=60, adjust=False).mean()
    return (np.sign(ema20.diff(5)).fillna(0) == np.sign(ema60.diff(10)).fillna(0)).astype(int)


def ltf_setup_quality(open_: pd.Series, high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    d = _direction(close, open_)
    pos = close_position(open_, high, low, close)
    body_rel = bar_body_relative(open_, close, high, low)
    score = (d * 2 + (pos - 0.5) * 2 + body_rel).rolling(3, min_periods=1).mean()
    return score.clip(-5, 5)


def timeframe_conflict(close: pd.Series) -> pd.Series:
    dir3 = np.sign(close.ewm(span=3, adjust=False).mean().diff(2)).fillna(0)
    dir20 = np.sign(close.ewm(span=20, adjust=False).mean().diff(5)).fillna(0)
    dir60 = np.sign(close.ewm(span=60, adjust=False).mean().diff(10)).fillna(0)
    conflict = (dir3 != dir20).astype(int) + (dir20 != dir60).astype(int) + (dir3 != dir60).astype(int)
    return (conflict / 3 * 2).clip(0, 2)


def compute_price_action_advanced_features(df: pd.DataFrame) -> pd.DataFrame:
    o = df["open"]
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]
    result = pd.DataFrame(index=df.index)

    for w in [5, 10, 20]:
        result[f"pa_leg_body_cv_{w}"] = leg_body_consistency(o, c, h, l, w)
        result[f"pa_leg_close_cons_{w}"] = leg_close_consistency(o, h, l, c, w)
    for w in [10, 20]:
        result[f"pa_leg_gap_count_{w}"] = leg_gap_count(o, c, w)
        result[f"pa_leg_ema_sep_{w}"] = leg_ema_separation(c, h, l, 20, w)
    result["pa_trend_bar_cluster_20"] = trend_bar_cluster(o, c, h, l, 20)
    result["pa_leg_accel_10"] = leg_acceleration(o, c, 10)

    result["pa_pullback_type"] = pullback_type(c, o, h, l)
    result["pa_pb_overlap_20"] = pullback_overlap_with_leg(c, o, h, l, 20)
    result["pa_pb_close_entry"] = pullback_close_vs_entry(o, h, l, c)
    result["pa_first_pullback_20"] = first_pullback(c, o, h, l, 20)
    result["pa_h123"] = high_1_2_3(c, o, h, l)
    result["pa_l123"] = low_1_2_3(c, o, h, l)

    result["pa_channel_overshoot"] = channel_overshoot(h, l, c, 20)
    result["pa_channel_touch_20"] = channel_touch_count(h, l, c, 20)
    result["pa_channel_age_20"] = channel_age(h, l, c, 20)
    result["pa_spike_channel"] = spike_and_channel(o, c, h, l)

    result["pa_range_maturity_20"] = range_maturity(h, l, c, 20)
    result["pa_range_shrinking_20"] = range_shrinking(h, l, 20)
    result["pa_range_false_bo"] = range_false_bo_count(h, l, c, 20)
    result["pa_mid_range_bounce"] = mid_range_bounce(h, l, c, 20)

    result["pa_second_entry"] = second_entry(o, h, l, c)
    result["pa_failed_sig_rev"] = failed_signal_reversal(o, h, l, c)
    result["pa_trail_stop"] = trail_stop_level(h, l, c, 10)
    result["pa_bar_since_sig"] = bar_since_signal(o, h, l, c)

    result["pa_htf_align"] = htf_trend_alignment(c, o, h, l)
    result["pa_ltf_quality"] = ltf_setup_quality(o, h, l, c)
    result["pa_tf_conflict"] = timeframe_conflict(c)
    return result


__all__ = [
    "compute_price_action_advanced_features",
    "pullback_type",
    "pullback_overlap_with_leg",
    "pullback_close_vs_entry",
    "first_pullback",
    "high_1_2_3",
    "low_1_2_3",
    "second_entry",
    "failed_signal_reversal",
    "trail_stop_level",
    "bar_since_signal",
    "htf_trend_alignment",
    "ltf_setup_quality",
    "timeframe_conflict",
]
