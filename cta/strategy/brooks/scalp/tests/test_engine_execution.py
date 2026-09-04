from __future__ import annotations

import json
from dataclasses import replace
from datetime import date, datetime, time, timedelta

import pandas as pd
import pytest

from cta.strategy.brooks.scalp.data import MinuteBar
from cta.strategy.brooks.scalp.engine import (
    BrokerOrderEvent,
    BrokerTradeEvent,
    ExitReason,
    Fill,
    Offset,
    OrderAction,
    OrderStatus,
    OrderType,
    ScalpEngine,
    calculate_fee,
)
from cta.strategy.brooks.scalp.metadata import FeeMarginSpec, InstrumentSpec
from cta.strategy.brooks.scalp.regime import RegimeState
from cta.strategy.brooks.scalp.session import SessionSegment, SessionSpec
from cta.strategy.brooks.scalp.setups import SetupCandidate


TZ = "Asia/Shanghai"
CONTRACT = "RB2605.SHF"
SYMBOL = "RB0.SHFE"


def _instrument(*, tick: float = 1.0, slippage_ticks: float = 1.0) -> InstrumentSpec:
    return InstrumentSpec(
        root_symbol="RB",
        exchange="SHFE",
        contract_size=10.0,
        price_tick=tick,
        lot_step=1,
        slippage_ticks_base=slippage_ticks,
        sessions=(
            SessionSpec(
                session_id="day",
                is_night=False,
                segments=(
                    SessionSegment(
                        segment_id="day",
                        start=time(9, 0),
                        end=time(15, 0),
                        bucket_anchor=time(9, 0),
                    ),
                ),
            ),
        ),
        effective_from=date(2020, 1, 1),
        effective_to=None,
    )


def _fees(
    *,
    open_rate: float = 0.0,
    close_rate: float = 0.0,
    close_today_rate: float = 0.0,
    per_lot_open: float = 0.0,
    per_lot_close: float = 0.0,
    per_lot_close_today: float = 0.0,
    margin_rate: float = 0.10,
) -> FeeMarginSpec:
    effective = pd.Timestamp("2020-01-01 00:00", tz=TZ).to_pydatetime()
    return FeeMarginSpec(
        schedule_id="RB_TEST",
        root_symbol="RB",
        contract_code=CONTRACT,
        margin_rate_long=margin_rate,
        margin_rate_short=margin_rate,
        open_fee_rate=open_rate,
        close_fee_rate=close_rate,
        close_today_fee_rate=close_today_rate,
        fee_per_lot_open=per_lot_open,
        fee_per_lot_close=per_lot_close,
        fee_per_lot_close_today=per_lot_close_today,
        effective_from=effective,
        effective_to=None,
        source="unit-test",
        known_at=effective,
    )


def _engine(
    *,
    tick: float = 1.0,
    slippage_ticks: float = 1.0,
    fee_spec: FeeMarginSpec | None = None,
    auto_match: bool = True,
    no_follow_through_bars: int = 3,
    max_holding_bars: int = 25,
    allow_cross_session: bool = False,
) -> ScalpEngine:
    return ScalpEngine(
        _instrument(tick=tick, slippage_ticks=slippage_ticks),
        fee_spec or _fees(),
        initial_cash=200_000.0,
        auto_match=auto_match,
        entry_valid_bars=3,
        no_follow_through_bars=no_follow_through_bars,
        max_holding_bars=max_holding_bars,
        force_flat_minutes=5,
        allow_cross_session=allow_cross_session,
    )


def _ts(value: str) -> datetime:
    return pd.Timestamp(value, tz=TZ).to_pydatetime()


def _candidate(
    setup_end: str,
    *,
    direction: int = 1,
    trigger: float = 100.0,
    stop: float = 95.0,
) -> SetupCandidate:
    setup_bar_end = _ts(setup_end)
    return SetupCandidate(
        candidate_id=f"candidate-{setup_bar_end:%Y%m%d%H%M}-{direction}",
        rule_id="strong_breakout_follow_through",
        symbol=SYMBOL,
        contract_code=CONTRACT,
        direction=direction,
        regime=(
            RegimeState.STRONG_TREND_UP
            if direction > 0
            else RegimeState.STRONG_TREND_DOWN
        ),
        setup_bar_end=setup_bar_end,
        trigger_price=trigger,
        structural_stop=stop,
        available_space_price=100.0,
        feature_source_max=setup_bar_end,
        expires_after_bar_end=setup_bar_end + timedelta(minutes=3),
        context={"test": 1},
    )


