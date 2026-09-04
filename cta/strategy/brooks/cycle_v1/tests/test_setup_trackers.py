from __future__ import annotations

import pandas as pd
import pytest
from types import SimpleNamespace

from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.core.setup_engine import (
    SetupEngine,
    gate_breakout_failure,
    gate_failed_breakout,
    setup_availability,
)
from cta.strategy.brooks.cycle_v1.core.market_cycle import DirectionalView
from cta.strategy.brooks.cycle_v1.core.trackers.second_entry import (
    SecondEntryPatternTracker,
    SecondEntryState,
)
from cta.strategy.brooks.cycle_v1.core.types import (
    CycleSnapshot,
    EventKey,
    MarketCycle,
    RangeSubtype,
    SetupType,
)


TZ = "Asia/Shanghai"


def _bar(minute: int, *, high: float, low: float, open_: float = 8.0, close: float = 8.0):
    return {
        "bar_end": pd.Timestamp(f"2026-01-05 09:{minute:02d}", tz=TZ),
        "feature_sequence": minute * 10,
        "contract_code": "RB2605.SHF",
        "session_id": "20260105:day",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
    }


def test_h1_h2_counts_price_crossings_independent_of_orders() -> None:
    tracker = SecondEntryPatternTracker(
        vt_symbol="RB0.SHFE",
        direction=1,
        price_tick=1.0,
        max_pullback_bars=20,
    )
    bars = [
        _bar(5, high=10.0, low=8.0, open_=8.5, close=9.5),
        _bar(10, high=9.0, low=7.0, open_=9.0, close=8.0),
        _bar(15, high=8.5, low=6.5, open_=8.0, close=7.0),
        _bar(20, high=10.0, low=7.0, open_=7.5, close=9.5),
        _bar(25, high=9.0, low=6.0, open_=9.0, close=7.0),
        _bar(30, high=10.5, low=7.0, open_=7.5, close=10.0),
    ]

    updates = [tracker.update(bar, context_valid=True) for bar in bars]

    h1 = updates[3].attempt
    h2 = updates[5].attempt
    assert h1 is not None and h1.ordinal == 1 and h1.setup_type is SetupType.H1
    assert h1.signal_bar_end == bars[2]["bar_end"]
    assert h1.crossed_at == bars[3]["bar_end"]
    assert h2 is not None and h2.ordinal == 2 and h2.setup_type is SetupType.H2
    assert h2.signal_bar_end == bars[4]["bar_end"]
    assert tracker.state is SecondEntryState.COMPLETE


def test_untriggered_replacement_observation_remains_h1() -> None:
    tracker = SecondEntryPatternTracker(
        vt_symbol="CU0.SHFE", direction=1, price_tick=0.1, max_pullback_bars=20
    )
    tracker.update(_bar(5, high=10.0, low=8.0), context_valid=True)
    tracker.update(_bar(10, high=9.0, low=7.0), context_valid=True)
    replacement = tracker.update(_bar(15, high=8.0, low=6.0), context_valid=True)

    assert replacement.attempt is None
    assert tracker.attempt_count == 0
    assert tracker.state is SecondEntryState.ATTEMPT_1_ARMED
    assert tracker.raw_trigger == 8.1


def test_tracker_invalidates_on_context_or_contract_change() -> None:
    tracker = SecondEntryPatternTracker(
        vt_symbol="RB0.SHFE", direction=-1, price_tick=1.0, max_pullback_bars=20
    )
    tracker.update(_bar(5, high=10.0, low=8.0), context_valid=True)
    result = tracker.update(_bar(10, high=11.0, low=9.0), context_valid=False)

    assert result.state is SecondEntryState.INVALID
    assert result.reason == "CONTEXT_INVALID"


