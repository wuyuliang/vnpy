from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.backtest.engine_adapter import (
    ActualContractLedger,
    ConservativeBarMatcher,
)
from cta.strategy.brooks.cycle_v1.core.execution.order_state import OrderFill
from cta.strategy.brooks.cycle_v1.core.strategy import BrooksCycleV1Core
from cta.strategy.brooks.cycle_v1.tests.test_execution import _features, _meta, _plan
from cta.strategy.brooks.cycle_v1.tests.test_offline_online_parity import (
    _order_test_config,
    _strategy_input,
)
from cta.strategy.brooks.cycle_v1.core.types import EventKey


TZ = "Asia/Shanghai"


def test_entry_never_uses_signal_bar_and_gap_fill_is_adverse() -> None:
    matcher = ConservativeBarMatcher()
    plan = _plan()
    signal_event = plan.candidate.signal_event
    bar = {"open": 99.0, "high": 103.0, "low": 98.0, "close": 102.0}
    metadata = _meta(stressed_entry_slippage_ticks=1.0)

    blocked = matcher.match_entry(
        plan,
        bar,
        signal_event,
        metadata,
        features=_features(),
        config=load_config(),
    )
    fill = matcher.match_entry(
        plan,
        bar,
        EventKey(pd.Timestamp("2026-01-05 09:06", tz=TZ).to_pydatetime(), 2),
        metadata,
        features=_features(),
        config=load_config(),
    )

    assert blocked.fill is None
    assert blocked.reason == "ORDER_NOT_ACTIVE"
    assert fill.fill is not None
    assert fill.fill.price == 101.0
    assert fill.fill.reference_price == 100.0
    assert fill.geometry is not None
    assert fill.geometry.entry == 101.0


def test_breakout_estimated_entry_slippage_is_not_charged_twice() -> None:
    item = _strategy_input()
    decision = BrooksCycleV1Core(_order_test_config()).on_snapshot(item)
    plan = decision.order_plans[0]

    result = ConservativeBarMatcher().match_entry(
        plan,
        {"open": 110.0, "high": 113.0, "low": 109.0, "close": 112.0},
        EventKey(item.active_event.timestamp, item.active_event.sequence + 1),
        item.metadata,
        features=item.features,
        config=load_config(),
    )

    assert plan.geometry.entry == 111.0
    assert result.fill is not None
    assert result.fill.price == 111.0
    assert result.fill.reference_price == 110.0


def test_gap_entry_is_cancelled_when_rebuilt_geometry_loses_space() -> None:
    matcher = ConservativeBarMatcher()
    plan = _plan()

    result = matcher.match_entry(
        plan,
        {"open": 110.0, "high": 112.0, "low": 109.0, "close": 111.0},
        EventKey(pd.Timestamp("2026-01-05 09:06", tz=TZ).to_pydatetime(), 2),
        _meta(stressed_entry_slippage_ticks=1.0),
        features=_features(),
        config=load_config(),
    )

    assert result.fill is None
    assert result.reason.startswith("GAP_REPRICE_")


def test_oco_requires_group_match_and_blocks_same_bar_two_sided_ambiguity() -> None:
    matcher = ConservativeBarMatcher()
    long_plan = replace(_plan(), order_id="order-long", oco_group_id="oco-range-1")
    short_plan = replace(
        long_plan,
        order_id="order-short",
        candidate=replace(
            long_plan.candidate,
            candidate_id="candidate-short",
            direction=-1,
            trigger_price=99.0,
            initial_stop=109.0,
            target_price=81.0,
        ),
        geometry=replace(long_plan.geometry, entry=99.0, stop=109.0, target=81.0),
    )
    event = EventKey(pd.Timestamp("2026-01-05 09:06", tz=TZ).to_pydatetime(), 2)

    direct = matcher.match_entry(
        long_plan,
        {"open": 100.0, "high": 102.0, "low": 99.5, "close": 101.0},
        event,
        _meta(),
        features=_features(),
        config=load_config(),
    )
    one_sided = matcher.match_oco_entry(
        (long_plan, short_plan),
        {"open": 100.0, "high": 102.0, "low": 99.5, "close": 101.0},
        event,
        _meta(),
        features=_features(),
        config=load_config(),
    )
    ambiguous = matcher.match_oco_entry(
        (long_plan, short_plan),
        {"open": 100.0, "high": 102.0, "low": 98.0, "close": 100.0},
        event,
        _meta(),
        features=_features(),
        config=load_config(),
    )

    assert direct.fill is None
    assert direct.reason == "OCO_GROUP_MATCH_REQUIRED"
    assert one_sided.selected_order_id == "order-long"
    assert one_sided.match.fill is not None
    assert one_sided.cancelled_order_ids == ("order-short",)
    assert ambiguous.selected_order_id is None
    assert ambiguous.match.fill is None
    assert ambiguous.match.reason == "OCO_AMBIGUOUS_NO_FILL"


