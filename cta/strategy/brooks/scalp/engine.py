"""Shared one-minute order, execution, position, and marked-equity engine."""
from __future__ import annotations

from dataclasses import asdict, dataclass, replace
from datetime import date, datetime
from enum import Enum
import hashlib
import math
from typing import Any

import pandas as pd

from .data import MinuteBar
from .metadata import FeeMarginSpec, InstrumentSpec
from .setups import SetupCandidate


class Offset(str, Enum):
    OPEN = "OPEN"
    CLOSE = "CLOSE"
    CLOSETODAY = "CLOSETODAY"
    CLOSEYESTERDAY = "CLOSEYESTERDAY"


class OrderType(str, Enum):
    ENTRY_STOP = "ENTRY_STOP"
    HARD_STOP = "HARD_STOP"
    PROFIT_TARGET = "PROFIT_TARGET"
    MARKET_EXIT = "MARKET_EXIT"


class OrderAction(str, Enum):
    SUBMIT = "SUBMIT"
    CANCEL = "CANCEL"


class OrderStatus(str, Enum):
    CREATED = "CREATED"
    RISK_APPROVED = "RISK_APPROVED"
    WORKING = "WORKING"
    SUBMITTED = "SUBMITTED"
    PARTIALLY_FILLED = "PARTIALLY_FILLED"
    FILLED = "FILLED"
    CANCEL_PENDING = "CANCEL_PENDING"
    CANCELLED = "CANCELLED"
    REJECTED = "REJECTED"
    EXPIRED = "EXPIRED"


class ExitReason(str, Enum):
    EXCHANGE_KILL_SWITCH = "exchange_account_kill_switch"
    HARD_STOP = "hard_stop"
    GAP_STOP = "gap_stop"
    FORCED_SESSION_FLAT = "forced_session_flat"
    PROFIT_TARGET = "profit_target"
    NO_FOLLOW_THROUGH_SCRATCH = "no_follow_through_scratch"
    MAX_HOLDING_TIME = "max_holding_time"
    MARGIN_REDUCTION = "margin_reduction"
    ENTRY_RISK_REDUCTION = "entry_risk_reduction"


class RejectReason(str, Enum):
    QTY_ZERO = "qty_zero"
    METADATA_MISSING = "metadata_missing"
    ENTRY_EXPIRED = "entry_expired"
    LIMIT_LOCKED = "limit_locked"
    SESSION_CUTOFF = "session_cutoff"


@dataclass(frozen=True)
class FeeBreakdown:
    base_fee: float
    close_today_surcharge: float
    total_fee: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def calculate_fee(
    spec: FeeMarginSpec,
    offset: Offset,
    price: float,
    quantity: int,
    *,
    contract_size: float,
) -> FeeBreakdown:
    """Calculate ad-valorem plus per-lot fees, applying quantity exactly once."""
    if not _positive(price) or not _positive(contract_size) or quantity < 0:
        raise ValueError("invalid fee input")
    notional = price * contract_size * quantity
    if offset is Offset.OPEN:
        base = notional * spec.open_fee_rate + quantity * spec.fee_per_lot_open
        return FeeBreakdown(base, 0.0, base)
    base = notional * spec.close_fee_rate + quantity * spec.fee_per_lot_close
    if offset is Offset.CLOSETODAY:
        today = (
            notional * spec.close_today_fee_rate
            + quantity * spec.fee_per_lot_close_today
        )
        surcharge = today - base
        return FeeBreakdown(base, surcharge, today)
    return FeeBreakdown(base, 0.0, base)


@dataclass(frozen=True)
class TradePlan:
    trigger_price: float
    structural_stop: float
    target_price: float
    obstacle_price: float
    price_risk: float
    net_stop_loss_per_contract: float
    planned_net_payoff: float
    available_space_r: float
    pressure_cost_price: float

    def to_dict(self) -> dict[str, float]:
        return asdict(self)


def plan_target_price(
    candidate: SetupCandidate,
    obstacle_price: float,
    instrument: InstrumentSpec,
    fees: FeeMarginSpec,
    *,
    planned_net_payoff: float = 1.10,
    allowed_min: float = 1.02,
    allowed_max: float = 1.20,
    minimum_space_r: float = 1.20,
    pressure_fee_multiplier: float = 2.0,
    pressure_slippage_multiplier: float = 3.0,
) -> TradePlan | None:
    """Enumerate legal ticks until the first target reaches the frozen net payoff."""
    trigger = candidate.trigger_price
    stop = candidate.structural_stop
    direction = candidate.direction
    price_risk = abs(trigger - stop)
    if price_risk <= 0 or direction * (obstacle_price - trigger) <= 0:
        return None
    open_fee = calculate_fee(fees, Offset.OPEN, trigger, 1, contract_size=instrument.contract_size)
    stop_fee = calculate_fee(
        fees,
        Offset.CLOSETODAY,
        stop,
        1,
        contract_size=instrument.contract_size,
    )
    slippage = (
        2.0
        * instrument.slippage_ticks_base
        * instrument.price_tick
        * instrument.contract_size
    )
    stop_loss = price_risk * instrument.contract_size + open_fee.total_fee + stop_fee.total_fee + slippage
    obstacle_fee = calculate_fee(
        fees,
        Offset.CLOSETODAY,
        obstacle_price,
        1,
        contract_size=instrument.contract_size,
    )
    pressure_fee = pressure_fee_multiplier * (
        open_fee.total_fee + max(stop_fee.total_fee, obstacle_fee.total_fee)
    )
    pressure_slippage = (
        2.0
        * pressure_slippage_multiplier
        * instrument.slippage_ticks_base
        * instrument.price_tick
        * instrument.contract_size
    )
    pressure_cost_price = (pressure_fee + pressure_slippage) / instrument.contract_size
    available = abs(obstacle_price - trigger)
    available_space_r = (available - pressure_cost_price) / price_risk
    if available < minimum_space_r * price_risk + pressure_cost_price:
        return None
    max_steps = int(available / instrument.price_tick)
    for steps in range(1, max_steps + 1):
        target = trigger + direction * steps * instrument.price_tick
        close_fee = calculate_fee(
            fees,
            Offset.CLOSETODAY,
            target,
            1,
            contract_size=instrument.contract_size,
        )
        net_profit = (
            abs(target - trigger) * instrument.contract_size
            - open_fee.total_fee
            - close_fee.total_fee
            - slippage
        )
        payoff = net_profit / stop_loss
        if payoff >= planned_net_payoff:
            if not allowed_min <= payoff <= allowed_max:
                return None
            return TradePlan(
                trigger,
                stop,
                target,
                obstacle_price,
                price_risk,
                stop_loss,
                payoff,
                available_space_r,
                pressure_cost_price,
            )
    return None


@dataclass(frozen=True)
class OrderIntent:
    order_id: str
    candidate_id: str
    symbol: str
    contract_code: str
    datetime: datetime
    direction: int
    offset: Offset
    quantity: int
    order_type: OrderType
    price: float
    reduce_only: bool
    action: OrderAction = OrderAction.SUBMIT
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["datetime"] = self.datetime.isoformat()
        payload["offset"] = self.offset.value
        payload["order_type"] = self.order_type.value
        payload["action"] = self.action.value
        return payload


