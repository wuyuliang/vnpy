"""Thin per-symbol vn.py shell; all decisions remain in the shared coordinator."""
from __future__ import annotations

from collections.abc import Callable
from datetime import date, datetime, timedelta
import hashlib
from pathlib import Path
from typing import Any

import pandas as pd

from vnpy.trader.constant import Direction, Offset as VnOffset, Status
from vnpy.trader.object import BarData, OrderData, TickData, TradeData
from vnpy.trader.utility import BarGenerator
from vnpy_ctastrategy import CtaTemplate

from .data import MinuteBar
from .engine import (
    BrokerOrderEvent,
    BrokerTradeEvent,
    Offset,
    OrderAction,
    OrderIntent,
    OrderStatus,
    OrderType,
)
from .metadata import BlockedMetadataError, DailyTradingSpec, InstrumentSpec
from .portfolio_coordinator import BrooksScalpPortfolioCoordinator
from .report import write_json
from .session import SessionCalendar, ensure_shanghai


class LiveBarAdapter:
    """Enrich a vn.py one-minute bar with authoritative session/daily metadata."""

    def __init__(
        self,
        *,
        symbol: str,
        contract_code: str,
        instrument: InstrumentSpec,
        calendar: SessionCalendar,
        daily_spec_provider: Callable[[str, object], DailyTradingSpec],
    ) -> None:
        self.symbol = symbol
        self.contract_code = contract_code
        self.instrument = instrument
        self.calendar = calendar
        self.daily_spec_provider = daily_spec_provider
        self._source_row = 0

    def convert(self, bar: BarData | object) -> MinuteBar:
        raw_start = pd.Timestamp(bar.datetime)
        if raw_start.tzinfo is None:
            raw_start = raw_start.tz_localize("Asia/Shanghai")
        else:
            raw_start = raw_start.tz_convert("Asia/Shanghai")
        bar_start = raw_start.to_pydatetime()
        bar_end = bar_start + timedelta(minutes=1)
        assignment = self.calendar.assign_bar(bar_start, bar_end)
        daily = self.daily_spec_provider(
            self.contract_code,
            assignment.exchange_trade_date,
        )
        if daily is None:
            raise BlockedMetadataError(
                f"daily metadata missing for {self.contract_code} {assignment.exchange_trade_date}"
            )
        if daily.contract_code != self.contract_code:
            raise BlockedMetadataError("live daily metadata contract mismatch")
        if daily.exchange_trade_date != assignment.exchange_trade_date:
            raise BlockedMetadataError("live daily metadata trade-date mismatch")
        known_at = ensure_shanghai(daily.known_at, "daily.known_at")
        if known_at > assignment.session_open:
            raise BlockedMetadataError("live daily metadata was not known before session open")
        result = MinuteBar(
            source_calendar_date=bar_start.date(),
            exchange_trade_date=assignment.exchange_trade_date,
            session_id=assignment.session_id,
            session_kind=assignment.session_kind,
            segment_id=assignment.segment_id,
            bar_start=bar_start,
            bar_end=bar_end,
            root_symbol=self.instrument.root_symbol,
            vt_symbol=self.symbol,
            contract_code=self.contract_code,
            exchange=self.instrument.exchange,
            open=float(bar.open_price),
            high=float(bar.high_price),
            low=float(bar.low_price),
            close=float(bar.close_price),
            volume=float(bar.volume),
            turnover=float(getattr(bar, "turnover", 0.0)),
            open_interest=float(getattr(bar, "open_interest", 0.0)),
            pre_settlement=daily.pre_settlement,
            limit_up=daily.limit_up,
            limit_down=daily.limit_down,
            limit_source=daily.source,
            limit_known_at=known_at,
            source_path="vnpy-live",
            source_row=self._source_row,
            calendar_sha256=assignment.calendar_sha256,
            session_open=assignment.session_open,
            session_close=assignment.session_close,
            segment_start=assignment.segment_start,
            segment_end=assignment.segment_end,
            bucket_anchor=assignment.bucket_anchor,
        )
        self._source_row += 1
        return result