def _bar(
    start: str,
    *,
    open_: float,
    high: float,
    low: float,
    close: float,
    trade_date: date | None = None,
    pre_settlement: float = 100.0,
    limit_up: float = 110.0,
    limit_down: float = 90.0,
    session_close: str | None = None,
) -> MinuteBar:
    bar_start = _ts(start)
    bar_end = bar_start + timedelta(minutes=1)
    exchange_trade_date = trade_date or bar_start.date()
    open_at = datetime.combine(bar_start.date(), time(9, 0), tzinfo=bar_start.tzinfo)
    close_at = _ts(session_close) if session_close else datetime.combine(
        bar_start.date(), time(15, 0), tzinfo=bar_start.tzinfo
    )
    return MinuteBar(
        source_calendar_date=bar_start.date(),
        exchange_trade_date=exchange_trade_date,
        session_id=f"{exchange_trade_date:%Y%m%d}:day",
        session_kind="day",
        segment_id="day",
        bar_start=bar_start,
        bar_end=bar_end,
        root_symbol="RB",
        vt_symbol=SYMBOL,
        contract_code=CONTRACT,
        exchange="SHFE",
        open=open_,
        high=high,
        low=low,
        close=close,
        volume=100.0,
        turnover=100_000.0,
        open_interest=10_000.0,
        pre_settlement=pre_settlement,
        limit_up=limit_up,
        limit_down=limit_down,
        limit_source="unit-test",
        limit_known_at=open_at - timedelta(days=1),
        source_path="unit-test",
        source_row=0,
        calendar_sha256="calendar-test",
        session_open=open_at,
        session_close=close_at,
        segment_start=open_at,
        segment_end=close_at,
        bucket_anchor=open_at,
    )


def _order(snapshot: object, order_id: str):
    return next(order for order in snapshot.orders if order.order_id == order_id)


def test_setup_bar_never_fills_next_bar_can_trigger_and_third_bar_expires() -> None:
    engine = _engine(slippage_ticks=0.0)
    intent = engine.arm_candidate(
        _candidate("2026-01-05 09:01"), quantity=1, target_price=108.0
    )

    engine.on_bar(
        _bar("2026-01-05 09:00", open_=99.0, high=105.0, low=98.0, close=101.0)
    )
    assert engine.snapshot().position is None

    engine.on_bar(
        _bar("2026-01-05 09:01", open_=99.0, high=101.0, low=98.0, close=100.0)
    )
    assert engine.snapshot().position is not None
    assert engine.snapshot().position.entry_time == _ts("2026-01-05 09:02")

    expiring = _engine(slippage_ticks=0.0)
    expiring_intent = expiring.arm_candidate(
        _candidate("2026-01-05 10:01"), quantity=1, target_price=108.0
    )
    for minute in (1, 2, 3):
        expiring.on_bar(
            _bar(
                f"2026-01-05 10:{minute:02d}",
                open_=98.0,
                high=99.0,
                low=97.0,
                close=98.0,
            )
        )
    assert _order(expiring.snapshot(), expiring_intent.order_id).status is OrderStatus.EXPIRED

    expiring.on_bar(
        _bar("2026-01-05 10:04", open_=101.0, high=105.0, low=100.0, close=104.0)
    )
    assert expiring.snapshot().position is None
    assert not expiring.snapshot().fills
    assert _order(engine.snapshot(), intent.order_id).status is OrderStatus.FILLED
    events = engine.snapshot().order_events
    assert events[0].order_id == intent.order_id
    assert events[0].status is OrderStatus.WORKING
    assert any(
        event.order_id == intent.order_id and event.status is OrderStatus.FILLED
        for event in events
    )


def test_missing_session_tail_is_flagged_and_flattened_at_next_available_open() -> None:
    engine = _engine(slippage_ticks=0.0)
    engine.arm_candidate(
        _candidate("2026-01-05 09:01"),
        quantity=1,
        target_price=108.0,
    )
    engine.on_bar(
        _bar("2026-01-05 09:01", open_=99.0, high=101.0, low=98.0, close=100.0)
    )
    assert engine.snapshot().position is not None

    engine.on_bar(
        _bar(
            "2026-01-06 09:00",
            open_=99.0,
            high=100.0,
            low=98.0,
            close=99.0,
            trade_date=date(2026, 1, 6),
        )
    )

    snapshot = engine.snapshot()
    assert snapshot.position is None
    assert snapshot.forced_flat_failed
    assert snapshot.closed_trades[-1].exit_reason is ExitReason.FORCED_SESSION_FLAT


def test_open_exit_does_not_use_same_minute_close_to_look_through_limit_lock() -> None:
    engine = _engine(slippage_ticks=0.0)
    engine.arm_candidate(
        _candidate("2026-01-05 09:01"),
        quantity=1,
        target_price=108.0,
    )
    engine.on_bar(
        _bar("2026-01-05 09:01", open_=99.0, high=101.0, low=98.0, close=100.0)
    )
    engine.request_exit(ExitReason.EXCHANGE_KILL_SWITCH)

    engine.on_bar(
        _bar(
            "2026-01-05 09:02",
            open_=90.0,
            high=92.0,
            low=90.0,
            close=91.0,
            limit_down=90.0,
        )
    )
    locked = engine.snapshot()
    assert locked.position is not None
    assert locked.pending_exit_reason is ExitReason.EXCHANGE_KILL_SWITCH

    engine.on_bar(
        _bar(
            "2026-01-05 09:03",
            open_=91.0,
            high=92.0,
            low=90.0,
            close=91.0,
            limit_down=90.0,
        )
    )
    assert engine.snapshot().position is None


