from __future__ import annotations

from dataclasses import replace
from datetime import date, time

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.instruments.metadata import (
    BlockedMetadataError,
    ContractLifecycleSpec,
    DailyTradingSpec,
    ExecutionCostSpec,
    InstrumentSpec,
    MetadataStore,
    OrderCapabilitySpec,
    TradingStatusSpec,
)
from cta.strategy.brooks.cycle_v1.instruments.sessions import SessionSegment, SessionSpec


TZ = "Asia/Shanghai"


def _ts(value: str):
    return pd.Timestamp(value, tz=TZ).to_pydatetime()


def _store() -> MetadataStore:
    session = SessionSpec(
        session_id="day",
        is_night=False,
        segments=(SessionSegment("morning", time(9), time(11, 30), time(9)),),
    )
    instrument = InstrumentSpec(
        root_symbol="RB",
        exchange="SHFE",
        contract_size=10.0,
        price_tick=1.0,
        lot_step=1,
        sessions=(session,),
        effective_from=date(2020, 1, 1),
        effective_to=None,
        source="SHFE",
        known_at=_ts("2019-12-01 00:00"),
    )
    daily = DailyTradingSpec(
        contract_code="RB2605.SHF",
        exchange_trade_date=date(2026, 1, 5),
        pre_settlement=3500.0,
        limit_up=3850.0,
        limit_down=3150.0,
        margin_rate_long=0.12,
        margin_rate_short=0.12,
        fee_schedule_id="RB_2026",
        effective_from=_ts("2026-01-05 00:00"),
        effective_to=_ts("2026-01-06 00:00"),
        source="SHFE",
        known_at=_ts("2026-01-04 18:00"),
    )
    lifecycle = ContractLifecycleSpec(
        contract_code="RB2605.SHF",
        listed_on=date(2025, 5, 16),
        last_trade_date=date(2026, 5, 15),
        delivery_start=date(2026, 5, 16),
        delivery_end=date(2026, 5, 20),
        client_delivery_eligible=False,
        effective_from=_ts("2025-05-01 00:00"),
        effective_to=None,
        source="SHFE",
        known_at=_ts("2025-05-01 00:00"),
    )
    status = TradingStatusSpec(
        contract_code="RB2605.SHF",
        exchange_trade_date=date(2026, 1, 5),
        tradable=True,
        suspended=False,
        close_only=False,
        max_position=500,
        status_reason="NORMAL",
        effective_from=_ts("2026-01-05 00:00"),
        effective_to=_ts("2026-01-06 00:00"),
        source="SHFE",
        known_at=_ts("2026-01-04 18:00"),
    )
    capabilities = [
        OrderCapabilitySpec(
            exchange="SHFE",
            gateway="CTP",
            root_symbol=None,
            contract_code=None,
            order_type="STOP",
            supported=False,
            max_order_size=None,
            effective_from=_ts("2020-01-01 00:00"),
            effective_to=None,
            source="gateway",
            known_at=_ts("2020-01-01 00:00"),
        ),
        OrderCapabilitySpec(
            exchange="SHFE",
            gateway="CTP",
            root_symbol="RB",
            contract_code=None,
            order_type="STOP",
            supported=True,
            max_order_size=50,
            effective_from=_ts("2020-01-01 00:00"),
            effective_to=None,
            source="gateway",
            known_at=_ts("2020-01-01 00:00"),
        ),
    ]
    cost = ExecutionCostSpec(
        fee_schedule_id="RB_2026",
        contract_code="RB2605.SHF",
        stressed_round_trip_fee_cash=8.0,
        stressed_entry_slippage_ticks=1.0,
        stressed_round_trip_slippage_ticks=2.0,
        effective_from=_ts("2026-01-01 00:00"),
        effective_to=_ts("2027-01-01 00:00"),
        source="broker",
        known_at=_ts("2025-12-31 18:00"),
    )
    return MetadataStore(
        instruments=[instrument],
        daily_specs=[daily],
        lifecycles=[lifecycle],
        capabilities=capabilities,
        statuses=[status],
        costs=[cost],
    )


def test_snapshot_selects_most_specific_known_capability() -> None:
    snapshot = _store().execution_snapshot(
        root_symbol="RB",
        exchange="SHFE",
        contract_code="RB2605.SHF",
        exchange_trade_date=date(2026, 1, 5),
        gateway="CTP",
        order_types=("STOP",),
        decision_asof=_ts("2026-01-05 09:00"),
        order_event=_ts("2026-01-05 09:01"),
    )

    assert snapshot.capability["STOP"].root_symbol == "RB"
    assert snapshot.capability["STOP"].supported
    assert snapshot.price_tick == 1.0
    assert snapshot.stressed_round_trip_fee_cash == 8.0
    assert snapshot.round_trip_cost_price == pytest.approx(2.8)
    assert len(snapshot.metadata_hash) == 64


def test_future_known_metadata_is_not_visible() -> None:
    store = _store()
    store.daily_specs[0] = replace(store.daily_specs[0], known_at=_ts("2026-01-05 10:00"))

    with pytest.raises(BlockedMetadataError) as error:
        store.execution_snapshot(
            root_symbol="RB",
            exchange="SHFE",
            contract_code="RB2605.SHF",
            exchange_trade_date=date(2026, 1, 5),
            gateway="CTP",
            order_types=("STOP",),
            decision_asof=_ts("2026-01-05 09:00"),
            order_event=_ts("2026-01-05 09:01"),
        )

    assert error.value.reason_code == "BLOCKED_METADATA"