class BrooksScalpLiveStrategy(CtaTemplate):
    """Single-vt_symbol transport shell with no local signal or sizing logic."""

    author = "Brooks rule-only scalp"

    state_path: str = "cta/strategy/brooks/report/scalp/live_state"
    parameters = ["state_path"]
    variables = ["pos"]

    def __init__(
        self,
        cta_engine: object,
        strategy_name: str,
        vt_symbol: str,
        setting: dict[str, Any],
    ) -> None:
        runtime = dict(setting)
        coordinator = runtime.pop("_coordinator", None)
        bar_adapter = runtime.pop("_bar_adapter", None)
        super().__init__(cta_engine, strategy_name, vt_symbol, runtime)
        if not isinstance(coordinator, BrooksScalpPortfolioCoordinator):
            raise ValueError("BrooksScalpLiveStrategy requires shared _coordinator")
        if not isinstance(bar_adapter, LiveBarAdapter):
            raise ValueError("BrooksScalpLiveStrategy requires _bar_adapter")
        self.coordinator = coordinator
        self.bar_adapter = bar_adapter
        self.bg = BarGenerator(self.on_bar)
        self._broker_to_engine_order: dict[str, str] = {}
        self._last_bar_end: datetime | None = None
        self._last_exchange_trade_date: date | None = None

    def on_init(self) -> None:
        self.write_log("Brooks scalp shell initializing")
        self.load_bar(30)

    def on_start(self) -> None:
        self.write_log("Brooks scalp shell started")

    def on_stop(self) -> None:
        self.cancel_all()
        self._persist_state()
        self.write_log("Brooks scalp shell stopped")

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        minute = self.bar_adapter.convert(bar)
        self._last_bar_end = minute.bar_end
        self._last_exchange_trade_date = minute.exchange_trade_date
        try:
            intents = self.coordinator.on_symbol_bar(self.vt_symbol, minute)
        except Exception as exc:
            self.write_log(f"coordinator unavailable; risk reduction only: {exc}")
            self._fail_safe_reduce(bar)
            return
        self._dispatch(intents)
        self._persist_state()
        self.put_event()

    def on_order(self, order: OrderData) -> None:
        engine_order_id = self._broker_to_engine_order.get(order.vt_orderid)
        if engine_order_id is None:
            return
        timestamp = _event_datetime(order, self._last_bar_end)
        status = _map_order_status(order.status)
        raw = f"{order.vt_orderid}|{status.value}|{order.traded}|{timestamp.isoformat()}"
        event = BrokerOrderEvent(
            event_id=hashlib.sha256(raw.encode()).hexdigest()[:24],
            order_id=engine_order_id,
            status=status,
            datetime=timestamp,
            traded_quantity=int(order.traded),
        )
        self._dispatch(self.coordinator.on_order(self.vt_symbol, event))

    def on_trade(self, trade: TradeData) -> None:
        engine_order_id = self._broker_to_engine_order.get(trade.vt_orderid)
        if engine_order_id is None:
            return
        if self._last_exchange_trade_date is None:
            raise ValueError("trade arrived before an exchange trade date was established")
        timestamp = _event_datetime(trade, self._last_bar_end)
        side = 1 if trade.direction is Direction.LONG else -1
        engine = self.coordinator.snapshot().engines[self.vt_symbol]
        working = next(order for order in engine.orders if order.order_id == engine_order_id)
        offset = _map_offset(trade.offset)
        if offset is Offset.CLOSE:
            offset = working.offset
        event = BrokerTradeEvent(
            trade_id=trade.vt_tradeid,
            order_id=engine_order_id,
            candidate_id=working.candidate_id,
            symbol=self.vt_symbol,
            contract_code=working.contract_code,
            datetime=timestamp,
            direction=side,
            offset=offset,
            quantity=int(trade.volume),
            reference_price=working.price,
            fill_price=float(trade.price),
            source_bar_end=self._last_bar_end or timestamp,
            exchange_trade_date=self._last_exchange_trade_date,
        )
        self._dispatch(self.coordinator.on_trade(self.vt_symbol, event))
        self.put_event()

    def _dispatch(self, intents: list[OrderIntent]) -> None:
        for intent in intents:
            if intent.symbol != self.vt_symbol:
                continue
            if intent.action is OrderAction.CANCEL:
                for broker_id, engine_id in tuple(self._broker_to_engine_order.items()):
                    if engine_id == intent.order_id:
                        self.cancel_order(broker_id)
                continue
            if intent.quantity <= 0:
                continue
            is_stop = intent.order_type is OrderType.HARD_STOP
            if intent.offset is Offset.OPEN and intent.direction > 0:
                broker_ids = self.buy(intent.price, intent.quantity)
            elif intent.offset is Offset.OPEN:
                broker_ids = self.short(intent.price, intent.quantity)
            else:
                direction = (
                    Direction.SHORT if intent.direction < 0 else Direction.LONG
                )
                broker_ids = self.send_order(
                    direction,
                    _vn_offset(intent.offset),
                    intent.price,
                    intent.quantity,
                    stop=is_stop,
                )
            for broker_id in broker_ids:
                self._broker_to_engine_order[broker_id] = intent.order_id

    def _fail_safe_reduce(self, bar: BarData) -> None:
        self.cancel_all()
        if self.pos > 0:
            self.sell(float(bar.low_price), abs(self.pos))
        elif self.pos < 0:
            self.cover(float(bar.high_price), abs(self.pos))

    def _persist_state(self) -> None:
        path = Path(self.state_path) / f"{self.vt_symbol.replace('.', '_')}.json"
        path.parent.mkdir(parents=True, exist_ok=True)
        temporary = path.with_suffix(".tmp")
        write_json(temporary, self.coordinator.snapshot().to_dict())
        temporary.replace(path)