@pytest.mark.parametrize(
    ("direction", "trigger", "stop", "open_", "high", "low", "expected_reference", "expected_fill"),
    [
        (1, 100.0, 95.0, 101.1, 103.0, 100.5, 101.1, 102.0),
        (-1, 100.0, 105.0, 98.9, 99.5, 97.0, 98.9, 98.0),
    ],
)
def test_long_short_gap_stop_entry_uses_adverse_slippage_and_tick_rounding(
    direction: int,
    trigger: float,
    stop: float,
    open_: float,
    high: float,
    low: float,
    expected_reference: float,
    expected_fill: float,
) -> None:
    engine = _engine(tick=0.5, slippage_ticks=1.0)
    engine.arm_candidate(
        _candidate(
            "2026-01-05 09:01", direction=direction, trigger=trigger, stop=stop
        ),
        quantity=1,
        target_price=110.0 if direction > 0 else 90.0,
    )
    engine.on_bar(
        _bar(
            "2026-01-05 09:01",
            open_=open_,
            high=high,
            low=low,
            close=101.0 if direction > 0 else 99.0,
        )
    )

    fill = engine.snapshot().fills[0]
    assert fill.reference_price == pytest.approx(expected_reference)
    assert fill.fill_price == pytest.approx(expected_fill)
    assert engine.snapshot().position.direction == direction


def test_gap_entry_at_locked_limit_does_not_backfill_from_same_bar_close() -> None:
    engine = _engine(tick=1.0, slippage_ticks=1.0)
    engine.arm_candidate(
        _candidate("2026-01-05 09:01", trigger=100.0, stop=95.0),
        quantity=1,
        target_price=108.0,
    )
    engine.on_bar(
        _bar(
            "2026-01-05 09:01",
            open_=110.0,
            high=110.0,
            low=109.0,
            close=109.0,
            limit_up=110.0,
        )
    )
    assert engine.snapshot().position is None
    assert engine.snapshot().fills == ()


def test_adverse_slippage_is_clamped_to_daily_price_limit() -> None:
    engine = _engine(tick=1.0, slippage_ticks=2.0)
    engine.arm_candidate(
        _candidate("2026-01-05 09:01", trigger=109.0, stop=100.0),
        quantity=1,
        target_price=120.0,
    )
    engine.on_bar(
        _bar(
            "2026-01-05 09:01",
            open_=109.0,
            high=110.0,
            low=108.0,
            close=109.0,
            limit_up=110.0,
        )
    )
    assert engine.snapshot().fills[0].fill_price == 110.0


@pytest.mark.parametrize(
    ("direction", "trigger", "stop", "high", "low", "expected_stop_fill"),
    [
        (1, 100.0, 95.0, 101.0, 99.0, 90.0),
        (-1, 100.0, 105.0, 101.0, 99.0, 110.0),
    ],
)
def test_entry_risk_preview_clamps_extreme_stop_slippage_to_daily_limit(
    direction: int,
    trigger: float,
    stop: float,
    high: float,
    low: float,
    expected_stop_fill: float,
) -> None:
    engine = _engine(tick=1.0, slippage_ticks=1_000_000.0)
    engine.arm_candidate(
        _candidate(
            "2026-01-05 09:01",
            direction=direction,
            trigger=trigger,
            stop=stop,
        ),
        quantity=1,
        target_price=108.0 if direction > 0 else 92.0,
    )
    bar = _bar(
        "2026-01-05 09:01",
        open_=100.0,
        high=high,
        low=low,
        close=100.0,
        limit_up=110.0,
        limit_down=90.0,
    )
    engine.on_bar(bar, allow_entries=False)

    preview = engine.preview_entry_trigger(bar)

    assert preview is not None
    expected_fill = 110.0 if direction > 0 else 90.0
    expected_loss = abs(expected_fill - expected_stop_fill) * 10.0
    assert preview.fill_price == expected_fill
    assert preview.net_stop_loss_per_contract == expected_loss


@pytest.mark.parametrize(
    ("direction", "stop", "gap_open", "high", "low", "close", "expected_fill"),
    [
        (1, 98.0, 97.7, 98.0, 97.0, 97.5, 97.0),
        (-1, 102.0, 102.3, 103.0, 102.0, 102.5, 103.0),
    ],
)
def test_long_short_gap_stop_fills_from_open_with_adverse_slippage(
    direction: int,
    stop: float,
    gap_open: float,
    high: float,
    low: float,
    close: float,
    expected_fill: float,
) -> None:
    engine = _engine(tick=0.5, slippage_ticks=1.0)
    engine.arm_candidate(
        _candidate(
            "2026-01-05 09:01", direction=direction, trigger=100.0, stop=stop
        ),
        quantity=1,
        target_price=110.0 if direction > 0 else 90.0,
    )
    if direction > 0:
        entry_bar = _bar(
            "2026-01-05 09:01", open_=99.0, high=100.0, low=98.5, close=99.5
        )
    else:
        entry_bar = _bar(
            "2026-01-05 09:01", open_=101.0, high=101.5, low=100.0, close=100.5
        )
    engine.on_bar(entry_bar)
    engine.on_bar(
        _bar(
            "2026-01-05 09:02",
            open_=gap_open,
            high=high,
            low=low,
            close=close,
        )
    )

    closed = engine.snapshot().closed_trades[0]
    exit_fill = engine.snapshot().fills[-1]
    assert closed.exit_reason is ExitReason.GAP_STOP
    assert exit_fill.reference_price == pytest.approx(gap_open)
    assert exit_fill.fill_price == pytest.approx(expected_fill)


