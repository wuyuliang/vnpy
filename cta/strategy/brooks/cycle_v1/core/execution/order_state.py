"""Auditable order lifecycle independent of setup pattern state."""
from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
import math

from ..types import EventKey, OrderPlan, OrderStatus


@dataclass(frozen=True)
class OrderFill:
    event: EventKey
    quantity: int
    price: float
    reference_price: float | None = None

    def __post_init__(self) -> None:
        if self.quantity <= 0 or not math.isfinite(self.price) or self.price <= 0:
            raise ValueError("fill quantity and price must be finite and positive")
        if self.reference_price is not None and (
            not math.isfinite(self.reference_price) or self.reference_price <= 0
        ):
            raise ValueError("fill reference price must be finite and positive")


class OrderStateMachine:
    def __init__(self, plan: OrderPlan) -> None:
        if plan.quantity <= 0:
            raise ValueError("order quantity must be positive")
        if plan.status is not OrderStatus.PLANNED:
            raise ValueError("new order state requires a planned order")
        self.plan = plan
        self.status = OrderStatus.PLANNED
        self.active_event: EventKey | None = None
        self.fills: list[OrderFill] = []
        self.filled_quantity = 0
        self.cancel_reason = ""

    @property
    def remaining_quantity(self) -> int:
        return self.plan.quantity - self.filled_quantity

    def activate(self, event: EventKey) -> None:
        if self.status is not OrderStatus.PLANNED:
            raise ValueError("only planned orders can activate")
        if event < self.plan.candidate.active_event:
            raise ValueError("activation precedes candidate active event")
        if event >= self.plan.expires_at:
            raise ValueError("cannot activate an expired order")
        self.active_event = event
        self.status = OrderStatus.ACTIVE

    def fill(self, event: EventKey, *, quantity: int, price: float) -> None:
        if self.status not in {OrderStatus.ACTIVE, OrderStatus.PARTIAL}:
            raise ValueError("only active orders can fill")
        assert self.active_event is not None
        if event < self.active_event:
            raise ValueError("fill event is before active event")
        if event >= self.plan.expires_at:
            raise ValueError("fill event is after expiry")
        if quantity <= 0 or quantity > self.remaining_quantity:
            raise ValueError("invalid fill quantity")
        if not math.isfinite(price) or price <= 0:
            raise ValueError("fill price must be finite and positive")
        self.fills.append(OrderFill(event, quantity, float(price)))
        self.filled_quantity += quantity
        self.status = (
            OrderStatus.FILLED
            if self.filled_quantity == self.plan.quantity
            else OrderStatus.PARTIAL
        )

    def cancel(self, event: EventKey, *, reason: str) -> None:
        if self.status not in {OrderStatus.PLANNED, OrderStatus.ACTIVE, OrderStatus.PARTIAL}:
            raise ValueError("order cannot be cancelled from its current state")
        if not reason:
            raise ValueError("cancel reason is required")
        if self.active_event is not None and event < self.active_event:
            raise ValueError("cancel event precedes activation")
        self.cancel_reason = reason
        self.status = OrderStatus.CANCELLED

    def expire(self, event: EventKey) -> None:
        if self.status not in {OrderStatus.PLANNED, OrderStatus.ACTIVE, OrderStatus.PARTIAL}:
            raise ValueError("order cannot expire from its current state")
        if event < self.plan.expires_at:
            raise ValueError("expiry event precedes planned expiry")
        self.status = OrderStatus.EXPIRED


class OcoOrderCoordinator:
    """Cancel the opposite stop immediately after the first actual fill."""

    def __init__(self, plans: Sequence[OrderPlan]) -> None:
        if len(plans) != 2:
            raise ValueError("OCO requires exactly two orders")
        group_ids = {plan.oco_group_id for plan in plans}
        directions = {plan.candidate.direction for plan in plans}
        symbols = {plan.candidate.vt_symbol for plan in plans}
        contracts = {plan.candidate.contract_code for plan in plans}
        order_ids = {plan.order_id for plan in plans}
        if None in group_ids or len(group_ids) != 1:
            raise ValueError("OCO orders require one shared group id")
        if directions != {-1, 1} or len(symbols) != 1 or len(contracts) != 1:
            raise ValueError("OCO orders require opposite directions on one actual contract")
        if len(order_ids) != 2:
            raise ValueError("OCO order ids must be unique")
        synchronization = {
            (plan.candidate.active_event, plan.expires_at, plan.metadata_hash)
            for plan in plans
        }
        if len(synchronization) != 1:
            raise ValueError(
                "OCO orders require synchronized activation, expiry, and metadata"
            )
        self.orders = {plan.order_id: OrderStateMachine(plan) for plan in plans}

    def activate(self, event: EventKey) -> None:
        for order in self.orders.values():
            order.activate(event)

    def fill(self, order_id: str, event: EventKey, *, quantity: int, price: float) -> None:
        if order_id not in self.orders:
            raise KeyError(f"unknown OCO order: {order_id}")
        filled = self.orders[order_id]
        filled.fill(event, quantity=quantity, price=price)
        for peer_id, peer in self.orders.items():
            if peer_id == order_id:
                continue
            if peer.status in {OrderStatus.PLANNED, OrderStatus.ACTIVE, OrderStatus.PARTIAL}:
                peer.cancel(event, reason=f"OCO_PEER_FILLED:{order_id}")


__all__ = ["OcoOrderCoordinator", "OrderFill", "OrderStateMachine"]
