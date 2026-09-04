from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.core import strategy as strategy_module
from cta.strategy.brooks.cycle_v1.core.risk.portfolio import PortfolioRiskSnapshot
from cta.strategy.brooks.cycle_v1.core.strategy import (
    BrooksCycleV1Core,
    StrategyInput,
    run_offline,
)
from cta.strategy.brooks.cycle_v1.core.trackers.second_entry import SecondEntryState
from cta.strategy.brooks.cycle_v1.core.types import (
    CycleSnapshot,
    EventKey,
    MarketCycle,
    RangeSubtype,
)
from cta.strategy.brooks.cycle_v1.online.live_strategy import BrooksCycleV1Online


TZ = "Asia/Shanghai"


def _event(value: str, sequence: int) -> EventKey:
    return EventKey(pd.Timestamp(value, tz=TZ).to_pydatetime(), sequence)


def _cycle(event: EventKey) -> CycleSnapshot:
    return CycleSnapshot(
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


def _strategy_input() -> StrategyInput:
    event = _event("2026-01-05 09:05", 10)
    history = pd.DataFrame(
        {
            "ema_slope": np.zeros(20),
            "ema_separation": np.zeros(20),
            "ema_distance": np.zeros(20),
            "trend_efficiency": np.full(20, 0.2),
            "breakout_distance_long": np.zeros(20),
            "breakout_distance_short": np.zeros(20),
            "body_ratio": np.full(20, 0.2),
            "bull_channel_slope_atr": np.zeros(20),
            "bear_channel_slope_atr": np.zeros(20),
            "bull_median_pullback_bars": np.ones(20),
            "bear_median_pullback_bars": np.ones(20),
            "bull_median_pullback_depth": np.full(20, 0.2),
            "bear_median_pullback_depth": np.full(20, 0.2),
            "bull_max_pullback_depth": np.full(20, 0.2),
            "bear_max_pullback_depth": np.full(20, 0.2),
            "bull_ema_cross_count": np.zeros(20),
            "bear_ema_cross_count": np.zeros(20),
            "bull_opposite_trend_bar_ratio": np.full(20, 0.2),
            "bear_opposite_trend_bar_ratio": np.full(20, 0.2),
            "hh_score": np.full(20, 0.5),
            "hl_score": np.full(20, 0.5),
            "ll_score": np.full(20, 0.5),
            "lh_score": np.full(20, 0.5),
        }
    )
    features = {
        "bar_end": event.timestamp,
        "feature_sequence": event.sequence,
        "contract_code": "RB2605.SHF",
        "session_id": "20260105:day",
        "open": 105.0,
        "high": 111.0,
        "low": 104.0,
        "close": 110.0,
        "body": 5.0,
        "bar_range": 7.0,
        "body_ratio": 5.0 / 7.0,
        "close_pos_long": 6.0 / 7.0,
        "close_pos_short": 1.0 / 7.0,
        "ema_slope": 0.8,
        "ema_separation": 0.8,
        "ema_distance": 1.0,
        "trend_efficiency": 0.85,
        "overlap_ratio": 0.1,
        "overlap_ratio_recent": 0.2,
        "bull_trend_bar_ratio": 0.9,
        "bear_trend_bar_ratio": 0.05,
        "close_pos_long_mean": 0.9,
        "close_pos_short_mean": 0.1,
        "prior_high": 105.0,
        "prior_low": 90.0,
        "breakout_distance_long": 0.5,
        "breakout_distance_short": -2.0,
        "bull_trend_bar_count_recent": 3,
        "bear_trend_bar_count_recent": 0,
        "hh_score": 0.9,
        "hl_score": 0.9,
        "ll_score": 0.1,
        "lh_score": 0.1,
        "bull_channel_age": 10,
        "bear_channel_age": 0,
        "bull_channel_slope_atr": 0.2,
        "bear_channel_slope_atr": -0.2,
        "bull_median_pullback_bars": 1.0,
        "bear_median_pullback_bars": 1.0,
        "bull_median_pullback_depth": 0.2,
        "bear_median_pullback_depth": 0.2,
        "bull_max_pullback_depth": 0.2,
        "bear_max_pullback_depth": 0.2,
        "bull_ema_cross_count": 0,
        "bear_ema_cross_count": 0,
        "bull_opposite_trend_bar_ratio": 0.1,
        "bear_opposite_trend_bar_ratio": 0.1,
        "latest_confirmed_swing_low": 104.0,
        "atr": 10.0,
        "range_high": 125.0,
        "range_low": 85.0,
        "range_mid": 105.0,
        "range_pct": 0.625,
        "cycle": MarketCycle.STRONG_BULL_BREAKOUT.value,
        "range_subtype": "",
        "confirmed_directional_swing_obstacles": {1: (130.0,), -1: (80.0,)},
        "completed_higher_tf_obstacles": {1: (135.0,), -1: (75.0,)},
    }
    metadata = SimpleNamespace(
        price_tick=1.0,
        contract_size=10.0,
        limit_up=150.0,
        limit_down=70.0,
        stressed_entry_slippage_ticks=1.0,
        stressed_round_trip_fee_cash=5.0,
        stressed_round_trip_slippage_ticks=1.0,
        metadata_hash="b" * 64,
        daily=SimpleNamespace(margin_rate_long=0.12, margin_rate_short=0.12),
        capability={"STOP": SimpleNamespace(max_order_size=50)},
        lifecycle=SimpleNamespace(contract_code="RB2605.SHF"),
        instrument=SimpleNamespace(lot_step=1),
        status=SimpleNamespace(max_position=None),
        assembled_at=event.timestamp,
        order_event=_event("2026-01-05 09:06", 1).timestamp,
    )
    portfolio = PortfolioRiskSnapshot(
        equity=200_000.0,
        symbol_open_risk={},
        sector_open_risk={},
        total_open_risk=0.0,
        margin_used_and_reserved=0.0,
        day_start_adjusted_equity=200_000.0,
        adjusted_equity=200_000.0,
        high_watermark_equity=200_000.0,
    )
    return StrategyInput(
        vt_symbol="RB0.SHFE",
        root_symbol="RB",
        sector="BLACK",
        contract_code="RB2605.SHF",
        features=features,
        causal_history=history,
        medium_cycle=_cycle(event),
        large_cycle=_cycle(event),
        metadata=metadata,
        portfolio=portfolio,
        active_event=_event("2026-01-05 09:06", 1),
        expiry_event=_event("2026-01-05 09:20", 1),
    )


def _order_test_config():
    config = load_config()
    return replace(
        config,
        risk=replace(
            config.risk,
            risk_per_trade=0.0025,
            max_trade_risk=0.0050,
        ),
    )


def test_offline_and_online_use_identical_core_decisions() -> None:
    config = _order_test_config()
    offline = run_offline(BrooksCycleV1Core(config), [_strategy_input()])[0]
    online = BrooksCycleV1Online(BrooksCycleV1Core(config)).on_snapshot(
        _strategy_input()
    )

    assert offline.to_audit_dict() == online.to_audit_dict()
    assert len(offline.order_plans) == 1
    assert offline.order_plans[0].quantity > 0


def test_production_portfolio_rejections_are_bound_to_sized_candidate() -> None:
    item = _strategy_input()
    item = replace(
        item,
        portfolio=replace(
            item.portfolio,
            symbol_open_risk={"RB": 3_999.0},
            sector_open_risk={"BLACK": 7_999.0},
        ),
    )
    decision = BrooksCycleV1Core(load_config()).on_snapshot(item)
    candidate = decision.setup_decisions[0].candidate
    assert candidate is not None

    audits = {
        audit.reason_code: audit
        for audit in decision.rejection_audits
        if audit.reason_code in {"MAX_SYMBOL_OPEN_RISK", "MAX_SECTOR_OPEN_RISK"}
    }

    assert set(audits) == {"MAX_SYMBOL_OPEN_RISK", "MAX_SECTOR_OPEN_RISK"}
    assert {audit.candidate_id for audit in audits.values()} == {
        candidate.candidate_id
    }
    assert {audit.risk_budget for audit in audits.values()} == {4_000.0}
    assert all("incremental_open_risk=" in audit.detail for audit in audits.values())


def test_small_setup_bars_reuse_latest_completed_medium_and_large_cycles() -> None:
    item = _strategy_input()
    small_event = item.medium_cycle.feature_event
    medium_event = _event("2026-01-05 09:00", 5)
    large_event = _event("2026-01-05 08:00", 1)
    first = replace(
        item,
        small_cycle=_cycle(small_event),
        medium_cycle=_cycle(medium_event),
        large_cycle=_cycle(large_event),
    )
    second_event = _event("2026-01-05 09:10", 20)
    second = replace(
        first,
        features={
            **first.features,
            "bar_end": second_event.timestamp,
            "feature_sequence": second_event.sequence,
        },
        small_cycle=_cycle(second_event),
        metadata=SimpleNamespace(
            **{
                **vars(first.metadata),
                "assembled_at": second_event.timestamp,
                "order_event": _event("2026-01-05 09:11", 1).timestamp,
            }
        ),
        active_event=_event("2026-01-05 09:11", 1),
        expiry_event=_event("2026-01-05 09:30", 1),
    )
    core = BrooksCycleV1Core(load_config())

    decisions = [core.on_snapshot(first), core.on_snapshot(second)]

    assert [decision.feature_event for decision in decisions] == [
        small_event,
        second_event,
    ]
    assert all(decision.medium_cycle.feature_event == medium_event for decision in decisions)


def test_tight_range_creates_two_opposite_orders_in_one_oco_group() -> None:
    item = _strategy_input()
    range_cycle = CycleSnapshot(
        cycle=MarketCycle.TRADING_RANGE,
        direction=0,
        strength=0.1,
        confidence=0.8,
        bull_pressure=0.5,
        bear_pressure=0.5,
        range_subtype=RangeSubtype.TIGHT_BREAKOUT_MODE,
        feature_event=item.medium_cycle.feature_event,
        evidence={},
    )
    item = replace(
        item,
        features={
            **item.features,
            "close": 110.0,
            "range_high": 116.0,
            "range_low": 104.0,
            "range_mid": 110.0,
            "range_pct": 0.5,
            "latest_confirmed_swing_low": 111.0,
            "latest_confirmed_swing_high": 109.0,
            "confirmed_directional_swing_obstacles": {1: (130.0,), -1: (90.0,)},
            "completed_higher_tf_obstacles": {1: (135.0,), -1: (85.0,)},
        },
        medium_cycle=range_cycle,
        large_cycle=range_cycle,
    )

    decision = BrooksCycleV1Core(_order_test_config()).on_snapshot(item)

    assert len(decision.order_plans) == 2
    assert {plan.candidate.direction for plan in decision.order_plans} == {-1, 1}
    assert len({plan.oco_group_id for plan in decision.order_plans}) == 1
    assert decision.order_plans[0].oco_group_id is not None


def test_strategy_audits_candidates_dropped_by_deduplication(monkeypatch) -> None:
    monkeypatch.setattr(strategy_module, "deduplicate_decisions", lambda decisions: [])

    decision = BrooksCycleV1Core(load_config()).on_snapshot(_strategy_input())

    assert len(decision.setup_decisions) == 1
    candidate = decision.setup_decisions[0].candidate
    assert candidate is not None
    assert f"DEDUPLICATED_CANDIDATE:{candidate.candidate_id}" in decision.rejections


def test_large_timeframe_opposite_direction_blocks_order() -> None:
    item = _strategy_input()
    item = replace(
        item,
        large_cycle=CycleSnapshot(
            cycle=MarketCycle.BEAR_BROAD_CHANNEL,
            direction=-1,
            strength=0.8,
            confidence=0.8,
            bull_pressure=0.1,
            bear_pressure=0.9,
            range_subtype=None,
            feature_event=item.large_cycle.feature_event,
            evidence={},
        ),
    )

    decision = BrooksCycleV1Core(load_config()).on_snapshot(item)

    assert decision.order_plans == ()
    assert "LARGE_DIRECTION_CONFLICT" in decision.rejections


def test_strategy_rejects_incomplete_execution_metadata() -> None:
    item = _strategy_input()
    incomplete = SimpleNamespace(
        price_tick=item.metadata.price_tick,
        contract_size=item.metadata.contract_size,
    )

    with pytest.raises(ValueError, match="execution metadata"):
        BrooksCycleV1Core(load_config()).on_snapshot(
            replace(item, metadata=incomplete)
        )


def test_strategy_rejects_future_large_timeframe_snapshot() -> None:
    item = _strategy_input()
    future_event = _event("2026-01-05 09:10", 10)

    with pytest.raises(ValueError, match="cannot be newer than medium-cycle"):
        BrooksCycleV1Core(load_config()).on_snapshot(
            replace(
                item,
                large_cycle=replace(item.large_cycle, feature_event=future_event),
            )
        )


def test_daily_loss_gate_is_applied_before_position_sizing() -> None:
    item = _strategy_input()
    blocked_portfolio = PortfolioRiskSnapshot(
        equity=193_000.0,
        symbol_open_risk={},
        sector_open_risk={},
        total_open_risk=0.0,
        margin_used_and_reserved=0.0,
        day_start_adjusted_equity=200_000.0,
        adjusted_equity=193_000.0,
        high_watermark_equity=200_000.0,
    )
    item = replace(item, portfolio=blocked_portfolio)

    decision = BrooksCycleV1Core(load_config()).on_snapshot(item)

    assert decision.order_plans == ()
    assert "DAILY_LOSS_SOFT" in decision.rejections


def test_exchange_position_limit_caps_quantity_after_risk_sizing() -> None:
    item = _strategy_input()
    metadata = SimpleNamespace(
        **{
            **vars(item.metadata),
            "status": SimpleNamespace(max_position=2),
            "instrument": SimpleNamespace(lot_step=1),
        }
    )
    item = replace(item, metadata=metadata, current_symbol_position=1)

    decision = BrooksCycleV1Core(load_config()).on_snapshot(item)

    assert len(decision.order_plans) == 1
    assert decision.order_plans[0].quantity == 1


def test_safety_block_prevents_pattern_and_order_generation() -> None:
    item = replace(_strategy_input(), safety_blocks=("BLOCKED_DATA_GAP",))

    decision = BrooksCycleV1Core(load_config()).on_snapshot(item)

    assert decision.setup_decisions == ()
    assert decision.order_plans == ()
    assert decision.rejections == ("BLOCKED_DATA_GAP",)


def test_safety_block_discards_armed_price_pattern_state() -> None:
    core = BrooksCycleV1Core(load_config())
    first = _strategy_input()
    second_event = _event("2026-01-05 09:10", 10)
    second = replace(
        first,
        features={
            **first.features,
            "bar_end": second_event.timestamp,
            "feature_sequence": second_event.sequence,
            "low": 103.0,
        },
        medium_cycle=replace(first.medium_cycle, feature_event=second_event),
        large_cycle=replace(first.large_cycle, feature_event=second_event),
        metadata=SimpleNamespace(
            **{
                **vars(first.metadata),
                "assembled_at": second_event.timestamp,
                "order_event": _event("2026-01-05 09:11", 1).timestamp,
            }
        ),
        active_event=_event("2026-01-05 09:11", 1),
        expiry_event=_event("2026-01-05 09:20", 1),
    )
    blocked_event = _event("2026-01-05 09:15", 10)
    blocked = replace(
        second,
        features={
            **second.features,
            "bar_end": blocked_event.timestamp,
            "feature_sequence": blocked_event.sequence,
        },
        medium_cycle=replace(second.medium_cycle, feature_event=blocked_event),
        large_cycle=replace(second.large_cycle, feature_event=blocked_event),
        metadata=SimpleNamespace(
            **{
                **vars(second.metadata),
                "assembled_at": blocked_event.timestamp,
                "order_event": _event("2026-01-05 09:16", 1).timestamp,
            }
        ),
        active_event=_event("2026-01-05 09:16", 1),
        expiry_event=_event("2026-01-05 09:25", 1),
        safety_blocks=("BLOCKED_DATA_GAP",),
    )

    core.on_snapshot(first)
    core.on_snapshot(second)
    assert core._trackers[(first.vt_symbol, 1)].state is SecondEntryState.ATTEMPT_1_ARMED

    core.on_snapshot(blocked)

    assert not any(key[0] == first.vt_symbol for key in core._trackers)
    assert not any(key[0] == first.vt_symbol for key in core._feature_rows)