def test_entry_stop_and_target_in_same_bar_uses_stop_first_and_cancels_oco() -> None:
    engine = _engine(slippage_ticks=0.0)
    engine.arm_candidate(
        _candidate("2026-01-05 09:01", trigger=100.0, stop=98.0),
        quantity=1,
        target_price=102.0,
    )
    engine.on_bar(
        _bar("2026-01-05 09:01", open_=99.0, high=103.0, low=97.0, close=100.0)
    )

    snapshot = engine.snapshot()
    assert snapshot.position is None
    assert [fill.fill_price for fill in snapshot.fills] == [100.0, 98.0]
    assert snapshot.closed_trades[0].exit_reason is ExitReason.HARD_STOP
    target = next(order for order in snapshot.orders if order.order_type is OrderType.PROFIT_TARGET)
    assert target.status is OrderStatus.CANCELLED


def test_limit_lock_blocks_buy_entry_and_forced_sell_stays_pending_then_fails() -> None:
    engine = _engine(slippage_ticks=0.0)
    entry = engine.arm_candidate(
        _candidate("2026-01-05 09:01", trigger=100.0, stop=80.0),
        quantity=1,
        target_price=108.0,
    )
    engine.on_bar(
        _bar(
            "2026-01-05 09:01",
            open_=110.0,
            high=110.0,
            low=110.0,
            close=110.0,
        )
    )
    assert engine.snapshot().position is None
    assert _order(engine.snapshot(), entry.order_id).status is OrderStatus.WORKING

    engine.on_bar(
        _bar("2026-01-05 09:02", open_=100.0, high=101.0, low=99.0, close=100.0)
    )
    assert engine.snapshot().position is not None

    engine.on_bar(
        _bar(
            "2026-01-05 14:55",
            open_=90.0,
            high=90.0,
            low=90.0,
            close=90.0,
        )
    )
    assert engine.snapshot().position is not None
    assert engine.snapshot().forced_flat_pending
    assert not engine.snapshot().forced_flat_failed

    engine.on_bar(
        _bar(
            "2026-01-05 14:59",
            open_=90.0,
            high=90.0,
            low=90.0,
            close=90.0,
        )
    )
    assert engine.snapshot().forced_flat_pending
    assert engine.snapshot().forced_flat_failed


def test_forced_flat_pending_retries_on_next_unlocked_bar() -> None:
    engine = _engine(slippage_ticks=0.0)
    engine.arm_candidate(
        _candidate("2026-01-05 14:40", trigger=100.0, stop=80.0),
        quantity=1,
        target_price=108.0,
    )
    engine.on_bar(
        _bar("2026-01-05 14:40", open_=99.0, high=101.0, low=99.0, close=100.0)
    )
    engine.on_bar(
        _bar("2026-01-05 14:55", open_=90.0, high=90.0, low=90.0, close=90.0)
    )
    assert engine.snapshot().forced_flat_pending

    engine.on_bar(
        _bar("2026-01-05 14:56", open_=91.0, high=92.0, low=91.0, close=91.5)
    )
    assert engine.snapshot().position is None
    assert not engine.snapshot().forced_flat_pending
    assert engine.snapshot().closed_trades[-1].exit_reason is ExitReason.FORCED_SESSION_FLAT


def test_fee_is_turnover_plus_per_lot_with_quantity_applied_once() -> None:
    fees = _fees(
        open_rate=0.0001,
        close_rate=0.0002,
        close_today_rate=0.0005,
        per_lot_open=2.0,
        per_lot_close=3.0,
        per_lot_close_today=7.0,
    )

    opened = calculate_fee(fees, Offset.OPEN, 100.0, 3, contract_size=10.0)
    close_today = calculate_fee(
        fees, Offset.CLOSETODAY, 100.0, 3, contract_size=10.0
    )
    close_yesterday = calculate_fee(
        fees, Offset.CLOSEYESTERDAY, 100.0, 3, contract_size=10.0
    )

    assert opened.total_fee == pytest.approx(6.3)
    assert opened.close_today_surcharge == 0.0
    assert close_today.base_fee == pytest.approx(9.6)
    assert close_today.close_today_surcharge == pytest.approx(12.9)
    assert close_today.total_fee == pytest.approx(22.5)
    assert close_yesterday.total_fee == pytest.approx(9.6)


def test_engine_uses_close_today_then_rolls_inventory_to_close_yesterday() -> None:
    fees = _fees(
        close_rate=0.0002,
        close_today_rate=0.0005,
        per_lot_close=3.0,
        per_lot_close_today=7.0,
    )
    today = _engine(slippage_ticks=0.0, fee_spec=fees)
    today.arm_candidate(
        _candidate("2026-01-05 14:40", stop=80.0), quantity=1, target_price=108.0
    )
    today.on_bar(
        _bar("2026-01-05 14:40", open_=99.0, high=101.0, low=99.0, close=100.0)
    )
    today.on_bar(
        _bar("2026-01-05 14:55", open_=100.0, high=101.0, low=99.0, close=100.0)
    )
    assert today.snapshot().fills[-1].offset is Offset.CLOSETODAY

    yesterday = _engine(
        slippage_ticks=0.0,
        fee_spec=fees,
        allow_cross_session=True,
    )
    yesterday.arm_candidate(
        _candidate("2026-01-05 09:01", stop=80.0), quantity=1, target_price=108.0
    )
    yesterday.on_bar(
        _bar("2026-01-05 09:01", open_=99.0, high=101.0, low=99.0, close=100.0)
    )
    yesterday.on_bar(
        _bar(
            "2026-01-06 09:00",
            open_=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            trade_date=date(2026, 1, 6),
        )
    )
    assert yesterday.snapshot().position.today_quantity == 0
    assert yesterday.snapshot().position.yesterday_quantity == 1
    yesterday.on_bar(
        _bar(
            "2026-01-06 14:55",
            open_=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            trade_date=date(2026, 1, 6),
        )
    )
    assert yesterday.snapshot().fills[-1].offset is Offset.CLOSEYESTERDAY