def test_same_specificity_overlapping_capabilities_fail_closed() -> None:
    store = _store()
    store.capabilities.append(replace(store.capabilities[-1], source="duplicate"))

    with pytest.raises(BlockedMetadataError) as error:
        store.execution_snapshot(
            root_symbol="RB",
            exchange="SHFE",
            contract_code="RB2605.SHF",
            exchange_trade_date=date(2026, 1, 5),
            gateway="CTP",
            order_types=("STOP",),
            decision_asof=_ts("2026-01-05 09:00"),
            order_event=_ts("2026-01-05 09:01"),
        )

    assert error.value.reason_code == "BLOCKED_ORDER_CAPABILITY"


def test_missing_lifecycle_has_specific_reason() -> None:
    store = _store()
    store.lifecycles.clear()

    with pytest.raises(BlockedMetadataError) as error:
        store.execution_snapshot(
            root_symbol="RB",
            exchange="SHFE",
            contract_code="RB2605.SHF",
            exchange_trade_date=date(2026, 1, 5),
            gateway="CTP",
            order_types=("STOP",),
            decision_asof=_ts("2026-01-05 09:00"),
            order_event=_ts("2026-01-05 09:01"),
        )

    assert error.value.reason_code == "BLOCKED_LIFECYCLE"


def test_coverage_report_is_order_event_specific_and_fail_closed() -> None:
    request = {
        "root_symbol": "RB",
        "exchange": "SHFE",
        "contract_code": "RB2605.SHF",
        "exchange_trade_date": date(2026, 1, 5),
        "gateway": "CTP",
        "order_types": ("STOP",),
        "decision_asof": _ts("2026-01-05 09:00"),
        "order_event": _ts("2026-01-05 09:01"),
    }
    store = _store()
    covered = store.coverage_report([request])
    assert covered["covered"].eq(1).all()
    assert {"instrument", "daily", "lifecycle", "status", "cost", "capability:STOP"}.issubset(
        set(covered["field"])
    )

    store.lifecycles.clear()
    blocked = store.coverage_report([request])
    lifecycle = blocked.loc[blocked["field"].eq("lifecycle")].iloc[0]
    assert lifecycle["covered"] == 0
    assert lifecycle["reason_code"] == "BLOCKED_LIFECYCLE"


def test_coverage_report_lists_all_missing_fields_in_one_pass() -> None:
    store = _store()
    store.lifecycles.clear()
    store.costs.clear()
    request = {
        "root_symbol": "RB",
        "exchange": "SHFE",
        "contract_code": "RB2605.SHF",
        "exchange_trade_date": date(2026, 1, 5),
        "gateway": "CTP",
        "order_types": ("STOP",),
        "decision_asof": _ts("2026-01-05 09:00"),
        "order_event": _ts("2026-01-05 09:01"),
    }

    report = store.coverage_report([request])
    missing = report.loc[report["covered"].eq(0)]

    assert set(missing["field"]) == {"lifecycle", "cost"}
    assert set(missing["reason_code"]) == {"BLOCKED_LIFECYCLE", "BLOCKED_METADATA"}


def test_effective_window_is_checked_at_order_event_not_decision_time() -> None:
    store = _store()
    store.costs[0] = replace(
        store.costs[0],
        effective_from=_ts("2026-01-05 09:03"),
        known_at=_ts("2026-01-05 08:00"),
    )

    snapshot = store.execution_snapshot(
        root_symbol="RB",
        exchange="SHFE",
        contract_code="RB2605.SHF",
        exchange_trade_date=date(2026, 1, 5),
        gateway="CTP",
        order_types=("STOP",),
        decision_asof=_ts("2026-01-05 09:00"),
        order_event=_ts("2026-01-05 09:05"),
    )

    assert snapshot.stressed_round_trip_fee_cash == 8.0


def test_metadata_rejects_order_event_before_decision() -> None:
    with pytest.raises(ValueError, match="order_event"):
        _store().execution_snapshot(
            root_symbol="RB",
            exchange="SHFE",
            contract_code="RB2605.SHF",
            exchange_trade_date=date(2026, 1, 5),
            gateway="CTP",
            order_types=("STOP",),
            decision_asof=_ts("2026-01-05 09:00"),
            order_event=_ts("2026-01-05 08:59"),
        )


@pytest.mark.parametrize("invalid", [float("nan"), float("inf")])
def test_metadata_mechanics_reject_nonfinite_values(invalid: float) -> None:
    session = SessionSpec(
        session_id="day",
        is_night=False,
        segments=(SessionSegment("morning", time(9), time(11, 30), time(9)),),
    )
    with pytest.raises(ValueError, match="finite and positive"):
        InstrumentSpec(
            root_symbol="RB",
            exchange="SHFE",
            contract_size=invalid,
            price_tick=1.0,
            lot_step=1,
            sessions=(session,),
            effective_from=date(2020, 1, 1),
            effective_to=None,
            source="SHFE",
            known_at=_ts("2019-12-01 00:00"),
        )
    with pytest.raises(ValueError, match="finite and nonnegative"):
        ExecutionCostSpec(
            fee_schedule_id="RB_2026",
            contract_code="RB2605.SHF",
            stressed_round_trip_fee_cash=invalid,
            stressed_entry_slippage_ticks=1.0,
            stressed_round_trip_slippage_ticks=2.0,
            effective_from=_ts("2026-01-01 00:00"),
            effective_to=None,
            source="broker",
            known_at=_ts("2025-12-31 18:00"),
        )
