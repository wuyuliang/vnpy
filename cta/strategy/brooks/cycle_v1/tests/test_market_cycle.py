from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.core.always_in import AlwaysInMachine
from cta.strategy.brooks.cycle_v1.core.market_cycle import (
    REQUIRED_STATE_COLUMNS,
    _Hysteresis,
    build_trend_snapshot,
    classify_market_cycles,
)
from cta.strategy.brooks.cycle_v1.core.multitimeframe import assess_direction_permission
from cta.strategy.brooks.cycle_v1.core.trade_mode import select_trade_mode
from cta.strategy.brooks.cycle_v1.core.types import (
    AlwaysIn,
    CycleSnapshot,
    EventKey,
    MarketCycle,
    RangeSubtype,
    SetupType,
    TradeMode,
)


TZ = "Asia/Shanghai"


def _feature_frame() -> pd.DataFrame:
    count = 45
    frame = pd.DataFrame({name: np.full(count, 0.2) for name in REQUIRED_STATE_COLUMNS})
    frame["bar_end"] = pd.date_range("2026-01-05 09:05", periods=count, freq="5min", tz=TZ)
    frame["feature_sequence"] = np.arange(count) * 10
    frame["high"] = 101.0
    frame["low"] = 99.0
    frame["open"] = 100.0
    frame["close"] = 100.2
    frame["body"] = 0.2
    frame["bar_range"] = 2.0
    frame["body_ratio"] = 0.1
    frame["close_pos_long"] = 0.6
    frame["close_pos_short"] = 0.4
    frame["prior_high"] = 105.0
    frame["prior_low"] = 95.0
    frame["range_high"] = 110.0
    frame["range_low"] = 90.0
    frame["range_mid"] = 100.0
    frame["atr"] = 2.0
    frame["range_width_cost_multiple"] = 20.0
    frame["range_width_atr"] = 10.0
    frame["range_aux_gate"] = 1.0
    frame["bar_range_percentile"] = 50.0
    frame["cumulative_move_atr"] = 1.0
    frame["momentum_decay"] = 0.0
    frame["bull_channel_age"] = 10.0
    frame["bear_channel_age"] = 10.0
    frame["bull_median_pullback_bars"] = 1.0
    frame["bear_median_pullback_bars"] = 1.0
    frame["bull_median_pullback_depth"] = 0.2
    frame["bear_median_pullback_depth"] = 0.2
    frame["bull_max_pullback_depth"] = 0.2
    frame["bear_max_pullback_depth"] = 0.2
    frame["bull_ema_cross_count"] = 0.0
    frame["bear_ema_cross_count"] = 0.0
    frame["bull_opposite_trend_bar_ratio"] = 0.2
    frame["bear_opposite_trend_bar_ratio"] = 0.2
    frame["bull_channel_slope_atr"] = 0.0
    frame["bear_channel_slope_atr"] = 0.0
    frame["hh_score"] = frame["hl_score"] = frame["ll_score"] = frame["lh_score"] = 0.5
    return frame


def _make_last_two_strong_bull(frame: pd.DataFrame) -> None:
    for index in frame.index[-2:]:
        frame.loc[index, [
            "open", "high", "low", "close", "body", "bar_range", "body_ratio",
            "close_pos_long", "close_pos_short", "ema_slope", "ema_separation",
            "ema_distance", "trend_efficiency", "bull_trend_bar_ratio",
            "bear_trend_bar_ratio", "close_pos_long_mean", "close_pos_short_mean",
            "overlap_ratio", "overlap_ratio_recent", "breakout_distance_long",
            "breakout_distance_short", "bull_trend_bar_count_recent",
            "bear_trend_bar_count_recent", "hh_score", "hl_score", "ll_score", "lh_score",
        ]] = [
            105.0, 111.0, 104.5, 110.5, 5.5, 6.5, 0.85,
            0.92, 0.08, 0.8, 0.8,
            1.0, 0.85, 0.9,
            0.05, 0.9, 0.1,
            0.10, 0.10, 2.75,
            -7.75, 3.0,
            0.0, 0.9, 0.9, 0.1, 0.1,
        ]


def _make_last_two_strong_bear(frame: pd.DataFrame) -> None:
    for index in frame.index[-2:]:
        frame.loc[index, [
            "open", "high", "low", "close", "body", "bar_range", "body_ratio",
            "close_pos_long", "close_pos_short", "ema_slope", "ema_separation",
            "ema_distance", "trend_efficiency", "bull_trend_bar_ratio",
            "bear_trend_bar_ratio", "close_pos_long_mean", "close_pos_short_mean",
            "overlap_ratio", "overlap_ratio_recent", "breakout_distance_long",
            "breakout_distance_short", "bull_trend_bar_count_recent",
            "bear_trend_bar_count_recent", "hh_score", "hl_score", "ll_score", "lh_score",
        ]] = [
            95.0, 95.5, 89.0, 89.5, -5.5, 6.5, 0.85,
            0.08, 0.92, -0.8, -0.8,
            -1.0, 0.85, 0.05,
            0.9, 0.1, 0.9,
            0.10, 0.10, -7.75,
            2.75, 0.0,
            3.0, 0.1, 0.1, 0.9, 0.9,
        ]