def test_mixed_today_yesterday_inventory_is_split_into_correct_close_offsets() -> None:
    engine = _engine(
        slippage_ticks=0.0,
        auto_match=False,
        allow_cross_session=True,
    )
    entry = engine.arm_candidate(
        _candidate("2026-01-05 09:01", stop=80.0),
        quantity=2,
        target_price=120.0,
    )
    first_bar = _bar(
        "2026-01-05 09:01",
        open_=99.0,
        high=101.0,
        low=99.0,
        close=100.0,
    )
    engine.on_bar(first_bar)
    engine.on_fill(
        Fill(
            fill_id="mixed-entry-yesterday",
            order_id=entry.order_id,
            candidate_id=entry.candidate_id,
            symbol=SYMBOL,
            contract_code=CONTRACT,
            datetime=first_bar.bar_end,
            direction=1,
            offset=Offset.OPEN,
            quantity=1,
            reference_price=100.0,
            fill_price=100.0,
            source_bar_end=first_bar.bar_end,
            exchange_trade_date=date(2026, 1, 5),
        )
    )
    second_bar = _bar(
        "2026-01-06 09:00",
        open_=100.0,
        high=101.0,
        low=99.0,
        close=100.0,
        trade_date=date(2026, 1, 6),
    )
    engine.on_bar(second_bar)
    engine.on_fill(
        Fill(
            fill_id="mixed-entry-today",
            order_id=entry.order_id,
            candidate_id=entry.candidate_id,
            symbol=SYMBOL,
            contract_code=CONTRACT,
            datetime=second_bar.bar_end,
            direction=1,
            offset=Offset.OPEN,
            quantity=1,
            reference_price=100.0,
            fill_price=100.0,
            source_bar_end=second_bar.bar_end,
            exchange_trade_date=date(2026, 1, 6),
        )
    )
    assert engine.snapshot().position.today_quantity == 1
    assert engine.snapshot().position.yesterday_quantity == 1

    engine.request_exit(ExitReason.EXCHANGE_KILL_SWITCH)
    intents = engine.on_bar(
        _bar(
            "2026-01-06 09:01",
            open_=100.0,
            high=101.0,
            low=99.0,
            close=100.0,
            trade_date=date(2026, 1, 6),
        )
    )
    close_intents = [intent for intent in intents if intent.order_type is OrderType.MARKET_EXIT]
    assert {(intent.offset, intent.quantity) for intent in close_intents} == {
        (Offset.CLOSETODAY, 1),
        (Offset.CLOSEYESTERDAY, 1),
    }