def _event_datetime(event: object, fallback: datetime | None) -> datetime:
    value = getattr(event, "datetime", None)
    if value is None:
        if fallback is None:
            raise ValueError("broker event has no deterministic timestamp")
        return fallback
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        timestamp = timestamp.tz_localize("Asia/Shanghai")
    else:
        timestamp = timestamp.tz_convert("Asia/Shanghai")
    return timestamp.to_pydatetime()


def _map_order_status(status: Status) -> OrderStatus:
    mapping = {
        Status.SUBMITTING: OrderStatus.SUBMITTED,
        Status.NOTTRADED: OrderStatus.SUBMITTED,
        Status.PARTTRADED: OrderStatus.PARTIALLY_FILLED,
        Status.ALLTRADED: OrderStatus.FILLED,
        Status.CANCELLED: OrderStatus.CANCELLED,
        Status.REJECTED: OrderStatus.REJECTED,
    }
    return mapping.get(status, OrderStatus.SUBMITTED)


def _map_offset(offset: VnOffset) -> Offset:
    if offset is VnOffset.OPEN:
        return Offset.OPEN
    if offset is VnOffset.CLOSETODAY:
        return Offset.CLOSETODAY
    if offset is VnOffset.CLOSEYESTERDAY:
        return Offset.CLOSEYESTERDAY
    return Offset.CLOSE


def _vn_offset(offset: Offset) -> VnOffset:
    mapping = {
        Offset.OPEN: VnOffset.OPEN,
        Offset.CLOSE: VnOffset.CLOSE,
        Offset.CLOSETODAY: VnOffset.CLOSETODAY,
        Offset.CLOSEYESTERDAY: VnOffset.CLOSEYESTERDAY,
    }
    return mapping[offset]


__all__ = ["BrooksScalpLiveStrategy", "LiveBarAdapter"]