def test_same_bar_stop_and_target_uses_adverse_exit() -> None:
    matcher = ConservativeBarMatcher()
    result = matcher.match_exit(
        direction=1,
        quantity=1,
        stop=90.0,
        target=110.0,
        bar={"open": 100.0, "high": 112.0, "low": 88.0, "close": 105.0},
        event=EventKey(pd.Timestamp("2026-01-05 09:07", tz=TZ).to_pydatetime(), 1),
        metadata=SimpleNamespace(
            price_tick=1.0,
            stressed_entry_slippage_ticks=1.0,
            stressed_round_trip_slippage_ticks=2.0,
            limit_down=80.0,
            limit_up=120.0,
        ),
    )

    assert result.fill is not None
    assert result.reason == "STOP_AMBIGUOUS_ADVERSE"
    assert result.fill.price == 89.0


def test_limit_lock_does_not_assume_protective_exit_fill() -> None:
    matcher = ConservativeBarMatcher()
    result = matcher.match_exit(
        direction=1,
        quantity=1,
        stop=90.0,
        target=110.0,
        bar={"open": 90.0, "high": 90.0, "low": 90.0, "close": 90.0},
        event=EventKey(pd.Timestamp("2026-01-05 09:07", tz=TZ).to_pydatetime(), 1),
        metadata=SimpleNamespace(
            price_tick=1.0,
            stressed_entry_slippage_ticks=1.0,
            stressed_round_trip_slippage_ticks=2.0,
            limit_down=90.0,
            limit_up=120.0,
        ),
    )

    assert result.fill is None
    assert result.reason == "LIMIT_LOCK_NO_COUNTERPARTY"


