from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.core.execution import order_state as order_state_module
from cta.strategy.brooks.cycle_v1.core.execution.order_state import (
    OrderFill,
    OrderStateMachine,
)
from cta.strategy.brooks.cycle_v1.core.execution.planner import (
    build_plan_geometry,
    geometry_tradeable,
    round_for_order,
)
from cta.strategy.brooks.cycle_v1.core.types import (
    EventKey,
    MarketCycle,
    OrderPlan,
    OrderStatus,
    SetupCandidate,
    SetupType,
    TradeMode,
)


TZ = "Asia/Shanghai"


def _ts(value: str):
    return pd.Timestamp(value, tz=TZ).to_pydatetime()


@pytest.mark.parametrize(
    ("side", "kind", "role", "expected"),
    [
        ("BUY", "stop", "entry", 100.5),
        ("SELL", "stop", "entry", 100.0),
        ("SELL", "stop", "protective_stop", 100.0),
        ("BUY", "stop", "protective_stop", 100.5),
        ("SELL", "limit", "profit_target", 100.5),
        ("BUY", "limit", "profit_target", 100.0),
    ],
)
def test_order_rounding_is_adverse_and_symmetric(side, kind, role, expected) -> None:
    assert round_for_order(100.1, side, kind, role, 0.5) == expected


def _meta(**changes):
    values = {
        "price_tick": 1.0,
        "contract_size": 10.0,
        "limit_up": 140.0,
        "limit_down": 60.0,
        "stressed_round_trip_fee_cash": 5.0,
        "stressed_entry_slippage_ticks": 0.5,
        "stressed_round_trip_slippage_ticks": 1.5,
        "metadata_hash": "a" * 64,
    }
    values.update(changes)
    return SimpleNamespace(**values)


def _features():
    return {
        "atr": 10.0,
        "confirmed_directional_swing_obstacles": {1: (120.0,), -1: (80.0,)},
        "range_high": 125.0,
        "range_low": 75.0,
        "completed_higher_tf_obstacles": {1: (130.0,), -1: (70.0,)},
    }


@pytest.mark.parametrize(
    ("direction", "structural_stop", "target", "expected_stop", "expected_target"),
    [(1, 90.0, 118.0, 90.0, 118.0), (-1, 110.0, 82.0, 110.0, 82.0)],
)
def test_geometry_uses_nearest_causal_obstacle_and_real_costs(
    direction, structural_stop, target, expected_stop, expected_target
) -> None:
    geometry = build_plan_geometry(
        _features(),
        direction,
        entry=100.0,
        structural_stop_candidate=structural_stop,
        planned_target=target,
        metadata=_meta(),
        config=load_config(),
    )

    assert geometry.stop == expected_stop
    assert geometry.target == expected_target
    assert geometry.nearest_obstacle == (120.0 if direction == 1 else 80.0)
    assert geometry.cost_R == pytest.approx(0.2)
    assert geometry_tradeable(geometry, load_config())


def test_geometry_rejects_entry_too_near_daily_limit() -> None:
    geometry = build_plan_geometry(
        _features(),
        1,
        entry=100.0,
        structural_stop_candidate=90.0,
        planned_target=101.0,
        metadata=_meta(limit_up=102.0),
        config=load_config(),
    )

    assert geometry.near_price_limit
    assert not geometry_tradeable(geometry, load_config())


def _plan() -> OrderPlan:
    signal = EventKey(_ts("2026-01-05 09:05"), 10)
    candidate = SetupCandidate.create(
        rule_version="cycle_v1",
        vt_symbol="RB0.SHFE",
        contract_code="RB2605.SHF",
        setup_type=SetupType.H2,
        direction=1,
        context_cycle=MarketCycle.BULL_BROAD_CHANNEL,
        signal_event=signal,
        known_event=EventKey(signal.timestamp, 20),
        decision_event=EventKey(signal.timestamp, 30),
        active_event=EventKey(_ts("2026-01-05 09:06"), 1),
        entry_type="STOP",
        trigger_price=100.0,
        initial_stop=90.0,
        target_price=118.0,
        trade_mode=TradeMode.SWING,
        expected_holding_bars=(2, 30),
        evidence={},
        invalidation={},
    )
    geometry = build_plan_geometry(
        _features(), 1, 100.0, 90.0, 118.0, _meta(), load_config()
    )
    return OrderPlan(
        order_id="order-1",
        candidate=candidate,
        geometry=geometry,
        quantity=2,
        status=OrderStatus.PLANNED,
        expires_at=EventKey(_ts("2026-01-05 09:09"), 1),
        metadata_hash="a" * 64,
    )