def test_partial_and_late_fills_are_deduplicated_and_immediately_protected_by_oco() -> None:
    engine = _engine(slippage_ticks=0.0, auto_match=False)
    entry = engine.arm_candidate(
        _candidate("2026-01-05 09:01", stop=95.0), quantity=3, target_price=108.0
    )
    trigger_bar = _bar(
        "2026-01-05 09:01", open_=99.0, high=101.0, low=98.0, close=100.0
    )
    intents = engine.on_bar(trigger_bar)
    assert any(intent.order_id == entry.order_id for intent in intents)

    first = Fill(
        fill_id="fill-1",
        order_id=entry.order_id,
        candidate_id=entry.candidate_id,
        symbol=SYMBOL,
        contract_code=CONTRACT,
        datetime=trigger_bar.bar_end,
        direction=1,
        offset=Offset.OPEN,
        quantity=1,
        reference_price=100.0,
        fill_price=100.0,
        source_bar_end=trigger_bar.bar_end,
        exchange_trade_date=trigger_bar.exchange_trade_date,
    )
    engine.on_fill(first)
    engine.on_fill(first)
    snapshot = engine.snapshot()
    assert snapshot.position.quantity == 1
    assert _order(snapshot, entry.order_id).status is OrderStatus.PARTIALLY_FILLED
    assert _order(snapshot, snapshot.position.stop_order_id).quantity == 1
    assert _order(snapshot, snapshot.position.target_order_id).quantity == 1

    cancel = BrokerOrderEvent(
        event_id="order-event-1",
        order_id=entry.order_id,
        status=OrderStatus.CANCEL_PENDING,
        datetime=_ts("2026-01-05 09:02"),
        traded_quantity=1,
    )
    engine.on_order(cancel)
    engine.on_order(cancel)
    assert _order(engine.snapshot(), entry.order_id).status is OrderStatus.CANCEL_PENDING

    late = BrokerTradeEvent(
        trade_id="trade-2",
        order_id=entry.order_id,
        candidate_id=entry.candidate_id,
        symbol=SYMBOL,
        contract_code=CONTRACT,
        datetime=_ts("2026-01-05 09:02"),
        direction=1,
        offset=Offset.OPEN,
        quantity=1,
        reference_price=100.0,
        fill_price=100.5,
        source_bar_end=_ts("2026-01-05 09:02"),
        exchange_trade_date=date(2026, 1, 5),
    )
    engine.on_trade(late)
    engine.on_trade(late)
    snapshot = engine.snapshot()
    assert snapshot.position.quantity == 2
    assert _order(snapshot, snapshot.position.stop_order_id).quantity == 2
    assert _order(snapshot, snapshot.position.target_order_id).quantity == 2

    stop_order_id = snapshot.position.stop_order_id
    target_order_id = snapshot.position.target_order_id
    engine.on_fill(
        Fill(
            fill_id="fill-stop-1",
            order_id=stop_order_id,
            candidate_id=entry.candidate_id,
            symbol=SYMBOL,
            contract_code=CONTRACT,
            datetime=_ts("2026-01-05 09:03"),
            direction=-1,
            offset=Offset.CLOSETODAY,
            quantity=1,
            reference_price=95.0,
            fill_price=95.0,
            source_bar_end=_ts("2026-01-05 09:03"),
            exchange_trade_date=date(2026, 1, 5),
        )
    )
    assert engine.snapshot().position.quantity == 1
    engine.on_fill(
        Fill(
            fill_id="fill-stop-2",
            order_id=stop_order_id,
            candidate_id=entry.candidate_id,
            symbol=SYMBOL,
            contract_code=CONTRACT,
            datetime=_ts("2026-01-05 09:04"),
            direction=-1,
            offset=Offset.CLOSETODAY,
            quantity=1,
            reference_price=96.0,
            fill_price=96.0,
            source_bar_end=_ts("2026-01-05 09:04"),
            exchange_trade_date=date(2026, 1, 5),
        )
    )
    final = engine.snapshot()
    assert final.position is None
    assert _order(final, stop_order_id).status is OrderStatus.FILLED
    assert _order(final, target_order_id).status in {
        OrderStatus.CANCEL_PENDING,
        OrderStatus.CANCELLED,
    }
    assert len(final.fills) == 4
    assert final.closed_trades[0].quantity == 2
    assert final.closed_trades[0].exit_price == pytest.approx(95.5)
    assert final.closed_trades[0].gross_pnl == pytest.approx(-95.0)


def test_live_entry_submits_once_then_emits_cancel_when_validity_expires() -> None:
    engine = _engine(slippage_ticks=0.0, auto_match=False)
    entry = engine.arm_candidate(
        _candidate("2026-01-05 09:01", stop=95.0),
        quantity=2,
        target_price=108.0,
    )

    first = engine.on_bar(
        _bar("2026-01-05 09:01", open_=99.0, high=101.0, low=98.0, close=100.0)
    )
    second = engine.on_bar(
        _bar("2026-01-05 09:02", open_=99.0, high=101.0, low=98.0, close=100.0)
    )
    third = engine.on_bar(
        _bar("2026-01-05 09:03", open_=99.0, high=101.0, low=98.0, close=100.0)
    )

    assert [intent.action for intent in first] == [OrderAction.SUBMIT]
    assert second == []
    assert len(third) == 1
    assert third[0].order_id == entry.order_id
    assert third[0].action is OrderAction.CANCEL
    assert _order(engine.snapshot(), entry.order_id).status is OrderStatus.CANCEL_PENDING


def test_live_fill_recalculates_stop_risk_from_actual_fill_price() -> None:
    engine = _engine(slippage_ticks=0.0, auto_match=False)
    entry = engine.arm_candidate(
        _candidate("2026-01-05 09:01", stop=95.0),
        quantity=1,
        target_price=120.0,
        net_stop_loss_per_contract=50.0,
    )
    bar = _bar(
        "2026-01-05 09:01",
        open_=99.0,
        high=101.0,
        low=98.0,
        close=100.0,
    )
    engine.on_bar(bar)

    engine.on_fill(
        Fill(
            fill_id="actual-price-fill",
            order_id=entry.order_id,
            candidate_id=entry.candidate_id,
            symbol=SYMBOL,
            contract_code=CONTRACT,
            datetime=bar.bar_end,
            direction=1,
            offset=Offset.OPEN,
            quantity=1,
            reference_price=100.0,
            fill_price=104.0,
            source_bar_end=bar.bar_end,
            exchange_trade_date=bar.exchange_trade_date,
        )
    )

    assert engine.snapshot().position.initial_net_stop_loss_per_contract == pytest.approx(90.0)


def test_live_broker_protection_is_not_resubmitted_by_bar_matching() -> None:
    engine = _engine(slippage_ticks=0.0, auto_match=False)
    entry = engine.arm_candidate(
        _candidate("2026-01-05 09:01", stop=95.0),
        quantity=1,
        target_price=108.0,
    )
    trigger_bar = _bar(
        "2026-01-05 09:01",
        open_=99.0,
        high=101.0,
        low=98.0,
        close=100.0,
    )
    engine.on_bar(trigger_bar)
    protection = engine.on_fill(
        Fill(
            fill_id="live-entry",
            order_id=entry.order_id,
            candidate_id=entry.candidate_id,
            symbol=SYMBOL,
            contract_code=CONTRACT,
            datetime=trigger_bar.bar_end,
            direction=1,
            offset=Offset.OPEN,
            quantity=1,
            reference_price=100.0,
            fill_price=100.0,
            source_bar_end=trigger_bar.bar_end,
            exchange_trade_date=trigger_bar.exchange_trade_date,
        )
    )
    assert {intent.order_type for intent in protection} == {
        OrderType.HARD_STOP,
        OrderType.PROFIT_TARGET,
    }

    intents = engine.on_bar(
        _bar(
            "2026-01-05 09:02",
            open_=100.0,
            high=109.0,
            low=99.0,
            close=108.0,
        )
    )
    assert not any(
        intent.order_type in {OrderType.HARD_STOP, OrderType.PROFIT_TARGET}
        for intent in intents
    )


