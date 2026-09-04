from __future__ import annotations

import pandas as pd

from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.core.risk.trade_management import (
    ExitAction,
    TradeManager,
)
from cta.strategy.brooks.cycle_v1.core.types import EventKey, TradeMode


TZ = "Asia/Shanghai"


def _event(value: str, sequence: int = 1) -> EventKey:
    return EventKey(pd.Timestamp(value, tz=TZ).to_pydatetime(), sequence)


def test_trade_mode_is_frozen_and_pivot_trail_waits_until_known_at() -> None:
    manager = TradeManager(
        direction=1,
        entry=100.0,
        initial_stop=90.0,
        target=120.0,
        quantity=4,
        mode=TradeMode.SWING,
        entry_event=_event("2026-01-05 09:00"),
        config=load_config(),
        partial_fraction=0.5,
    )

    losing = manager.update(
        event=_event("2026-01-05 09:05"),
        high=101.0,
        low=95.0,
        close=96.0,
        proposed_structural_stop=94.0,
        proposed_stop_known_at=_event("2026-01-05 09:10"),
        remaining_space_R=2.0,
        favorable_structure_confirmed=True,
    )
    assert losing.action is ExitAction.HOLD
    assert manager.mode is TradeMode.SWING
    assert manager.current_stop == 90.0

    confirmed = manager.update(
        event=_event("2026-01-05 09:10"),
        high=110.0,
        low=96.0,
        close=108.0,
        proposed_structural_stop=94.0,
        proposed_stop_known_at=_event("2026-01-05 09:10"),
        remaining_space_R=2.0,
        favorable_structure_confirmed=True,
    )
    assert confirmed.action is ExitAction.HOLD
    assert manager.current_stop == 94.0


def test_swing_partial_target_is_predeclared_and_safety_block_never_assumes_fill() -> None:
    manager = TradeManager(
        direction=1,
        entry=100.0,
        initial_stop=90.0,
        target=120.0,
        quantity=4,
        mode=TradeMode.SWING,
        entry_event=_event("2026-01-05 09:00"),
        config=load_config(),
        partial_fraction=0.5,
    )

    partial = manager.update(
        event=_event("2026-01-05 09:05"),
        high=121.0,
        low=100.0,
        close=119.0,
        remaining_space_R=2.0,
        favorable_structure_confirmed=True,
    )
    assert partial.action is ExitAction.PARTIAL_TARGET
    assert partial.quantity == 2
    assert manager.remaining_quantity == 4

    manager.apply_exit_fill(
        event=_event("2026-01-05 09:05", 2),
        quantity=1,
    )
    assert manager.remaining_quantity == 3
    manager.apply_exit_fill(
        event=_event("2026-01-05 09:05", 3),
        quantity=1,
    )
    assert manager.remaining_quantity == 2

    runner = manager.update(
        event=_event("2026-01-05 09:10"),
        high=125.0,
        low=115.0,
        close=123.0,
        remaining_space_R=2.0,
        favorable_structure_confirmed=True,
        proposed_structural_stop=110.0,
        proposed_stop_known_at=_event("2026-01-05 09:10"),
    )
    assert runner.action is ExitAction.HOLD
    assert manager.current_stop == 110.0

    blocked = manager.update(
        event=_event("2026-01-05 09:15"),
        high=118.0,
        low=80.0,
        close=90.0,
        safety_block="LIMIT_LOCK",
        remaining_space_R=0.0,
        favorable_structure_confirmed=False,
    )
    assert blocked.action is ExitAction.RISK_REDUCTION_PENDING
    assert blocked.quantity == 0
    assert manager.remaining_quantity == 2


def test_unfilled_target_intent_never_reduces_position() -> None:
    manager = TradeManager(
        direction=1,
        entry=100.0,
        initial_stop=90.0,
        target=120.0,
        quantity=4,
        mode=TradeMode.SWING,
        entry_event=_event("2026-01-05 09:00"),
        config=load_config(),
        partial_fraction=0.5,
    )

    intent = manager.update(
        event=_event("2026-01-05 09:05"),
        high=121.0,
        low=100.0,
        close=119.0,
        remaining_space_R=2.0,
        favorable_structure_confirmed=True,
    )
    pending = manager.update(
        event=_event("2026-01-05 09:10"),
        high=122.0,
        low=115.0,
        close=121.0,
        remaining_space_R=2.0,
        favorable_structure_confirmed=True,
    )

    assert intent.action is ExitAction.PARTIAL_TARGET
    assert pending.action is ExitAction.EXIT_ORDER_PENDING
    assert manager.remaining_quantity == 4

    manager.cancel_exit_order(
        event=_event("2026-01-05 09:10", 2),
        reason="LIMIT_NOT_FILLED",
    )
    retried = manager.update(
        event=_event("2026-01-05 09:15"),
        high=123.0,
        low=116.0,
        close=122.0,
        remaining_space_R=2.0,
        favorable_structure_confirmed=True,
    )
    assert retried.action is ExitAction.PARTIAL_TARGET
    assert manager.remaining_quantity == 4
