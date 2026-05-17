"""Al Brooks context feature entrypoint (quality + structure + pressure)."""
from __future__ import annotations

import pandas as pd

from cta.feature.price_action_quality import (
    breakout_body_ratio,
    breakout_close_pos,
    breakout_follow_consistency,
    breakout_volume_ratio,
    dist_to_ema,
    doji_density,
    ema_slope,
    ema_slope_acceleration,
    inside_bar_ratio,
    overlap_ratio,
    pre_breakout_range_bars,
    pre_breakout_tightness,
    prior_leg_slope,
    pullback_bar_count_ratio,
    pullback_retrace_ratio,
    pullback_to_ema,
    pullback_trend_bar_quality,
    range_over_atr,
    trend_bar_ratio,
    two_legged_pullback,
)
from cta.feature.price_action_structure import (
    always_in_direction,
    buy_climax,
    close_position_bias,
    expected_reward,
    expected_rr_ratio,
    exhaustion_gap,
    momentum_decay,
    range_position_zone,
    resistance_density,
    scalp_feasibility,
    sell_climax,
    shadow_pressure,
    signal_bar_risk,
    space_ratio_up_down,
    space_to_prev_high,
    space_to_prev_low,
    support_density,
    swing_feasibility,
    trend_bar_streak_strength,
)


def compute_price_action_context_features(df: pd.DataFrame) -> pd.DataFrame:
    o = df["open"]
    h = df["high"]
    l = df["low"]  # noqa: E741
    c = df["close"]
    v = df["volume"] if "volume" in df.columns else pd.Series(0, index=df.index)
    result = pd.DataFrame(index=df.index)

    for w in [10, 20]:
        result[f"pa_range_over_atr_{w}"] = range_over_atr(h, l, c, w)
        result[f"pa_inside_bar_ratio_{w}"] = inside_bar_ratio(h, l, w)
        result[f"pa_overlap_ratio_{w}"] = overlap_ratio(h, l, w)
    tb_df = trend_bar_ratio(o, c, h, l, 20)
    result["pa_trend_bar_bull_pct"] = tb_df["trend_bar_bull_pct"]
    result["pa_trend_bar_bear_pct"] = tb_df["trend_bar_bear_pct"]
    result["pa_trend_bar_net"] = tb_df["trend_bar_net"]
    for w in [5, 10]:
        result[f"pa_doji_density_{w}"] = doji_density(o, c, h, l, w)
    result["pa_two_leg_pullback"] = two_legged_pullback(c, o, h, l)
    for span in [20, 60]:
        result[f"pa_ema_slope_{span}"] = ema_slope(c, span)
        result[f"pa_dist_to_ema_{span}"] = dist_to_ema(c, span)
    result["pa_ema_slope_accel"] = ema_slope_acceleration(c, 20)

    result["pa_prior_leg_slope"] = prior_leg_slope(c, o, h, l)
    result["pa_pullback_retrace"] = pullback_retrace_ratio(c, o, h, l)
    for w in [3, 5]:
        result[f"pa_pullback_tb_quality_{w}"] = pullback_trend_bar_quality(o, c, h, l, w)
    result["pa_pullback_bar_ratio"] = pullback_bar_count_ratio(c, o)
    result["pa_pullback_to_ema_20"] = pullback_to_ema(c, 20)

    for w in [10, 20]:
        result[f"pa_bo_body_ratio_{w}"] = breakout_body_ratio(o, c, h, l, w)
        result[f"pa_bo_close_pos_{w}"] = breakout_close_pos(o, c, h, l, w)
    result["pa_bo_vol_ratio_20"] = breakout_volume_ratio(v, h, l, 20)
    result["pa_bo_follow_consistency"] = breakout_follow_consistency(c, o, h, l, 20, 3)
    result["pa_pre_bo_bars"] = pre_breakout_range_bars(h, l, 20)
    for w in [5, 10]:
        result[f"pa_pre_bo_tightness_{w}"] = pre_breakout_tightness(h, l, o, c, w)

    for w in [20, 60]:
        result[f"pa_space_to_high_{w}"] = space_to_prev_high(c, h, w)
        result[f"pa_space_to_low_{w}"] = space_to_prev_low(c, l, w)
    result["pa_range_zone_20"] = range_position_zone(c, h, l, 20)
    result["pa_space_ratio_ud"] = space_ratio_up_down(c, h, l, 20)
    result["pa_resistance_density"] = resistance_density(h, c, 40)
    result["pa_support_density"] = support_density(l, c, 40)

    result["pa_signal_risk"] = signal_bar_risk(h, l, c)
    result["pa_expected_reward"] = expected_reward(c, h, l, 20)
    result["pa_expected_rr"] = expected_rr_ratio(c, h, l, 14, 20)
    result["pa_scalp_score"] = scalp_feasibility(c, h, l, o)
    result["pa_swing_score"] = swing_feasibility(c, h, l, o)

    for w in [5, 10, 20]:
        result[f"pa_close_pos_bias_{w}"] = close_position_bias(o, h, l, c, w)
    for w in [5, 10]:
        result[f"pa_shadow_pressure_{w}"] = shadow_pressure(o, h, l, c, w)
    result["pa_buy_climax"] = buy_climax(o, h, l, c)
    result["pa_sell_climax"] = sell_climax(o, h, l, c)
    result["pa_momentum_decay"] = momentum_decay(c, o)
    result["pa_exhaustion_gap"] = exhaustion_gap(o, h, l, c)
    result["pa_tb_streak_strength"] = trend_bar_streak_strength(o, c, h, l)
    result["pa_always_in_dir"] = always_in_direction(o, c, h, l)
    return result


__all__ = [
    "compute_price_action_context_features",
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