@dataclass
class WorkingOrder:
    order_id: str
    candidate_id: str
    symbol: str
    contract_code: str
    direction: int
    offset: Offset
    quantity: int
    price: float
    order_type: OrderType
    status: OrderStatus
    created_at: datetime
    eligible_after: datetime
    expires_after_bars: int
    reduce_only: bool
    traded_quantity: int = 0
    broker_reported_traded_quantity: int = 0
    eligible_bars_seen: int = 0
    exit_reason: ExitReason | None = None
    structural_stop: float | None = None
    target_price: float | None = None
    net_stop_loss_per_contract: float | None = None

    @property
    def remaining_quantity(self) -> int:
        return max(0, self.quantity - self.traded_quantity)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["offset"] = self.offset.value
        payload["order_type"] = self.order_type.value
        payload["status"] = self.status.value
        payload["created_at"] = self.created_at.isoformat()
        payload["eligible_after"] = self.eligible_after.isoformat()
        payload["exit_reason"] = self.exit_reason.value if self.exit_reason else None
        return payload


@dataclass(frozen=True)
class EntryTriggerPreview:
    order_id: str
    remaining_quantity: int
    reference_price: float
    fill_price: float
    net_stop_loss_per_contract: float
    reserved_open_risk: float


@dataclass(frozen=True)
class Fill:
    fill_id: str
    order_id: str
    candidate_id: str
    symbol: str
    contract_code: str
    datetime: datetime
    direction: int
    offset: Offset
    quantity: int
    reference_price: float
    fill_price: float
    source_bar_end: datetime
    exchange_trade_date: date
    base_fee: float = 0.0
    close_today_surcharge: float = 0.0
    total_fee: float = 0.0
    slippage: float = 0.0
    margin_after: float = 0.0
    marked_equity_after: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["datetime"] = self.datetime.isoformat()
        payload["source_bar_end"] = self.source_bar_end.isoformat()
        payload["exchange_trade_date"] = self.exchange_trade_date.isoformat()
        payload["offset"] = self.offset.value
        return payload


@dataclass(frozen=True)
class BrokerOrderEvent:
    event_id: str
    order_id: str
    status: OrderStatus
    datetime: datetime
    traded_quantity: int


@dataclass(frozen=True)
class OrderLifecycleEvent:
    event_id: str
    order_id: str
    status: OrderStatus
    datetime: datetime
    traded_quantity: int
    broker_reported_traded_quantity: int
    source: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["status"] = self.status.value
        payload["datetime"] = self.datetime.isoformat()
        return payload


@dataclass(frozen=True)
class BrokerTradeEvent:
    trade_id: str
    order_id: str
    candidate_id: str
    symbol: str
    contract_code: str
    datetime: datetime
    direction: int
    offset: Offset
    quantity: int
    reference_price: float
    fill_price: float
    source_bar_end: datetime
    exchange_trade_date: date


@dataclass
class PositionState:
    candidate_id: str
    symbol: str
    contract_code: str
    direction: int
    quantity: int
    initial_quantity: int
    entry_time: datetime
    entry_price: float
    total_entry_notional: float
    total_exit_notional: float
    exited_quantity: int
    entry_trigger: float
    initial_stop: float
    planned_target: float
    initial_net_stop_loss_per_contract: float
    entry_fee: float
    exit_fee: float
    total_slippage: float
    today_quantity: int
    yesterday_quantity: int
    entry_trade_date: date
    holding_1m_bars: int
    mfe_price: float
    mae_price: float
    stop_order_id: str
    target_order_id: str

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entry_time"] = self.entry_time.isoformat()
        payload["entry_trade_date"] = self.entry_trade_date.isoformat()
        return payload


@dataclass(frozen=True)
class ClosedTrade:
    trade_id: str
    candidate_id: str
    symbol: str
    contract_code: str
    direction: int
    entry_time: datetime
    entry_price: float
    quantity: int
    initial_risk_cny: float
    initial_stop: float
    planned_target: float
    exit_time: datetime
    exit_price: float
    exit_reason: ExitReason
    gross_pnl: float
    total_fee: float
    total_slippage: float
    net_pnl: float
    net_r: float
    mfe_r: float
    mae_r: float
    holding_1m_bars: int
    exchange_trade_date: date

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["entry_time"] = self.entry_time.isoformat()
        payload["exit_time"] = self.exit_time.isoformat()
        payload["exit_reason"] = self.exit_reason.value
        payload["exchange_trade_date"] = self.exchange_trade_date.isoformat()
        return payload


@dataclass(frozen=True)
class EngineSnapshot:
    orders: tuple[WorkingOrder, ...]
    order_events: tuple[OrderLifecycleEvent, ...]
    position: PositionState | None
    fills: tuple[Fill, ...]
    closed_trades: tuple[ClosedTrade, ...]
    marked_equity: float
    realized_gross_pnl: float
    paid_fees: float
    estimated_exit_fee: float
    margin_mark_price: float
    position_margin: float
    working_order_margin_reserve: float
    margin_usage: float
    available_funds: float
    forced_flat_pending: bool
    forced_flat_failed: bool
    pending_exit_reason: ExitReason | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "orders": [order.to_dict() for order in self.orders],
            "order_events": [event.to_dict() for event in self.order_events],
            "position": self.position.to_dict() if self.position else None,
            "fills": [fill.to_dict() for fill in self.fills],
            "closed_trades": [trade.to_dict() for trade in self.closed_trades],
            "marked_equity": self.marked_equity,
            "realized_gross_pnl": self.realized_gross_pnl,
            "paid_fees": self.paid_fees,
            "estimated_exit_fee": self.estimated_exit_fee,
            "margin_mark_price": self.margin_mark_price,
            "position_margin": self.position_margin,
            "working_order_margin_reserve": self.working_order_margin_reserve,
            "margin_usage": self.margin_usage,
            "available_funds": self.available_funds,
            "forced_flat_pending": self.forced_flat_pending,
            "forced_flat_failed": self.forced_flat_failed,
            "pending_exit_reason": self.pending_exit_reason.value if self.pending_exit_reason else None,
        }