def test_order_cannot_fill_before_activation_and_tracks_partial_fill() -> None:
    state = OrderStateMachine(_plan())
    active = EventKey(_ts("2026-01-05 09:06"), 1)

    state.activate(active)
    with pytest.raises(ValueError, match="before active"):
        state.fill(EventKey(active.timestamp, 0), quantity=1, price=100.0)
    state.fill(EventKey(active.timestamp, 2), quantity=1, price=100.0)
    assert state.status is OrderStatus.PARTIAL
    state.fill(EventKey(_ts("2026-01-05 09:07"), 1), quantity=1, price=101.0)
    assert state.status is OrderStatus.FILLED
    assert state.filled_quantity == 2


def test_oco_first_partial_fill_cancels_opposite_peer() -> None:
    long_plan = replace(_plan(), order_id="order-long", oco_group_id="oco-range-1")
    short_plan = replace(
        long_plan,
        order_id="order-short",
        candidate=replace(
            long_plan.candidate,
            candidate_id="candidate-short",
            direction=-1,
        ),
        geometry=replace(
            long_plan.geometry,
            entry=99.0,
            stop=109.0,
            target=81.0,
        ),
    )
    coordinator = order_state_module.OcoOrderCoordinator((long_plan, short_plan))
    active = EventKey(_ts("2026-01-05 09:06"), 1)
    fill_event = EventKey(active.timestamp, 2)

    coordinator.activate(active)
    coordinator.fill("order-long", fill_event, quantity=1, price=100.0)

    assert coordinator.orders["order-long"].status is OrderStatus.PARTIAL
    assert coordinator.orders["order-short"].status is OrderStatus.CANCELLED
    assert coordinator.orders["order-short"].cancel_reason == "OCO_PEER_FILLED:order-long"
    with pytest.raises(ValueError, match="only active orders can fill"):
        coordinator.fill("order-short", fill_event, quantity=1, price=99.0)


def test_oco_rejects_unsynchronized_expiry() -> None:
    long_plan = replace(_plan(), order_id="order-long", oco_group_id="oco-range-1")
    short_plan = replace(
        long_plan,
        order_id="order-short",
        candidate=replace(
            long_plan.candidate,
            candidate_id="candidate-short",
            direction=-1,
        ),
        expires_at=EventKey(_ts("2026-01-05 09:10"), 1),
    )

    with pytest.raises(ValueError, match="synchronized activation, expiry, and metadata"):
        order_state_module.OcoOrderCoordinator((long_plan, short_plan))


def test_unfilled_order_expires_at_predeclared_event() -> None:
    state = OrderStateMachine(_plan())
    state.activate(EventKey(_ts("2026-01-05 09:06"), 1))

    state.expire(EventKey(_ts("2026-01-05 09:09"), 1))

    assert state.status is OrderStatus.EXPIRED


@pytest.mark.parametrize("price", [float("nan"), float("inf")])
def test_order_fill_rejects_nonfinite_prices(price: float) -> None:
    event = EventKey(_ts("2026-01-05 09:06"), 1)

    with pytest.raises(ValueError, match="finite and positive"):
        OrderFill(event, 1, price)

    state = OrderStateMachine(_plan())
    state.activate(event)
    with pytest.raises(ValueError, match="finite and positive"):
        state.fill(EventKey(event.timestamp, 2), quantity=1, price=price)