def test_target_limit_requires_trade_through_and_never_fills_below_limit() -> None:
    matcher = ConservativeBarMatcher()
    metadata = SimpleNamespace(
        price_tick=1.0,
        stressed_entry_slippage_ticks=1.0,
        stressed_round_trip_slippage_ticks=3.0,
        limit_down=80.0,
        limit_up=120.0,
    )
    event = EventKey(pd.Timestamp("2026-01-05 09:07", tz=TZ).to_pydatetime(), 1)

    touched = matcher.match_exit(
        direction=1,
        quantity=1,
        stop=90.0,
        target=110.0,
        bar={"open": 100.0, "high": 110.0, "low": 99.0, "close": 109.0},
        event=event,
        metadata=metadata,
    )
    traded_through = matcher.match_exit(
        direction=1,
        quantity=1,
        stop=90.0,
        target=110.0,
        bar={"open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0},
        event=event,
        metadata=metadata,
    )

    assert touched.fill is None
    assert touched.reason == "EXIT_NOT_TOUCHED"
    assert traded_through.fill is not None
    assert traded_through.fill.price == 110.0
    assert traded_through.fill.reference_price == 110.0


def test_matcher_requires_hash_and_price_limit_metadata() -> None:
    plan = _plan()
    matcher = ConservativeBarMatcher()
    event = EventKey(pd.Timestamp("2026-01-05 09:06", tz=TZ).to_pydatetime(), 2)
    metadata = _meta(metadata_hash="")

    result = matcher.match_entry(
        plan,
        {"open": 99.0, "high": 103.0, "low": 98.0, "close": 102.0},
        event,
        metadata,
        features=_features(),
        config=load_config(),
    )

    assert result.fill is None
    assert result.reason == "BLOCKED_EXECUTION_METADATA"
    malformed = matcher.match_entry(
        plan,
        {"open": 99.0, "high": 103.0, "low": 98.0, "close": 102.0},
        event,
        _meta(price_tick="not-a-number"),
        features=_features(),
        config=load_config(),
    )
    assert malformed.fill is None
    assert malformed.reason == "BLOCKED_EXECUTION_METADATA"
    with pytest.raises(ValueError, match="execution metadata"):
        matcher.match_exit(
            direction=1,
            quantity=1,
            stop=90.0,
            target=110.0,
            bar={"open": 100.0, "high": 111.0, "low": 99.0, "close": 110.0},
            event=event,
            metadata=SimpleNamespace(
                price_tick=1.0,
                stressed_entry_slippage_ticks=1.0,
                stressed_round_trip_slippage_ticks=2.0,
            ),
        )


def test_actual_contract_ledger_reconciles_fees_margin_and_round_trip_pnl() -> None:
    plan = _plan()
    ledger = ActualContractLedger(initial_cash=200_000.0)
    entry = OrderFill(
        EventKey(pd.Timestamp("2026-01-05 09:06", tz=TZ).to_pydatetime(), 2),
        plan.quantity,
        100.0,
    )
    ledger.open(plan, entry, contract_multiplier=10.0, margin_rate=0.10, fee=2.0)
    trade = ledger.close(
        plan.candidate.contract_code,
        OrderFill(
            EventKey(pd.Timestamp("2026-01-05 09:10", tz=TZ).to_pydatetime(), 2),
            plan.quantity,
            110.0,
        ),
        fee=2.0,
    )

    assert trade.contract_code == "RB2605.SHF"
    assert trade.net_pnl == 196.0
    assert trade.to_dict()["fees"] == 4.0
    assert ledger.cash == 200_196.0
    assert ledger.margin_reserved == 0.0


@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_actual_contract_ledger_rejects_nonfinite_cash_and_fees(invalid: float) -> None:
    with pytest.raises(ValueError, match="finite and positive"):
        ActualContractLedger(initial_cash=invalid)

    plan = _plan()
    ledger = ActualContractLedger(initial_cash=200_000.0)
    fill = OrderFill(
        EventKey(pd.Timestamp("2026-01-05 09:06", tz=TZ).to_pydatetime(), 2),
        1,
        100.0,
    )
    with pytest.raises(ValueError, match="finite"):
        ledger.open(
            plan,
            fill,
            contract_multiplier=10.0,
            margin_rate=0.10,
            fee=invalid,
        )


def test_actual_contract_ledger_reconciles_partial_entry_and_exit_fills() -> None:
    plan = _plan()
    ledger = ActualContractLedger(initial_cash=200_000.0)
    ledger.open(
        plan,
        OrderFill(
            EventKey(pd.Timestamp("2026-01-05 09:06", tz=TZ).to_pydatetime(), 2),
            1,
            100.0,
        ),
        contract_multiplier=10.0,
        margin_rate=0.10,
        fee=1.0,
    )
    ledger.open(
        plan,
        OrderFill(
            EventKey(pd.Timestamp("2026-01-05 09:07", tz=TZ).to_pydatetime(), 2),
            1,
            102.0,
        ),
        contract_multiplier=10.0,
        margin_rate=0.10,
        fee=1.0,
    )

    first = ledger.close(
        plan.candidate.contract_code,
        OrderFill(
            EventKey(pd.Timestamp("2026-01-05 09:10", tz=TZ).to_pydatetime(), 2),
            1,
            110.0,
        ),
        fee=1.0,
    )

    assert first.quantity == 1
    assert first.entry_price == 101.0
    assert first.net_pnl == 88.0
    assert ledger.positions[plan.candidate.contract_code].quantity == 1
    assert ledger.margin_reserved == 101.0

    second = ledger.close(
        plan.candidate.contract_code,
        OrderFill(
            EventKey(pd.Timestamp("2026-01-05 09:11", tz=TZ).to_pydatetime(), 2),
            1,
            108.0,
        ),
        fee=1.0,
    )
    assert second.net_pnl == 68.0
    assert plan.candidate.contract_code not in ledger.positions
    assert ledger.cash == 200_156.0
    assert ledger.margin_reserved == 0.0