def test_filled_order_event_before_trade_does_not_double_count_execution() -> None:
    engine = _engine(slippage_ticks=0.0, auto_match=False)
    entry = engine.arm_candidate(
        _candidate("2026-01-05 09:01", stop=95.0),
        quantity=1,
        target_price=108.0,
    )
    bar = _bar("2026-01-05 09:01", open_=99.0, high=101.0, low=98.0, close=100.0)
    engine.on_bar(bar)
    order_event = BrokerOrderEvent(
        event_id="order-filled",
        order_id=entry.order_id,
        status=OrderStatus.FILLED,
        datetime=bar.bar_end,
        traded_quantity=1,
    )
    engine.on_order(order_event)
    engine.on_order(order_event)
    trade_event = BrokerTradeEvent(
        trade_id="trade-filled",
        order_id=entry.order_id,
        candidate_id=entry.candidate_id,
        symbol=SYMBOL,
        contract_code=CONTRACT,
        datetime=bar.bar_end,
        direction=1,
        offset=Offset.OPEN,
        quantity=1,
        reference_price=100.0,
        fill_price=100.0,
        source_bar_end=bar.bar_end,
        exchange_trade_date=bar.exchange_trade_date,
    )

    protection = engine.on_trade(trade_event)
    assert {intent.order_type for intent in protection} == {
        OrderType.HARD_STOP,
        OrderType.PROFIT_TARGET,
    }
    assert {intent.action for intent in protection} == {OrderAction.SUBMIT}
    assert engine.on_trade(trade_event) == []

    snapshot = engine.snapshot()
    assert snapshot.position is not None
    assert snapshot.position.quantity == 1
    order = _order(snapshot, entry.order_id)
    assert order.traded_quantity == 1
    assert order.broker_reported_traded_quantity == 1
    assert len(snapshot.fills) == 1


def test_no_follow_through_after_third_close_exits_at_next_open() -> None:
    engine = _engine(slippage_ticks=0.0)
    engine.arm_candidate(
        _candidate("2026-01-05 09:01", trigger=100.0, stop=96.0),
        quantity=1,
        target_price=110.0,
    )
    engine.on_bar(
        _bar("2026-01-05 09:01", open_=99.0, high=100.0, low=98.0, close=100.0)
    )
    engine.on_bar(
        _bar("2026-01-05 09:02", open_=100.0, high=100.5, low=99.0, close=99.5)
    )
    engine.on_bar(
        _bar("2026-01-05 09:03", open_=99.5, high=100.5, low=99.0, close=99.5)
    )
    assert engine.snapshot().position is not None
    assert engine.snapshot().pending_exit_reason is ExitReason.NO_FOLLOW_THROUGH_SCRATCH

    engine.on_bar(
        _bar("2026-01-05 09:04", open_=99.0, high=100.0, low=98.5, close=99.5)
    )
    closed = engine.snapshot().closed_trades[-1]
    assert closed.exit_reason is ExitReason.NO_FOLLOW_THROUGH_SCRATCH
    assert closed.exit_price == pytest.approx(99.0)


def test_contract_metadata_switch_cancels_old_entries_and_requires_flat_position() -> None:
    engine = _engine(slippage_ticks=0.0)
    old_entry = engine.arm_candidate(
        _candidate("2026-01-05 09:01"), quantity=1, target_price=108.0
    )
    next_contract = "RB2610.SHF"
    next_fees = replace(_fees(), contract_code=next_contract, schedule_id="RB_NEXT")

    engine.switch_contract_metadata(_instrument(), next_fees)

    assert engine.fee_spec.contract_code == next_contract
    assert _order(engine.snapshot(), old_entry.order_id).status is OrderStatus.CANCELLED
    next_bar = replace(
        _bar("2026-01-06 09:00", open_=100.0, high=101.0, low=99.0, close=100.0),
        contract_code=next_contract,
    )
    engine.on_bar(next_bar)

    positioned = _engine(slippage_ticks=0.0)
    positioned.arm_candidate(
        _candidate("2026-01-05 09:01"), quantity=1, target_price=108.0
    )
    positioned.on_bar(
        _bar("2026-01-05 09:01", open_=99.0, high=101.0, low=98.0, close=100.0)
    )
    with pytest.raises(ValueError, match="open position"):
        positioned.switch_contract_metadata(_instrument(), next_fees)