def test_tracker_rejects_duplicate_or_out_of_order_price_events() -> None:
    tracker = SecondEntryPatternTracker(
        vt_symbol="RB0.SHFE", direction=1, price_tick=1.0, max_pullback_bars=20
    )
    bar = _bar(5, high=10.0, low=8.0)
    tracker.update(bar, context_valid=True)

    with pytest.raises(ValueError, match="strictly increasing"):
        tracker.update(bar, context_valid=True)


def test_failed_breakout_gate_is_symmetric_and_uses_frozen_range() -> None:
    config = load_config()
    cycle = MarketCycle.TRADING_RANGE
    long_row = {
        "cycle": cycle.value,
        "range_subtype": RangeSubtype.BROAD.value,
        "range_low": 100.0,
        "range_high": 120.0,
        "low": 99.5,
        "high": 103.0,
        "open": 100.0,
        "close": 102.0,
        "body": 2.0,
        "body_ratio": 0.57,
        "close_pos_long": 0.71,
        "close_pos_short": 0.29,
        "atr": 4.0,
    }
    short_row = {
        **long_row,
        "low": 117.0,
        "high": 120.5,
        "open": 120.0,
        "close": 118.0,
        "body": -2.0,
        "close_pos_long": 0.29,
        "close_pos_short": 0.71,
    }

    assert gate_failed_breakout(long_row, 1, config)
    assert gate_failed_breakout(short_row, -1, config)


def test_non_v0_setup_returns_explicit_research_only_reason() -> None:
    allowed, reason = setup_availability(SetupType.WEDGE_PULLBACK, load_config())

    assert not allowed
    assert reason == "RESEARCH_ONLY_NOT_ENABLED"


def test_breakout_failure_requires_lost_level_or_opposite_follow_through() -> None:
    config = load_config()
    previous = {
        "open": 110.0,
        "close": 108.0,
        "body": -2.0,
        "body_ratio": 0.4,
        "close_pos_long": 0.3,
        "close_pos_short": 0.7,
    }
    ordinary = {
        **previous,
        "close": 106.0,
        "body": -2.0,
        "body_ratio": 0.4,
        "close_pos_short": 0.7,
        "breakout_level": 100.0,
        "atr": 10.0,
        "cycle": MarketCycle.BULL_TIGHT_CHANNEL.value,
    }
    lost = {**ordinary, "close": 98.0}
    strong_previous = {**previous, "body_ratio": 0.8, "close_pos_short": 0.8}
    strong_current = {**ordinary, "body_ratio": 0.8, "close_pos_short": 0.8}

    assert not gate_breakout_failure(ordinary, previous, 1, config)
    assert gate_breakout_failure(lost, previous, 1, config)
    assert gate_breakout_failure(strong_current, strong_previous, 1, config)


