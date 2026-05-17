"""Al Brooks price-action feature entrypoint."""
from __future__ import annotations

import pandas as pd

from cta.feature.price_action_bars import (
    bar_body,
    bar_body_abs,
    bar_body_mid,
    bar_body_relative,
    bar_mid,
    bar_overlap,
    bar_overlap_avg,
    bar_range,
    bar_range_avg,
    bar_range_relative,
    bear_reversal_bar,
    breakout_down,
    breakout_up,
    bull_reversal_bar,
    close_position,
    consecutive_bear_bars,
    consecutive_bull_bars,
    gap_bar,
    gap_bar_size,
    ii_pattern,
    inside_bar,
    ioi_pattern,
    is_doji,
    is_shaved_bottom,
    is_shaved_top,
    is_trend_bar,
    outside_bar,
    signal_bar_strength,
)
from cta.feature.price_action_swings import (
    breakout_failure,
    breakout_pullback,
    breakout_strength,
    channel_slope,
    channel_width_change,
    close_to_measured_move,
    current_leg_length,
    current_leg_range,
    dist_to_last_swing_high,
    dist_to_last_swing_low,
    double_bottom_proximity,
    double_top_proximity,
    follow_through,
    higher_high,
    higher_low,
    hh_hl_count,
    leg_ratio,
    lower_high,
    lower_low,
    micro_channel,
    pullback_depth,
    swing_high,
    swing_low,
    tight_channel_score,
    trading_range_score,
    trend_leg_count,
    trend_strength_score,
    wedge_pattern,
    barb_wire,
)


def compute_price_action_features(df: pd.DataFrame) -> pd.DataFrame:
    o = df["open"]
    h = df["high"]
    l = df["low"]   # noqa: E741
    c = df["close"]
    result = pd.DataFrame(index=df.index)

    result["pa_bar_range"] = bar_range(h, l)
    result["pa_bar_body"] = bar_body(o, c)
    result["pa_bar_body_abs"] = bar_body_abs(o, c)
    result["pa_bar_body_mid"] = bar_body_mid(o, c)
    result["pa_bar_mid"] = bar_mid(h, l)
    for w in [3, 10, 20]:
        result[f"pa_bar_range_avg_{w}"] = bar_range_avg(h, l, w)
        result[f"pa_bar_range_rel_{w}"] = bar_range_relative(h, l, w)
    result["pa_body_ratio"] = bar_body_relative(o, c, h, l)
    result["pa_close_position"] = close_position(o, h, l, c)
    result["pa_is_trend_bar"] = is_trend_bar(o, c, h, l)
    result["pa_is_doji"] = is_doji(o, c, h, l)
    result["pa_is_shaved_top"] = is_shaved_top(h, c, o, l)
    result["pa_is_shaved_bottom"] = is_shaved_bottom(l, c, o, h)

    result["pa_bull_reversal"] = bull_reversal_bar(o, h, l, c)
    result["pa_bear_reversal"] = bear_reversal_bar(o, h, l, c)
    for w in [3, 10, 20]:
        result[f"pa_signal_strength_{w}"] = signal_bar_strength(o, h, l, c, w)
    result["pa_consec_bull"] = consecutive_bull_bars(o, c)
    result["pa_consec_bear"] = consecutive_bear_bars(o, c)

    result["pa_inside_bar"] = inside_bar(h, l)
    result["pa_outside_bar"] = outside_bar(h, l)
    result["pa_ii_pattern"] = ii_pattern(h, l)
    result["pa_ioi_pattern"] = ioi_pattern(h, l)
    result["pa_bar_overlap"] = bar_overlap(h, l)
    for w in [5, 10]:
        result[f"pa_bar_overlap_avg_{w}"] = bar_overlap_avg(h, l, w)
    result["pa_gap_bar"] = gap_bar(o, h, l, c)
    result["pa_gap_bar_size"] = gap_bar_size(o, h, l, c)

    result["pa_higher_high"] = higher_high(h)
    result["pa_lower_low"] = lower_low(l)
    result["pa_higher_low"] = higher_low(l)
    result["pa_lower_high"] = lower_high(h)
    for w in [3, 10, 20]:
        hh_hl_df = hh_hl_count(h, l, w)
        result[f"pa_hh_hl_count_{w}"] = hh_hl_df["hh_hl_count"]
        result[f"pa_lh_ll_count_{w}"] = hh_hl_df["lh_ll_count"]
    result["pa_dist_swing_high"] = dist_to_last_swing_high(h, c)
    result["pa_dist_swing_low"] = dist_to_last_swing_low(l, c)
    for w in [3, 10, 20, 60]:
        result[f"pa_pullback_depth_{w}"] = pullback_depth(c, w)
    leg_df = trend_leg_count(c, o, 20)
    result["pa_bull_legs_20"] = leg_df["bull_legs"]
    result["pa_bear_legs_20"] = leg_df["bear_legs"]
    result["pa_leg_length"] = current_leg_length(c, o)
    result["pa_leg_range"] = current_leg_range(c, o, h, l)

    for n in [3, 5]:
        result[f"pa_micro_channel_{n}"] = micro_channel(c, o, n)
    for w in [10, 20]:
        result[f"pa_tight_channel_{w}"] = tight_channel_score(h, l, c, w)
    for w in [10, 20]:
        result[f"pa_trading_range_{w}"] = trading_range_score(h, l, c, w)
    result["pa_barb_wire"] = barb_wire(h, l, o, c)

    for w in [3, 10, 20]:
        result[f"pa_breakout_up_{w}"] = breakout_up(h, w)
        result[f"pa_breakout_down_{w}"] = breakout_down(l, w)
        result[f"pa_breakout_strength_{w}"] = breakout_strength(o, h, l, c, w)
        result[f"pa_follow_through_{w}"] = follow_through(c, o, h, l, w)
        result[f"pa_breakout_fail_{w}"] = breakout_failure(h, l, c, w)
        result[f"pa_breakout_pullback_{w}"] = breakout_pullback(h, l, c, w)

    result["pa_leg_ratio"] = leg_ratio(c, o, h, l, 20)
    result["pa_dist_measured_move"] = close_to_measured_move(h, l, c, 40)

    for w in [20, 60]:
        result[f"pa_double_top_{w}"] = double_top_proximity(h, w)
        result[f"pa_double_bottom_{w}"] = double_bottom_proximity(l, w)
    result["pa_wedge_20"] = wedge_pattern(h, l, 20)
    slopes = channel_slope(h, l, 20)
    result["pa_high_slope_20"] = slopes["high_slope"]
    result["pa_low_slope_20"] = slopes["low_slope"]
    result["pa_channel_width_chg"] = channel_width_change(h, l, 20)

    for w in [3, 10, 20]:
        result[f"pa_trend_strength_{w}"] = trend_strength_score(o, h, l, c, w)
    return result


__all__ = [
    "compute_price_action_features",
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
    "breakout_up",
    "breakout_down",
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