def test_twenty_fifth_holding_bar_schedules_exit_for_next_open() -> None:
    engine = _engine(slippage_ticks=0.0)
    engine.arm_candidate(
        _candidate("2026-01-05 09:01", trigger=100.0, stop=96.0),
        quantity=1,
        target_price=110.0,
    )
    engine.on_bar(
        _bar("2026-01-05 09:01", open_=99.0, high=101.1, low=98.0, close=100.5)
    )
    for minute in range(2, 26):
        engine.on_bar(
            _bar(
                f"2026-01-05 09:{minute:02d}",
                open_=100.5,
                high=101.1,
                low=99.5,
                close=100.5,
            )
        )
    assert engine.snapshot().position is not None
    assert engine.snapshot().position.holding_1m_bars == 25
    assert engine.snapshot().pending_exit_reason is ExitReason.MAX_HOLDING_TIME

    engine.on_bar(
        _bar("2026-01-05 09:26", open_=100.25, high=101.0, low=100.0, close=100.5)
    )
    assert engine.snapshot().position is None
    assert engine.snapshot().closed_trades[-1].exit_reason is ExitReason.MAX_HOLDING_TIME


def test_session_minus_five_minutes_forces_flat_before_time_exit() -> None:
    engine = _engine(slippage_ticks=0.0, max_holding_bars=100)
    engine.arm_candidate(
        _candidate("2026-01-05 14:40", stop=80.0), quantity=1, target_price=108.0
    )
    engine.on_bar(
        _bar("2026-01-05 14:40", open_=99.0, high=101.0, low=99.0, close=100.0)
    )
    engine.on_bar(
        _bar("2026-01-05 14:54", open_=100.0, high=101.0, low=99.0, close=100.0)
    )
    assert engine.snapshot().position is not None

    engine.on_bar(
        _bar("2026-01-05 14:55", open_=100.0, high=101.0, low=99.0, close=100.0)
    )
    assert engine.snapshot().position is None
    assert engine.snapshot().closed_trades[-1].exit_reason is ExitReason.FORCED_SESSION_FLAT


def test_marked_equity_uses_actual_fill_pnl_estimated_exit_fee_and_margin_mark() -> None:
    fees = _fees(
        open_rate=0.0001,
        close_rate=0.0002,
        close_today_rate=0.0005,
        per_lot_open=2.0,
        per_lot_close=3.0,
        per_lot_close_today=7.0,
        margin_rate=0.10,
    )
    engine = _engine(fee_spec=fees, auto_match=False, slippage_ticks=1.0)
    entry = engine.arm_candidate(
        _candidate("2026-01-05 09:01", trigger=100.0, stop=90.0),
        quantity=2,
        target_price=120.0,
    )
    bar = _bar(
        "2026-01-05 09:01",
        open_=99.0,
        high=101.0,
        low=98.0,
        close=100.0,
        pre_settlement=105.0,
    )
    engine.on_bar(bar)
    engine.on_fill(
        Fill(
            fill_id="entry-actual",
            order_id=entry.order_id,
            candidate_id=entry.candidate_id,
            symbol=SYMBOL,
            contract_code=CONTRACT,
            datetime=bar.bar_end,
            direction=1,
            offset=Offset.OPEN,
            quantity=2,
            reference_price=100.0,
            fill_price=102.0,
            source_bar_end=bar.bar_end,
            exchange_trade_date=bar.exchange_trade_date,
        )
    )
    snapshot = engine.snapshot()
    open_fee = calculate_fee(fees, Offset.OPEN, 102.0, 2, contract_size=10.0)
    estimated_exit = calculate_fee(
        fees, Offset.CLOSETODAY, 99.0, 2, contract_size=10.0
    )
    expected_equity = 200_000.0 - open_fee.total_fee + (99.0 - 102.0) * 10.0 * 2
    expected_equity -= estimated_exit.total_fee

    assert snapshot.margin_mark_price == pytest.approx(105.0)
    assert snapshot.position_margin == pytest.approx(210.0)
    assert snapshot.estimated_exit_fee == pytest.approx(estimated_exit.total_fee)
    assert snapshot.marked_equity == pytest.approx(expected_equity)

    stop_order_id = snapshot.position.stop_order_id
    engine.on_fill(
        Fill(
            fill_id="exit-actual",
            order_id=stop_order_id,
            candidate_id=entry.candidate_id,
            symbol=SYMBOL,
            contract_code=CONTRACT,
            datetime=_ts("2026-01-05 09:02"),
            direction=-1,
            offset=Offset.CLOSETODAY,
            quantity=2,
            reference_price=97.0,
            fill_price=96.0,
            source_bar_end=_ts("2026-01-05 09:02"),
            exchange_trade_date=date(2026, 1, 5),
        )
    )
    closed_snapshot = engine.snapshot()
    close_fee = calculate_fee(fees, Offset.CLOSETODAY, 96.0, 2, contract_size=10.0)
    expected_gross = (96.0 - 102.0) * 10.0 * 2
    assert closed_snapshot.closed_trades[-1].gross_pnl == pytest.approx(expected_gross)
    assert closed_snapshot.closed_trades[-1].net_pnl == pytest.approx(
        expected_gross - open_fee.total_fee - close_fee.total_fee
    )
    assert closed_snapshot.marked_equity == pytest.approx(
        200_000.0 + expected_gross - open_fee.total_fee - close_fee.total_fee
    )
    assert closed_snapshot.estimated_exit_fee == 0.0
    json.dumps(closed_snapshot.to_dict(), allow_nan=False)