def test_market_cycle_enters_strong_bull_after_confirmed_hysteresis() -> None:
    config = load_config()
    config = replace(
        config,
        features=replace(config.features, percentile_lookback=20),
        cycle=replace(config.cycle, min_state_bars=1),
    )
    frame = _feature_frame()
    _make_last_two_strong_bull(frame)

    result = classify_market_cycles(frame, config)

    assert result.iloc[-1]["cycle"] == MarketCycle.STRONG_BULL_BREAKOUT.value
    assert result.iloc[-1]["direction"] == 1
    assert result.iloc[-1]["bull_pressure"] > result.iloc[-1]["bear_pressure"]


def test_missing_required_state_input_returns_unavailable_not_partial_score() -> None:
    config = load_config()
    config = replace(config, features=replace(config.features, percentile_lookback=20))
    frame = _feature_frame()
    _make_last_two_strong_bull(frame)
    frame.loc[frame.index[-1], "hl_score"] = np.nan

    result = classify_market_cycles(frame, config)

    assert result.iloc[-1]["cycle"] == MarketCycle.UNAVAILABLE.value
    assert result.iloc[-1]["reason"] == "NON_FINITE_REQUIRED_STATE_INPUT"


def test_cycle_waits_for_real_historical_pressure_distribution() -> None:
    config = load_config()
    config = replace(config, features=replace(config.features, percentile_lookback=20))
    frame = _feature_frame()

    result = classify_market_cycles(frame, config)

    assert result.iloc[20]["cycle"] == MarketCycle.UNAVAILABLE.value
    assert result.iloc[20]["reason"] == "INSUFFICIENT_PRESSURE_HISTORY"


def test_market_cycle_state_stream_is_prefix_invariant() -> None:
    config = load_config()
    config = replace(config, features=replace(config.features, percentile_lookback=20))
    frame = _feature_frame()
    _make_last_two_strong_bull(frame)
    full = classify_market_cycles(frame, config)

    for end in (20, 35, 44):
        prefix = classify_market_cycles(frame.iloc[:end], config)
        pd.testing.assert_frame_equal(prefix, full.iloc[:end])


def test_hysteresis_keeps_existing_state_on_one_subthreshold_bar() -> None:
    config = load_config()
    machine = _Hysteresis()
    machine.current = MarketCycle.BULL_TIGHT_CHANNEL
    machine.bars_in_current = 10
    scores = {
        state: 0.1
        for state in (
            MarketCycle.STRONG_BULL_BREAKOUT,
            MarketCycle.BULL_TIGHT_CHANNEL,
            MarketCycle.BULL_BROAD_CHANNEL,
            MarketCycle.TRADING_RANGE,
            MarketCycle.BEAR_BROAD_CHANNEL,
            MarketCycle.BEAR_TIGHT_CHANNEL,
            MarketCycle.STRONG_BEAR_BREAKOUT,
        )
    }

    selected = machine.choose(scores, config, force_transition=False)

    assert selected is MarketCycle.BULL_TIGHT_CHANNEL


def test_strong_breakout_classification_is_bull_bear_mirrored() -> None:
    config = load_config()
    config = replace(
        config,
        features=replace(config.features, percentile_lookback=20),
        cycle=replace(config.cycle, min_state_bars=1),
    )
    frame = _feature_frame()
    _make_last_two_strong_bear(frame)

    result = classify_market_cycles(frame, config)

    assert result.iloc[-1]["cycle"] == MarketCycle.STRONG_BEAR_BREAKOUT.value
    assert result.iloc[-1]["direction"] == -1


def _snapshot(cycle: MarketCycle, direction: int) -> CycleSnapshot:
    return CycleSnapshot(
        cycle=cycle,
        direction=direction,
        strength=0.8,
        confidence=0.8,
        bull_pressure=0.8 if direction >= 0 else 0.1,
        bear_pressure=0.8 if direction <= 0 else 0.1,
        range_subtype=RangeSubtype.BROAD if cycle is MarketCycle.TRADING_RANGE else None,
        feature_event=EventKey(pd.Timestamp("2026-01-05 10:00", tz=TZ).to_pydatetime(), 10),
        evidence={},
    )