def test_setup_engine_builds_causal_breakout_candidate_with_shared_geometry() -> None:
    config = load_config()
    event = EventKey(pd.Timestamp("2026-01-05 09:05", tz=TZ).to_pydatetime(), 10)
    cycle = CycleSnapshot(
        cycle=MarketCycle.STRONG_BULL_BREAKOUT,
        direction=1,
        strength=0.8,
        confidence=0.8,
        bull_pressure=0.9,
        bear_pressure=0.1,
        range_subtype=None,
        feature_event=event,
        evidence={},
    )
    row = {
        "bar_end": event.timestamp,
        "feature_sequence": event.sequence,
        "open": 105.0,
        "high": 111.0,
        "low": 104.0,
        "close": 110.0,
        "body": 5.0,
        "body_ratio": 5.0 / 7.0,
        "close_pos_long": 6.0 / 7.0,
        "close_pos_short": 1.0 / 7.0,
        "prior_high": 105.0,
        "prior_low": 90.0,
        "breakout_distance_long": 0.5,
        "overlap_ratio_recent": 0.2,
        "latest_confirmed_swing_low": 104.0,
        "atr": 10.0,
        "range_high": 125.0,
        "range_low": 85.0,
        "confirmed_directional_swing_obstacles": {1: (130.0,), -1: (80.0,)},
        "completed_higher_tf_obstacles": {1: (135.0,), -1: (75.0,)},
    }
    view = DirectionalView(
        d=1,
        pressure=0.9,
        breakout_distance=0.5,
        structure_score=0.9,
        close_pos=6.0 / 7.0,
        close_strength=0.9,
        trend_bar_ratio=0.8,
        trend_bar_count_recent=3,
        channel_age=10,
        channel_slope_strength=0.2,
        median_pullback_bars=1.0,
        median_pullback_depth=0.2,
        max_pullback_depth=0.3,
        ema_cross_count=0,
        opposite_trend_bar_ratio=0.1,
        parts={},
    )
    metadata = SimpleNamespace(
        price_tick=1.0,
        contract_size=10.0,
        limit_up=150.0,
        limit_down=70.0,
        stressed_entry_slippage_ticks=1.0,
        stressed_round_trip_fee_cash=5.0,
        stressed_round_trip_slippage_ticks=1.0,
    )

    decision = SetupEngine(config).build_breakout(
        vt_symbol="RB0.SHFE",
        contract_code="RB2605.SHF",
        features=row,
        view=view,
        medium_cycle=cycle,
        large_cycle=cycle,
        metadata=metadata,
        active_event=EventKey(pd.Timestamp("2026-01-05 09:06", tz=TZ).to_pydatetime(), 1),
    )

    assert decision.accepted
    assert decision.candidate is not None
    assert decision.candidate.setup_type is SetupType.BREAKOUT
    assert decision.candidate.known_event < decision.candidate.active_event
    assert decision.geometry is not None


def test_setup_engine_builds_tight_range_breakout_stops_before_breakout() -> None:
    config = load_config()
    event = EventKey(pd.Timestamp("2026-01-05 09:05", tz=TZ).to_pydatetime(), 10)
    cycle = CycleSnapshot(
        cycle=MarketCycle.TRADING_RANGE,
        direction=0,
        strength=0.1,
        confidence=0.8,
        bull_pressure=0.5,
        bear_pressure=0.5,
        range_subtype=RangeSubtype.TIGHT_BREAKOUT_MODE,
        feature_event=event,
        evidence={},
    )
    features = {
        "bar_end": event.timestamp,
        "feature_sequence": event.sequence,
        "close": 110.0,
        "atr": 10.0,
        "range_high": 116.0,
        "range_low": 104.0,
        "range_pct": 0.5,
        "latest_confirmed_swing_low": 111.0,
        "latest_confirmed_swing_high": 109.0,
        "confirmed_directional_swing_obstacles": {1: (130.0,), -1: (90.0,)},
        "completed_higher_tf_obstacles": {1: (135.0,), -1: (85.0,)},
    }
    metadata = SimpleNamespace(
        price_tick=1.0,
        contract_size=10.0,
        limit_up=150.0,
        limit_down=70.0,
        stressed_entry_slippage_ticks=0.0,
        stressed_round_trip_fee_cash=5.0,
        stressed_round_trip_slippage_ticks=1.0,
    )
    active = EventKey(pd.Timestamp("2026-01-05 09:06", tz=TZ).to_pydatetime(), 1)

    long = SetupEngine(config).build_tight_range_breakout(
        vt_symbol="RB0.SHFE",
        contract_code="RB2605.SHF",
        features=features,
        direction=1,
        cycle=cycle,
        metadata=metadata,
        active_event=active,
    )
    short = SetupEngine(config).build_tight_range_breakout(
        vt_symbol="RB0.SHFE",
        contract_code="RB2605.SHF",
        features=features,
        direction=-1,
        cycle=cycle,
        metadata=metadata,
        active_event=active,
    )

    assert long.accepted and short.accepted
    assert long.candidate is not None and short.candidate is not None
    assert long.candidate.trigger_price == 117.0
    assert short.candidate.trigger_price == 103.0
    assert long.candidate.evidence["tight_range_oco"] == 1.0
    assert short.candidate.evidence["tight_range_oco"] == 1.0