class ScalpEngine:
    """Deterministic state machine shared by backtest auto-match and live callbacks."""

    def __init__(
        self,
        instrument: InstrumentSpec,
        fee_spec: FeeMarginSpec,
        *,
        initial_cash: float = 200_000.0,
        auto_match: bool = True,
        entry_valid_bars: int = 3,
        no_follow_through_bars: int = 3,
        max_holding_bars: int = 25,
        force_flat_minutes: int = 5,
        allow_cross_session: bool = False,
    ) -> None:
        if not _positive(initial_cash):
            raise ValueError("initial_cash must be positive")
        self.instrument = instrument
        self.fee_spec = fee_spec
        self.initial_cash = float(initial_cash)
        self.auto_match = bool(auto_match)
        self.entry_valid_bars = int(entry_valid_bars)
        self.no_follow_through_bars = int(no_follow_through_bars)
        self.max_holding_bars = int(max_holding_bars)
        self.force_flat_minutes = int(force_flat_minutes)
        self.allow_cross_session = bool(allow_cross_session)
        self.orders: list[WorkingOrder] = []
        self.order_events: list[OrderLifecycleEvent] = []
        self.position: PositionState | None = None
        self.fills: list[Fill] = []
        self.closed_trades: list[ClosedTrade] = []
        self.realized_gross_pnl = 0.0
        self.paid_fees = 0.0
        self.estimated_exit_fee = 0.0
        self.marked_equity = self.initial_cash
        self.margin_mark_price = 0.0
        self.position_margin = 0.0
        self.working_order_margin_reserve = 0.0
        self.margin_usage = 0.0
        self.available_funds = self.initial_cash
        self.forced_flat_pending = False
        self.forced_flat_failed = False
        self.pending_exit_reason: ExitReason | None = None
        self._current_bar: MinuteBar | None = None
        self._last_bar_end: datetime | None = None
        self._seen_fill_ids: set[str] = set()
        self._seen_order_events: set[str] = set()
        self._seen_trade_ids: set[str] = set()
        self._live_protection_dispatched: set[str] = set()
        self._fill_sequence = 0
        self._order_sequence = 0
        self._order_event_sequence = 0

    def arm_candidate(
        self,
        candidate: SetupCandidate,
        *,
        quantity: int,
        target_price: float,
        net_stop_loss_per_contract: float | None = None,
    ) -> OrderIntent:
        if quantity <= 0:
            raise ValueError("quantity must be positive")
        if net_stop_loss_per_contract is not None and not _positive(
            net_stop_loss_per_contract
        ):
            raise ValueError("net_stop_loss_per_contract must be positive")
        if self.position is not None or any(
            order.order_type is OrderType.ENTRY_STOP and _active(order.status)
            for order in self.orders
        ):
            raise ValueError("symbol already has an active entry or position")
        if candidate.contract_code != self.fee_spec.contract_code and self.fee_spec.contract_code:
            raise ValueError("fee schedule contract mismatch")
        order_id = self._next_order_id(candidate.candidate_id, "entry")
        order = WorkingOrder(
            order_id=order_id,
            candidate_id=candidate.candidate_id,
            symbol=candidate.symbol,
            contract_code=candidate.contract_code,
            direction=candidate.direction,
            offset=Offset.OPEN,
            quantity=int(quantity),
            price=float(candidate.trigger_price),
            order_type=OrderType.ENTRY_STOP,
            status=OrderStatus.WORKING,
            created_at=candidate.setup_bar_end,
            eligible_after=candidate.setup_bar_end,
            expires_after_bars=self.entry_valid_bars,
            reduce_only=False,
            structural_stop=float(candidate.structural_stop),
            target_price=float(target_price),
            net_stop_loss_per_contract=(
                float(net_stop_loss_per_contract)
                if net_stop_loss_per_contract is not None
                else None
            ),
        )
        self._append_order(order, candidate.setup_bar_end, source="strategy")
        return self._intent(order, candidate.setup_bar_end)

    def on_bar(
        self,
        bar: MinuteBar,
        *,
        allow_entries: bool = True,
    ) -> list[OrderIntent]:
        self._validate_bar(bar)
        session_changed = (
            self._current_bar is not None
            and self._current_bar.session_id != bar.session_id
        )
        self._roll_inventory(bar.exchange_trade_date)
        self._current_bar = bar
        intents: list[OrderIntent] = []

        if session_changed:
            for order in self.orders:
                if order.order_type is OrderType.ENTRY_STOP and _active(order.status):
                    self._set_order_status(
                        order,
                        OrderStatus.CANCELLED,
                        bar.bar_end,
                        source="session_guard",
                    )
            if self.position is not None and not self.allow_cross_session:
                self.forced_flat_failed = True
                intents.extend(
                    self._attempt_market_exit(
                        bar,
                        ExitReason.FORCED_SESSION_FLAT,
                        at_open=True,
                    )
                )

        if self.position is not None and self.pending_exit_reason is not None:
            reason = self.pending_exit_reason
            exit_intents = self._attempt_market_exit(bar, reason, at_open=True)
            intents.extend(exit_intents)
            if exit_intents:
                self.pending_exit_reason = None

        force_at = bar.session_close - pd.Timedelta(minutes=self.force_flat_minutes)
        if self.position is not None and bar.bar_start >= force_at:
            intents.extend(
                self._attempt_market_exit(bar, ExitReason.FORCED_SESSION_FLAT, at_open=True)
            )

        if self.position is not None:
            intents.extend(self._match_existing_position(bar))

        if allow_entries and self.position is None:
            intents.extend(self._process_entries(bar))

        if self.position is not None:
            self._update_position_path(bar)
            self.position.holding_1m_bars += 1
            self._schedule_close_exit()

        if bar.bar_end >= bar.session_close and self.position is not None:
            if self.forced_flat_pending:
                self.forced_flat_failed = True
            else:
                self.forced_flat_failed = True
                self.forced_flat_pending = True
        self._mark(bar)
        self._last_bar_end = bar.bar_end
        return intents

    def on_entry_bar(self, bar: MinuteBar) -> list[OrderIntent]:
        """Run the entry phase after a coordinator has processed every exit."""
        if self._current_bar is None or self._current_bar.bar_end != bar.bar_end:
            raise ValueError("entry phase must follow the same bar exit phase")
        if self.position is not None:
            return []
        intents = self._process_entries(bar)
        if self.position is not None:
            self._update_position_path(bar)
            self.position.holding_1m_bars += 1
            self._schedule_close_exit()
        self._mark(bar)
        return intents

    def on_fill(self, fill: Fill) -> list[OrderIntent]:
        if fill.fill_id in self._seen_fill_ids:
            return []
        if fill.quantity <= 0:
            raise ValueError("fill quantity must be positive")
        order = self._find_order(fill.order_id)
        if order is None:
            raise ValueError(f"unknown order: {fill.order_id}")
        if fill.contract_code != order.contract_code or fill.candidate_id != order.candidate_id:
            raise ValueError("fill/order identity mismatch")
        if fill.quantity > order.remaining_quantity:
            raise ValueError("fill exceeds remaining order quantity")
        fees = calculate_fee(
            self.fee_spec,
            fill.offset,
            fill.fill_price,
            fill.quantity,
            contract_size=self.instrument.contract_size,
        )
        slippage = (
            abs(fill.fill_price - fill.reference_price)
            * self.instrument.contract_size
            * fill.quantity
        )
        enriched = replace(
            fill,
            base_fee=fees.base_fee,
            close_today_surcharge=fees.close_today_surcharge,
            total_fee=fees.total_fee,
            slippage=slippage,
        )
        self._seen_fill_ids.add(fill.fill_id)
        order.traded_quantity += fill.quantity
        self._set_order_status(
            order,
            (
                OrderStatus.FILLED
                if order.traded_quantity >= order.quantity
                else OrderStatus.PARTIALLY_FILLED
            ),
            fill.datetime,
            source="trade",
        )
        self.paid_fees += fees.total_fee
        protection_ids = (
            (self.position.stop_order_id, self.position.target_order_id)
            if self.position is not None
            else ()
        )
        if fill.offset is Offset.OPEN:
            self._apply_entry_fill(order, enriched)
        else:
            self._apply_exit_fill(order, enriched)
        self.fills.append(enriched)
        if self._current_bar is not None:
            self._mark(self._current_bar)
            self.fills[-1] = replace(
                self.fills[-1],
                margin_after=self.position_margin,
                marked_equity_after=self.marked_equity,
            )
        if self.auto_match:
            return []
        if fill.offset is Offset.OPEN:
            return self._refresh_live_protection(fill.datetime)
        if self.position is not None:
            return self._refresh_live_protection(fill.datetime)
        return self._cancel_live_protection(
            protection_ids,
            fill.datetime,
            exclude_order_id=order.order_id,
        )

    def on_order(self, event: BrokerOrderEvent) -> list[OrderIntent]:
        if event.event_id in self._seen_order_events:
            return []
        self._seen_order_events.add(event.event_id)
        order = self._find_order(event.order_id)
        if order is None:
            raise ValueError(f"unknown broker order event: {event.order_id}")
        order.broker_reported_traded_quantity = max(
            order.broker_reported_traded_quantity,
            int(event.traded_quantity),
        )
        self._set_order_status(
            order,
            event.status,
            event.datetime,
            source="broker_order",
        )
        return []

    def on_trade(self, event: BrokerTradeEvent) -> list[OrderIntent]:
        if event.trade_id in self._seen_trade_ids:
            return []
        self._seen_trade_ids.add(event.trade_id)
        return self.on_fill(
            Fill(
                fill_id=event.trade_id,
                order_id=event.order_id,
                candidate_id=event.candidate_id,
                symbol=event.symbol,
                contract_code=event.contract_code,
                datetime=event.datetime,
                direction=event.direction,
                offset=event.offset,
                quantity=event.quantity,
                reference_price=event.reference_price,
                fill_price=event.fill_price,
                source_bar_end=event.source_bar_end,
                exchange_trade_date=event.exchange_trade_date,
            )
        )

    def snapshot(self) -> EngineSnapshot:
        return EngineSnapshot(
            orders=tuple(self.orders),
            order_events=tuple(self.order_events),
            position=self.position,
            fills=tuple(self.fills),
            closed_trades=tuple(self.closed_trades),
            marked_equity=self.marked_equity,
            realized_gross_pnl=self.realized_gross_pnl,
            paid_fees=self.paid_fees,
            estimated_exit_fee=self.estimated_exit_fee,
            margin_mark_price=self.margin_mark_price,
            position_margin=self.position_margin,
            working_order_margin_reserve=self.working_order_margin_reserve,
            margin_usage=self.margin_usage,
            available_funds=self.available_funds,
            forced_flat_pending=self.forced_flat_pending,
            forced_flat_failed=self.forced_flat_failed,
            pending_exit_reason=self.pending_exit_reason,
        )

    def switch_contract_metadata(
        self,
        instrument: InstrumentSpec,
        fee_spec: FeeMarginSpec,
    ) -> None:
        """Activate a new real contract only after the previous session is flat."""
        if self.position is not None:
            raise ValueError("cannot switch contract metadata with an open position")
        if instrument.root_symbol != self.instrument.root_symbol:
            raise ValueError("contract switch root_symbol mismatch")
        if instrument.exchange != self.instrument.exchange:
            raise ValueError("contract switch exchange mismatch")
        if fee_spec.root_symbol != instrument.root_symbol:
            raise ValueError("fee schedule root_symbol mismatch")
        for order in self.orders:
            if _active(order.status):
                self._set_order_status(
                    order,
                    OrderStatus.CANCELLED,
                    self._last_bar_end or order.created_at,
                    source="contract_switch",
                )
        self.instrument = instrument
        self.fee_spec = fee_spec
        self.pending_exit_reason = None
        self.forced_flat_pending = False

    def request_exit(self, reason: ExitReason) -> list[OrderIntent]:
        """Cancel entry risk and schedule any open position for the next bar open."""
        intents: list[OrderIntent] = []
        timestamp = self._last_bar_end or (
            self._current_bar.bar_end if self._current_bar is not None else None
        )
        for order in self.orders:
            if order.order_type is OrderType.ENTRY_STOP and _active(order.status):
                intents.extend(
                    self._cancel_entry_order(
                        order,
                        timestamp or order.created_at,
                        source="risk_exit",
                    )
                )
        if self.position is not None:
            self.pending_exit_reason = reason
        return intents

    def request_immediate_exit(self, reason: ExitReason) -> list[OrderIntent]:
        """Cancel entry risk and submit an exit using the latest completed bar."""
        intents = self.request_exit(reason)
        if self.position is None or self._current_bar is None:
            return intents
        exits = self._attempt_market_exit(
            self._current_bar,
            reason,
            at_open=False,
        )
        intents.extend(exits)
        if exits:
            self.pending_exit_reason = None
        return intents

    def preview_entry_trigger(self, bar: MinuteBar) -> EntryTriggerPreview | None:
        """Price an eligible synthetic stop-entry without changing order state."""
        if self._current_bar is None or self._current_bar.bar_end != bar.bar_end:
            raise ValueError("entry preview must follow the same bar exit phase")
        cutoff = bar.session_close - pd.Timedelta(minutes=15)
        if bar.bar_start >= cutoff:
            return None
        for order in self.orders:
            if order.order_type is not OrderType.ENTRY_STOP or not _active(order.status):
                continue
            if bar.bar_start < order.eligible_after:
                continue
            triggered = (order.direction > 0 and bar.high >= order.price) or (
                order.direction < 0 and bar.low <= order.price
            )
            gap_trigger = (order.direction > 0 and bar.open >= order.price) or (
                order.direction < 0 and bar.open <= order.price
            )
            if not triggered or not self._can_execute(
                order.direction,
                bar,
                at_open=gap_trigger,
            ):
                continue
            if order.structural_stop is None:
                raise ValueError("entry order is missing structural stop")
            reference = (
                max(order.price, bar.open)
                if order.direction > 0
                else min(order.price, bar.open)
            )
            fill_price = self._execution_price(reference, order.direction, bar)
            stop_fill = self._execution_price(
                order.structural_stop,
                -order.direction,
                bar,
            )
            open_fee = calculate_fee(
                self.fee_spec,
                Offset.OPEN,
                fill_price,
                1,
                contract_size=self.instrument.contract_size,
            ).total_fee
            stop_fee = calculate_fee(
                self.fee_spec,
                Offset.CLOSETODAY,
                stop_fill,
                1,
                contract_size=self.instrument.contract_size,
            ).total_fee
            stop_loss = (
                abs(fill_price - stop_fill) * self.instrument.contract_size
                + open_fee
                + stop_fee
            )
            reserved_open_risk = (
                order.net_stop_loss_per_contract * order.remaining_quantity
                if order.net_stop_loss_per_contract is not None
                else abs(order.price - order.structural_stop)
                * self.instrument.contract_size
                * order.remaining_quantity
            )
            return EntryTriggerPreview(
                order_id=order.order_id,
                remaining_quantity=order.remaining_quantity,
                reference_price=reference,
                fill_price=fill_price,
                net_stop_loss_per_contract=stop_loss,
                reserved_open_risk=reserved_open_risk,
            )
        return None

    def resize_entry_order(
        self,
        order_id: str,
        remaining_quantity: int,
        *,
        net_stop_loss_per_contract: float | None = None,
    ) -> None:
        """Reduce a synthetic entry before submission; increasing it is forbidden."""
        order = self._find_order(order_id)
        if order is None or order.order_type is not OrderType.ENTRY_STOP:
            raise ValueError(f"unknown entry order: {order_id}")
        if not _active(order.status):
            raise ValueError("entry order is not active")
        if remaining_quantity < 0 or remaining_quantity > order.remaining_quantity:
            raise ValueError("entry resize must not increase the remaining quantity")
        if net_stop_loss_per_contract is not None:
            if not _positive(net_stop_loss_per_contract):
                raise ValueError("net_stop_loss_per_contract must be positive")
            order.net_stop_loss_per_contract = float(net_stop_loss_per_contract)
        if remaining_quantity == 0:
            self._set_order_status(
                order,
                OrderStatus.CANCELLED,
                self._current_bar.bar_end if self._current_bar is not None else order.created_at,
                source="trigger_resize",
            )
            return
        order.quantity = order.traded_quantity + int(remaining_quantity)

    def _process_entries(self, bar: MinuteBar) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        cutoff = bar.session_close - pd.Timedelta(minutes=15)
        for order in self.orders:
            if order.order_type is not OrderType.ENTRY_STOP or not _active(order.status):
                continue
            if bar.bar_start < order.eligible_after:
                continue
            if bar.bar_start >= cutoff:
                intents.extend(
                    self._cancel_entry_order(
                        order,
                        bar.bar_end,
                        source="session_cutoff",
                    )
                )
                continue
            if order.status is not OrderStatus.CANCEL_PENDING:
                order.eligible_bars_seen += 1
            if order.status is OrderStatus.WORKING:
                triggered = (
                    order.direction > 0 and bar.high >= order.price
                ) or (
                    order.direction < 0 and bar.low <= order.price
                )
                gap_trigger = (order.direction > 0 and bar.open >= order.price) or (
                    order.direction < 0 and bar.open <= order.price
                )
                if triggered and self._can_execute(
                    order.direction,
                    bar,
                    at_open=gap_trigger,
                ):
                    reference = (
                        max(order.price, bar.open)
                        if order.direction > 0
                        else min(order.price, bar.open)
                    )
                    fill_price = self._execution_price(reference, order.direction, bar)
                    intents.append(self._intent(order, bar.bar_end))
                    if self.auto_match:
                        self.on_fill(
                            self._new_fill(
                                order,
                                bar,
                                reference,
                                fill_price,
                                order.remaining_quantity,
                            )
                        )
                        if self.position is not None:
                            intents.extend(self._match_existing_position(bar, new_entry=True))
                    else:
                        self._set_order_status(
                            order,
                            OrderStatus.SUBMITTED,
                            bar.bar_end,
                            source="broker_submit",
                        )
            if order.eligible_bars_seen >= order.expires_after_bars and _active(order.status):
                if order.status is OrderStatus.WORKING:
                    self._set_order_status(
                        order,
                        OrderStatus.EXPIRED,
                        bar.bar_end,
                        source="entry_expiry",
                    )
                else:
                    intents.extend(
                        self._cancel_entry_order(
                            order,
                            bar.bar_end,
                            source="entry_expiry",
                        )
                    )
        return intents

    def _cancel_entry_order(
        self,
        order: WorkingOrder,
        timestamp: datetime,
        *,
        source: str,
    ) -> list[OrderIntent]:
        if order.order_type is not OrderType.ENTRY_STOP or not _active(order.status):
            return []
        if order.status in {
            OrderStatus.SUBMITTED,
            OrderStatus.PARTIALLY_FILLED,
        }:
            intent = replace(
                self._intent(order, timestamp),
                quantity=0,
                action=OrderAction.CANCEL,
                reason=source,
            )
            self._set_order_status(
                order,
                OrderStatus.CANCEL_PENDING,
                timestamp,
                source=source,
            )
            return [intent]
        if order.status is not OrderStatus.CANCEL_PENDING:
            self._set_order_status(
                order,
                OrderStatus.CANCELLED,
                timestamp,
                source=source,
            )
        return []

    def _match_existing_position(
        self,
        bar: MinuteBar,
        *,
        new_entry: bool = False,
    ) -> list[OrderIntent]:
        position = self.position
        if position is None:
            return []
        stop_order = self._find_order(position.stop_order_id)
        target_order = self._find_order(position.target_order_id)
        if stop_order is None or target_order is None:
            raise RuntimeError("position is missing protection orders")
        direction = position.direction
        gap_stop = (direction > 0 and bar.open < position.initial_stop) or (
            direction < 0 and bar.open > position.initial_stop
        )
        stop_hit = (direction > 0 and bar.low <= position.initial_stop) or (
            direction < 0 and bar.high >= position.initial_stop
        )
        target_hit = (direction > 0 and bar.high >= position.planned_target) or (
            direction < 0 and bar.low <= position.planned_target
        )
        if gap_stop:
            return self._execute_protection(
                stop_order,
                bar,
                reference=bar.open,
                reason=ExitReason.GAP_STOP,
                at_open=True,
            )
        if stop_hit:
            return self._execute_protection(
                stop_order,
                bar,
                reference=position.initial_stop,
                reason=ExitReason.HARD_STOP,
            )
        if target_hit:
            return self._execute_protection(
                target_order,
                bar,
                reference=position.planned_target,
                reason=ExitReason.PROFIT_TARGET,
            )
        del new_entry
        return []

    def _execute_protection(
        self,
        order: WorkingOrder,
        bar: MinuteBar,
        *,
        reference: float,
        reason: ExitReason,
        at_open: bool = False,
    ) -> list[OrderIntent]:
        if self.position is None or not _active(order.status):
            return []
        if not self.auto_match and order.order_id in self._live_protection_dispatched:
            return []
        side = -self.position.direction
        if not self._can_execute(side, bar, at_open=at_open):
            if reason is ExitReason.FORCED_SESSION_FLAT:
                self.forced_flat_pending = True
            return []
        order.offset = self._close_offset(self.position)
        order.exit_reason = reason
        fill_price = self._execution_price(reference, side, bar)
        intent = self._intent(order, bar.bar_end)
        if self.auto_match:
            self.on_fill(
                self._new_fill(
                    order,
                    bar,
                    reference,
                    fill_price,
                    min(order.remaining_quantity, self.position.quantity),
                )
            )
        return [intent]

    def _attempt_market_exit(
        self,
        bar: MinuteBar,
        reason: ExitReason,
        *,
        at_open: bool,
    ) -> list[OrderIntent]:
        if self.position is None:
            self.forced_flat_pending = False
            return []
        side = -self.position.direction
        if not self._can_execute(side, bar, at_open=at_open):
            if reason is ExitReason.FORCED_SESSION_FLAT:
                self.forced_flat_pending = True
            return []
        reference = bar.open if at_open else bar.close
        fill_price = self._execution_price(reference, side, bar)
        intents: list[OrderIntent] = []
        for order in self._market_exit_orders(bar, reason):
            if not self.auto_match and order.status in {
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.CANCEL_PENDING,
            }:
                continue
            intent = self._intent(order, bar.bar_end)
            intents.append(intent)
            if self.auto_match and self.position is not None:
                self.on_fill(
                    self._new_fill(
                        order,
                        bar,
                        reference,
                        fill_price,
                        min(order.remaining_quantity, self.position.quantity),
                    )
                )
            elif not self.auto_match:
                self._set_order_status(
                    order,
                    OrderStatus.SUBMITTED,
                    bar.bar_end,
                    source="broker_submit",
                )
        self.forced_flat_pending = False
        return intents

    def _market_exit_orders(
        self,
        bar: MinuteBar,
        reason: ExitReason,
    ) -> list[WorkingOrder]:
        assert self.position is not None
        inventory = (
            (Offset.CLOSETODAY, self.position.today_quantity),
            (Offset.CLOSEYESTERDAY, self.position.yesterday_quantity),
        )
        result: list[WorkingOrder] = []
        for offset, quantity in inventory:
            if quantity <= 0:
                continue
            existing = next(
                (
                    order
                    for order in self.orders
                    if order.order_type is OrderType.MARKET_EXIT
                    and order.exit_reason is reason
                    and order.offset is offset
                    and _active(order.status)
                ),
                None,
            )
            if existing is not None:
                result.append(existing)
                continue
            order = WorkingOrder(
                order_id=self._next_order_id(
                    self.position.candidate_id,
                    f"{reason.value}-{offset.value}",
                ),
                candidate_id=self.position.candidate_id,
                symbol=self.position.symbol,
                contract_code=self.position.contract_code,
                direction=-self.position.direction,
                offset=offset,
                quantity=quantity,
                price=bar.open,
                order_type=OrderType.MARKET_EXIT,
                status=OrderStatus.WORKING,
                created_at=bar.bar_start,
                eligible_after=bar.bar_start,
                expires_after_bars=0,
                reduce_only=True,
                exit_reason=reason,
            )
            self._append_order(order, bar.bar_start, source="strategy")
            result.append(order)
        return result

    def _apply_entry_fill(self, order: WorkingOrder, fill: Fill) -> None:
        if order.structural_stop is None or order.target_price is None:
            raise ValueError("entry order is missing structural stop or target")
        stop = float(order.structural_stop)
        target = float(order.target_price)
        stop_fill = self._adverse_price(stop, -order.direction)
        stop_fee = calculate_fee(
            self.fee_spec,
            Offset.CLOSETODAY,
            stop_fill,
            1,
            contract_size=self.instrument.contract_size,
        ).total_fee
        net_stop_loss = (
            abs(fill.fill_price - stop_fill) * self.instrument.contract_size
            + fill.total_fee / fill.quantity
            + stop_fee
        )
        if self.position is None:
            stop_id = self._next_order_id(order.candidate_id, "stop")
            target_id = self._next_order_id(order.candidate_id, "target")
            self.position = PositionState(
                candidate_id=order.candidate_id,
                symbol=order.symbol,
                contract_code=order.contract_code,
                direction=order.direction,
                quantity=fill.quantity,
                initial_quantity=fill.quantity,
                entry_time=fill.datetime,
                entry_price=fill.fill_price,
                total_entry_notional=fill.fill_price * fill.quantity,
                total_exit_notional=0.0,
                exited_quantity=0,
                entry_trigger=order.price,
                initial_stop=stop,
                planned_target=target,
                initial_net_stop_loss_per_contract=net_stop_loss,
                entry_fee=fill.total_fee,
                exit_fee=0.0,
                total_slippage=fill.slippage,
                today_quantity=fill.quantity,
                yesterday_quantity=0,
                entry_trade_date=fill.exchange_trade_date,
                holding_1m_bars=0,
                mfe_price=fill.fill_price,
                mae_price=fill.fill_price,
                stop_order_id=stop_id,
                target_order_id=target_id,
            )
            for protection in (
                self._protection_order(stop_id, stop, OrderType.HARD_STOP),
                self._protection_order(target_id, target, OrderType.PROFIT_TARGET),
            ):
                self._append_order(protection, fill.datetime, source="strategy")
        else:
            position = self.position
            old_quantity = position.quantity
            new_quantity = old_quantity + fill.quantity
            position.entry_price = (
                position.entry_price * old_quantity + fill.fill_price * fill.quantity
            ) / new_quantity
            position.quantity = new_quantity
            position.initial_quantity += fill.quantity
            position.total_entry_notional += fill.fill_price * fill.quantity
            position.today_quantity += fill.quantity
            position.entry_fee += fill.total_fee
            position.total_slippage += fill.slippage
            position.initial_net_stop_loss_per_contract = (
                position.initial_net_stop_loss_per_contract * old_quantity
                + net_stop_loss * fill.quantity
            ) / new_quantity
            self._find_order(position.stop_order_id).quantity = new_quantity  # type: ignore[union-attr]
            self._find_order(position.target_order_id).quantity = new_quantity  # type: ignore[union-attr]

    def _protection_order(
        self,
        order_id: str,
        price: float,
        order_type: OrderType,
    ) -> WorkingOrder:
        assert self.position is not None
        return WorkingOrder(
            order_id=order_id,
            candidate_id=self.position.candidate_id,
            symbol=self.position.symbol,
            contract_code=self.position.contract_code,
            direction=-self.position.direction,
            offset=Offset.CLOSETODAY,
            quantity=self.position.quantity,
            price=price,
            order_type=order_type,
            status=OrderStatus.WORKING,
            created_at=self.position.entry_time,
            eligible_after=self.position.entry_time,
            expires_after_bars=0,
            reduce_only=True,
            exit_reason=(
                ExitReason.HARD_STOP
                if order_type is OrderType.HARD_STOP
                else ExitReason.PROFIT_TARGET
            ),
        )

    def _apply_exit_fill(self, order: WorkingOrder, fill: Fill) -> None:
        position = self.position
        if position is None:
            raise ValueError("exit fill without a position")
        if fill.quantity > position.quantity:
            raise ValueError("exit fill exceeds position")
        if fill.offset is Offset.CLOSETODAY and fill.quantity > position.today_quantity:
            raise ValueError("close-today fill exceeds today inventory")
        if (
            fill.offset is Offset.CLOSEYESTERDAY
            and fill.quantity > position.yesterday_quantity
        ):
            raise ValueError("close-yesterday fill exceeds yesterday inventory")
        gross = (
            position.direction
            * (fill.fill_price - position.entry_price)
            * self.instrument.contract_size
            * fill.quantity
        )
        self.realized_gross_pnl += gross
        position.total_exit_notional += fill.fill_price * fill.quantity
        position.exited_quantity += fill.quantity
        position.exit_fee += fill.total_fee
        position.total_slippage += fill.slippage
        if fill.offset is Offset.CLOSETODAY:
            position.today_quantity -= fill.quantity
        elif fill.offset is Offset.CLOSEYESTERDAY:
            position.yesterday_quantity -= fill.quantity
        position.quantity -= fill.quantity
        sibling_id = (
            position.target_order_id
            if order.order_id == position.stop_order_id
            else position.stop_order_id
        )
        sibling = self._find_order(sibling_id)
        if position.quantity > 0:
            if sibling is not None:
                sibling.quantity = position.quantity
            return
        for protection_id in (position.stop_order_id, position.target_order_id):
            protection = self._find_order(protection_id)
            if protection is not None and _active(protection.status):
                self._set_order_status(
                    protection,
                    OrderStatus.CANCELLED,
                    fill.datetime,
                    source="oco",
                )
        exit_reason = order.exit_reason or (
            ExitReason.HARD_STOP
            if order.order_type is OrderType.HARD_STOP
            else ExitReason.PROFIT_TARGET
        )
        total_entry_price = position.total_entry_notional / position.initial_quantity
        total_exit_price = position.total_exit_notional / position.exited_quantity
        total_gross = (
            position.direction
            * (position.total_exit_notional - position.total_entry_notional)
            * self.instrument.contract_size
        )
        risk_cny = (
            position.initial_net_stop_loss_per_contract
            * position.initial_quantity
        )
        net = total_gross - position.entry_fee - position.exit_fee
        mfe = (
            position.direction * (position.mfe_price - total_entry_price)
            * self.instrument.contract_size
            * position.initial_quantity
        )
        mae = (
            position.direction * (position.mae_price - total_entry_price)
            * self.instrument.contract_size
            * position.initial_quantity
        )
        self.closed_trades.append(
            ClosedTrade(
                trade_id=f"trade-{order.candidate_id}-{len(self.closed_trades) + 1}",
                candidate_id=order.candidate_id,
                symbol=position.symbol,
                contract_code=position.contract_code,
                direction=position.direction,
                entry_time=position.entry_time,
                entry_price=total_entry_price,
                quantity=position.initial_quantity,
                initial_risk_cny=risk_cny,
                initial_stop=position.initial_stop,
                planned_target=position.planned_target,
                exit_time=fill.datetime,
                exit_price=total_exit_price,
                exit_reason=exit_reason,
                gross_pnl=total_gross,
                total_fee=position.entry_fee + position.exit_fee,
                total_slippage=position.total_slippage,
                net_pnl=net,
                net_r=net / risk_cny if risk_cny > 0 else 0.0,
                mfe_r=mfe / risk_cny if risk_cny > 0 else 0.0,
                mae_r=mae / risk_cny if risk_cny > 0 else 0.0,
                holding_1m_bars=position.holding_1m_bars,
                exchange_trade_date=fill.exchange_trade_date,
            )
        )
        self.position = None
        self.pending_exit_reason = None
        self.forced_flat_pending = False

    def _refresh_live_protection(self, timestamp: datetime) -> list[OrderIntent]:
        position = self.position
        if position is None:
            return []
        current_ids = (position.stop_order_id, position.target_order_id)
        intents: list[OrderIntent] = []
        if any(order_id in self._live_protection_dispatched for order_id in current_ids):
            intents.extend(self._cancel_live_protection(current_ids, timestamp))
            stop_id = self._next_order_id(position.candidate_id, "stop-live-replace")
            target_id = self._next_order_id(position.candidate_id, "target-live-replace")
            position.stop_order_id = stop_id
            position.target_order_id = target_id
            for protection in (
                self._protection_order(
                    stop_id,
                    position.initial_stop,
                    OrderType.HARD_STOP,
                ),
                self._protection_order(
                    target_id,
                    position.planned_target,
                    OrderType.PROFIT_TARGET,
                ),
            ):
                self._append_order(protection, timestamp, source="strategy")
            current_ids = (stop_id, target_id)
        for order_id in current_ids:
            order = self._find_order(order_id)
            if order is None:
                raise RuntimeError("position is missing live protection order")
            intents.append(self._intent(order, timestamp))
            self._live_protection_dispatched.add(order_id)
            if order.status is OrderStatus.WORKING:
                self._set_order_status(
                    order,
                    OrderStatus.SUBMITTED,
                    timestamp,
                    source="broker_submit",
                )
        return intents

    def _cancel_live_protection(
        self,
        order_ids: tuple[str, ...],
        timestamp: datetime,
        *,
        exclude_order_id: str | None = None,
    ) -> list[OrderIntent]:
        intents: list[OrderIntent] = []
        for order_id in order_ids:
            if order_id == exclude_order_id:
                self._live_protection_dispatched.discard(order_id)
                continue
            if order_id not in self._live_protection_dispatched:
                continue
            order = self._find_order(order_id)
            if order is None:
                continue
            intents.append(
                replace(
                    self._intent(order, timestamp),
                    quantity=0,
                    action=OrderAction.CANCEL,
                )
            )
            if _active(order.status):
                self._set_order_status(
                    order,
                    OrderStatus.CANCEL_PENDING,
                    timestamp,
                    source="live_protection_replace",
                )
            self._live_protection_dispatched.discard(order_id)
        return intents

    def _update_position_path(self, bar: MinuteBar) -> None:
        assert self.position is not None
        if self.position.direction > 0:
            self.position.mfe_price = max(self.position.mfe_price, bar.high)
            self.position.mae_price = min(self.position.mae_price, bar.low)
        else:
            self.position.mfe_price = min(self.position.mfe_price, bar.low)
            self.position.mae_price = max(self.position.mae_price, bar.high)

    def _schedule_close_exit(self) -> None:
        assert self.position is not None
        position = self.position
        if position.holding_1m_bars == self.no_follow_through_bars:
            risk = abs(position.entry_trigger - position.initial_stop)
            mfe = (
                position.mfe_price - position.entry_trigger
                if position.direction > 0
                else position.entry_trigger - position.mfe_price
            )
            close = self._current_bar.close if self._current_bar else position.entry_price
            failed = (
                position.direction > 0 and close <= position.entry_trigger
            ) or (
                position.direction < 0 and close >= position.entry_trigger
            )
            if mfe < 0.25 * risk and failed:
                self.pending_exit_reason = ExitReason.NO_FOLLOW_THROUGH_SCRATCH
        if position.holding_1m_bars >= self.max_holding_bars:
            self.pending_exit_reason = ExitReason.MAX_HOLDING_TIME

    def _mark(self, bar: MinuteBar) -> None:
        unrealized = 0.0
        estimated_exit = 0.0
        self.margin_mark_price = 0.0
        self.position_margin = 0.0
        if self.position is not None:
            position = self.position
            conservative = self._execution_price(bar.close, -position.direction, bar)
            unrealized = (
                position.direction
                * (conservative - position.entry_price)
                * self.instrument.contract_size
                * position.quantity
            )
            close_offset = self._close_offset(position)
            estimated_exit = calculate_fee(
                self.fee_spec,
                close_offset,
                conservative,
                position.quantity,
                contract_size=self.instrument.contract_size,
            ).total_fee
            self.margin_mark_price = max(
                bar.close, position.entry_price, bar.pre_settlement
            )
            margin_rate = (
                self.fee_spec.margin_rate_long
                if position.direction > 0
                else self.fee_spec.margin_rate_short
            )
            self.position_margin = (
                position.quantity
                * self.instrument.contract_size
                * self.margin_mark_price
                * margin_rate
            )
        reserve = 0.0
        for order in self.orders:
            if order.order_type is not OrderType.ENTRY_STOP or not _active(order.status):
                continue
            mark = max(order.price, bar.close, bar.pre_settlement)
            rate = (
                self.fee_spec.margin_rate_long
                if order.direction > 0
                else self.fee_spec.margin_rate_short
            )
            reserve += (
                order.remaining_quantity
                * self.instrument.contract_size
                * mark
                * rate
            )
        self.working_order_margin_reserve = reserve
        self.estimated_exit_fee = estimated_exit
        self.marked_equity = (
            self.initial_cash
            + self.realized_gross_pnl
            - self.paid_fees
            + unrealized
            - estimated_exit
        )
        margin = self.position_margin + reserve
        self.margin_usage = margin / self.marked_equity if self.marked_equity > 0 else float("inf")
        self.available_funds = self.marked_equity - margin

    def _roll_inventory(self, trade_date: date) -> None:
        if self.position is None or trade_date == self.position.entry_trade_date:
            return
        self.position.yesterday_quantity += self.position.today_quantity
        self.position.today_quantity = 0
        self.position.entry_trade_date = trade_date
        stop = self._find_order(self.position.stop_order_id)
        target = self._find_order(self.position.target_order_id)
        if stop is not None:
            stop.offset = Offset.CLOSEYESTERDAY
        if target is not None:
            target.offset = Offset.CLOSEYESTERDAY

    def _close_offset(self, position: PositionState) -> Offset:
        return Offset.CLOSETODAY if position.today_quantity > 0 else Offset.CLOSEYESTERDAY

    def _can_execute(
        self,
        side: int,
        bar: MinuteBar,
        *,
        at_open: bool = False,
    ) -> bool:
        price = bar.open if at_open else bar.close
        tolerance = max(1e-10, self.instrument.price_tick * 1e-9)
        if side > 0 and price >= bar.limit_up - tolerance:
            return False
        return not (side < 0 and price <= bar.limit_down + tolerance)

    def _adverse_price(self, reference: float, side: int) -> float:
        raw = reference + side * self.instrument.slippage_ticks_base * self.instrument.price_tick
        if side > 0:
            return _round_up(raw, self.instrument.price_tick)
        return _round_down(raw, self.instrument.price_tick)

    def _execution_price(self, reference: float, side: int, bar: MinuteBar) -> float:
        adverse = self._adverse_price(reference, side)
        if side > 0:
            return min(adverse, bar.limit_up)
        return max(adverse, bar.limit_down)

    def _new_fill(
        self,
        order: WorkingOrder,
        bar: MinuteBar,
        reference: float,
        fill_price: float,
        quantity: int,
    ) -> Fill:
        self._fill_sequence += 1
        return Fill(
            fill_id=f"fill-{self._fill_sequence:08d}",
            order_id=order.order_id,
            candidate_id=order.candidate_id,
            symbol=order.symbol,
            contract_code=order.contract_code,
            datetime=bar.bar_end,
            direction=order.direction,
            offset=order.offset,
            quantity=quantity,
            reference_price=reference,
            fill_price=fill_price,
            source_bar_end=bar.bar_end,
            exchange_trade_date=bar.exchange_trade_date,
        )

    def _intent(self, order: WorkingOrder, timestamp: datetime) -> OrderIntent:
        return OrderIntent(
            order_id=order.order_id,
            candidate_id=order.candidate_id,
            symbol=order.symbol,
            contract_code=order.contract_code,
            datetime=timestamp,
            direction=order.direction,
            offset=order.offset,
            quantity=order.remaining_quantity,
            order_type=order.order_type,
            price=order.price,
            reduce_only=order.reduce_only,
            action=OrderAction.SUBMIT,
            reason=order.exit_reason.value if order.exit_reason else "",
        )

    def _append_order(
        self,
        order: WorkingOrder,
        timestamp: datetime,
        *,
        source: str,
    ) -> None:
        self.orders.append(order)
        self._record_order_event(order, timestamp, source=source)

    def _set_order_status(
        self,
        order: WorkingOrder,
        status: OrderStatus,
        timestamp: datetime,
        *,
        source: str,
    ) -> None:
        order.status = status
        self._record_order_event(order, timestamp, source=source)

    def _record_order_event(
        self,
        order: WorkingOrder,
        timestamp: datetime,
        *,
        source: str,
    ) -> None:
        self._order_event_sequence += 1
        event_key = (
            f"{order.symbol}|{order.order_id}|{self._order_event_sequence}|"
            f"{timestamp.isoformat()}|{source}|{order.status.value}"
        )
        self.order_events.append(
            OrderLifecycleEvent(
                event_id=f"OE-{hashlib.sha256(event_key.encode()).hexdigest()[:20]}",
                order_id=order.order_id,
                status=order.status,
                datetime=timestamp,
                traded_quantity=order.traded_quantity,
                broker_reported_traded_quantity=order.broker_reported_traded_quantity,
                source=source,
            )
        )

    def _next_order_id(self, candidate_id: str, purpose: str) -> str:
        self._order_sequence += 1
        raw = f"{candidate_id}|{purpose}|{self._order_sequence}"
        return f"O-{hashlib.sha256(raw.encode()).hexdigest()[:16]}"

    def _find_order(self, order_id: str) -> WorkingOrder | None:
        return next((order for order in self.orders if order.order_id == order_id), None)

    def _validate_bar(self, bar: MinuteBar) -> None:
        if bar.contract_code != self.fee_spec.contract_code and self.fee_spec.contract_code:
            raise ValueError("bar contract does not match fee schedule")
        if self._last_bar_end is not None and bar.bar_end <= self._last_bar_end:
            raise ValueError("bars must be strictly ordered")
        if not (
            bar.low <= min(bar.open, bar.close) <= max(bar.open, bar.close) <= bar.high
        ):
            raise ValueError("invalid bar OHLC")