def test_always_in_requires_confirmation_and_trade_mode_freezes_context() -> None:
    machine = AlwaysInMachine(confirm_bars=2, pressure_margin=0.10)
    medium = _snapshot(MarketCycle.BULL_TIGHT_CHANNEL, 1)
    large = _snapshot(MarketCycle.BULL_BROAD_CHANNEL, 1)

    assert machine.update(medium, large).state is AlwaysIn.NEUTRAL
    next_medium = replace(
        medium,
        feature_event=EventKey(
            pd.Timestamp("2026-01-05 10:05", tz=TZ).to_pydatetime(), 10
        ),
    )
    assert machine.update(next_medium, large).state is AlwaysIn.LONG
    assert select_trade_mode(medium, range_pct=None) is TradeMode.SWING

    middle = _snapshot(MarketCycle.TRADING_RANGE, 0)
    assert select_trade_mode(middle, range_pct=0.50) is TradeMode.NO_TRADE


def test_always_in_does_not_leave_long_on_one_ordinary_opposite_bar() -> None:
    machine = AlwaysInMachine(confirm_bars=2, pressure_margin=0.10)
    bull = _snapshot(MarketCycle.BULL_TIGHT_CHANNEL, 1)
    bear = _snapshot(MarketCycle.BEAR_BROAD_CHANNEL, -1)
    neutral_large = _snapshot(MarketCycle.TRADING_RANGE, 0)
    machine.update(bull, bull)
    bull_next = replace(
        bull,
        feature_event=EventKey(
            pd.Timestamp("2026-01-05 10:05", tz=TZ).to_pydatetime(), 10
        ),
    )
    assert machine.update(bull_next, bull).state is AlwaysIn.LONG

    bear_first = replace(
        bear,
        feature_event=EventKey(
            pd.Timestamp("2026-01-05 10:10", tz=TZ).to_pydatetime(), 10
        ),
    )
    bear_second = replace(
        bear,
        feature_event=EventKey(
            pd.Timestamp("2026-01-05 10:15", tz=TZ).to_pydatetime(), 10
        ),
    )
    assert machine.update(bear_first, neutral_large).state is AlwaysIn.LONG
    assert machine.update(bear_second, neutral_large).state is AlwaysIn.NEUTRAL


def test_always_in_hard_invalidation_is_immediate_and_audited() -> None:
    machine = AlwaysInMachine(confirm_bars=2, pressure_margin=0.10)
    bull = _snapshot(MarketCycle.BULL_TIGHT_CHANNEL, 1)
    machine.update(bull, bull)
    entered = machine.update(
        replace(
            bull,
            feature_event=EventKey(
                pd.Timestamp("2026-01-05 10:05", tz=TZ).to_pydatetime(), 10
            ),
        ),
        bull,
    )

    invalidated_cycle = replace(
        bull,
        feature_event=EventKey(
            pd.Timestamp("2026-01-05 10:10", tz=TZ).to_pydatetime(), 10
        ),
    )
    invalidated = machine.update(
        invalidated_cycle,
        bull,
        major_structure_broken=True,
    )

    assert entered.state is AlwaysIn.LONG
    assert entered.entered_at == entered.feature_event
    assert invalidated.state is AlwaysIn.NEUTRAL
    assert invalidated.last_changed_at == invalidated_cycle.feature_event
    assert invalidated.entered_at == invalidated_cycle.feature_event


def test_second_entry_requires_confirmed_always_in_direction() -> None:
    medium = _snapshot(MarketCycle.BULL_BROAD_CHANNEL, 1)
    large = _snapshot(MarketCycle.BULL_BROAD_CHANNEL, 1)

    blocked = assess_direction_permission(
        large=large,
        medium=medium,
        always_in=AlwaysIn.NEUTRAL,
        setup_type=SetupType.H2,
        direction=1,
        min_confidence=0.55,
    )
    allowed = assess_direction_permission(
        large=large,
        medium=medium,
        always_in=AlwaysIn.LONG,
        setup_type=SetupType.H2,
        direction=1,
        min_confidence=0.55,
    )

    assert not blocked.allowed
    assert blocked.reason_code == "ALWAYS_IN_NOT_CONFIRMED"
    assert allowed.allowed


def test_trend_snapshot_uses_only_confirmed_causal_structure() -> None:
    cycle = _snapshot(MarketCycle.BULL_BROAD_CHANNEL, 1)

    trend = build_trend_snapshot(
        cycle,
        {
            "latest_confirmed_swing_low": 98.0,
            "latest_confirmed_swing_high": 112.0,
            "range_low": 95.0,
            "range_high": 115.0,
        },
    )

    assert trend.direction == 1
    assert trend.major_support == 98.0
    assert trend.major_resistance == 112.0
    assert trend.feature_event == cycle.feature_event