def _active(status: OrderStatus) -> bool:
    return status in {
        OrderStatus.CREATED,
        OrderStatus.RISK_APPROVED,
        OrderStatus.WORKING,
        OrderStatus.SUBMITTED,
        OrderStatus.PARTIALLY_FILLED,
        OrderStatus.CANCEL_PENDING,
    }


def _same_price(left: float, right: float, tick: float) -> bool:
    return abs(left - right) <= max(1e-10, tick * 1e-9)


def _round_up(value: float, tick: float) -> float:
    return round(math.ceil((value - 1e-12) / tick) * tick, 10)


def _round_down(value: float, tick: float) -> float:
    return round(math.floor((value + 1e-12) / tick) * tick, 10)


def _positive(value: float) -> bool:
    return math.isfinite(float(value)) and value > 0


__all__ = [
    "BrokerOrderEvent",
    "BrokerTradeEvent",
    "ClosedTrade",
    "EngineSnapshot",
    "EntryTriggerPreview",
    "ExitReason",
    "FeeBreakdown",
    "Fill",
    "Offset",
    "OrderIntent",
    "OrderAction",
    "OrderLifecycleEvent",
    "OrderStatus",
    "OrderType",
    "PositionState",
    "RejectReason",
    "ScalpEngine",
    "TradePlan",
    "calculate_fee",
    "plan_target_price",
]
