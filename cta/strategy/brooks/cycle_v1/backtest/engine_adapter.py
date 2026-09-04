"""Conservative bar matcher used behind legacy or vn.py backtest transports."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import asdict, dataclass
import math
from typing import Any

from ..config import BrooksCycleConfig
from ..core.execution.order_state import OcoOrderCoordinator, OrderFill
from ..core.execution.planner import (
    BlockedPlanError,
    build_plan_geometry,
    geometry_tradeable,
    round_for_order,
)
from ..core.types import EventKey, OrderPlan, PlanGeometry


@dataclass(frozen=True)
class MatchResult:
    fill: OrderFill | None
    reason: str
    geometry: PlanGeometry | None = None


@dataclass(frozen=True)
class OcoMatchResult:
    selected_order_id: str | None
    match: MatchResult
    cancelled_order_ids: tuple[str, ...] = ()


@dataclass(frozen=True)
class LedgerPosition:
    contract_code: str
    candidate_id: str
    direction: int
    quantity: int
    planned_quantity: int
    entry: OrderFill
    contract_multiplier: float
    entry_fee: float
    entry_slippage: float
    margin: float


@dataclass(frozen=True)
class RoundTrip:
    candidate_id: str
    contract_code: str
    direction: int
    quantity: int
    entry_time: object
    exit_time: object
    entry_price: float
    exit_price: float
    gross_pnl: float
    total_fee: float
    slippage: float
    net_pnl: float

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["fees"] = payload.pop("total_fee")
        return payload


class ActualContractLedger:
    """Cash and margin ledger; positions are keyed only by real contract_code."""

    def __init__(self, *, initial_cash: float) -> None:
        if not math.isfinite(initial_cash) or initial_cash <= 0:
            raise ValueError("initial_cash must be finite and positive")
        self.cash = float(initial_cash)
        self.margin_reserved = 0.0
        self.positions: dict[str, LedgerPosition] = {}
        self.round_trips: list[RoundTrip] = []

    def open(
        self,
        plan: OrderPlan,
        fill: OrderFill,
        *,
        contract_multiplier: float,
        margin_rate: float,
        fee: float,
    ) -> None:
        contract = plan.candidate.contract_code
        if not contract:
            raise ValueError("actual contract is missing")
        if fill.event < plan.candidate.active_event:
            raise ValueError("entry fill precedes order activation")
        if fill.quantity <= 0 or fill.quantity > plan.quantity:
            raise ValueError("ledger open fill quantity is invalid")
        mechanics = (contract_multiplier, margin_rate, fee)
        if (
            not all(math.isfinite(value) for value in mechanics)
            or contract_multiplier <= 0
            or margin_rate <= 0
            or fee < 0
        ):
            raise ValueError("ledger mechanics must be finite with valid signs")
        existing = self.positions.get(contract)
        if existing is not None:
            if (
                existing.candidate_id != plan.candidate.candidate_id
                or existing.direction != plan.candidate.direction
                or existing.contract_multiplier != contract_multiplier
            ):
                raise ValueError("partial entry does not match the existing position")
            if existing.quantity + fill.quantity > existing.planned_quantity:
                raise ValueError("partial entries exceed planned quantity")
        margin = fill.quantity * fill.price * contract_multiplier * margin_rate
        entry_slippage = _fill_slippage_cash(fill, contract_multiplier)
        if margin > self.cash - self.margin_reserved - fee:
            raise ValueError("insufficient cash for margin")
        self.cash -= fee
        self.margin_reserved += margin
        if existing is None:
            self.positions[contract] = LedgerPosition(
                contract_code=contract,
                candidate_id=plan.candidate.candidate_id,
                direction=plan.candidate.direction,
                quantity=fill.quantity,
                planned_quantity=plan.quantity,
                entry=fill,
                contract_multiplier=contract_multiplier,
                entry_fee=fee,
                entry_slippage=entry_slippage,
                margin=margin,
            )
            return
        combined_quantity = existing.quantity + fill.quantity
        average_price = (
            existing.entry.price * existing.quantity + fill.price * fill.quantity
        ) / combined_quantity
        reference_values = (
            existing.entry.reference_price,
            fill.reference_price,
        )
        average_reference = None
        if all(value is not None for value in reference_values):
            average_reference = (
                float(existing.entry.reference_price) * existing.quantity
                + float(fill.reference_price) * fill.quantity
            ) / combined_quantity
        self.positions[contract] = LedgerPosition(
            contract_code=contract,
            candidate_id=existing.candidate_id,
            direction=existing.direction,
            quantity=combined_quantity,
            planned_quantity=existing.planned_quantity,
            entry=OrderFill(
                existing.entry.event,
                combined_quantity,
                average_price,
                average_reference,
            ),
            contract_multiplier=existing.contract_multiplier,
            entry_fee=existing.entry_fee + fee,
            entry_slippage=existing.entry_slippage + entry_slippage,
            margin=existing.margin + margin,
        )

    def close(self, contract_code: str, fill: OrderFill, *, fee: float) -> RoundTrip:
        if contract_code not in self.positions:
            raise ValueError("actual contract position is missing")
        position = self.positions[contract_code]
        if fill.event < position.entry.event:
            raise ValueError("exit fill precedes entry fill")
        if (
            fill.quantity <= 0
            or fill.quantity > position.quantity
            or not math.isfinite(fee)
            or fee < 0
        ):
            raise ValueError("ledger close quantity or fee is invalid")
        close_fraction = fill.quantity / position.quantity
        allocated_entry_fee = position.entry_fee * close_fraction
        allocated_entry_slippage = position.entry_slippage * close_fraction
        released_margin = position.margin * close_fraction
        gross = (
            position.direction
            * (fill.price - position.entry.price)
            * fill.quantity
            * position.contract_multiplier
        )
        total_fee = allocated_entry_fee + fee
        slippage = allocated_entry_slippage + _fill_slippage_cash(
            fill, position.contract_multiplier
        )
        trade = RoundTrip(
            candidate_id=position.candidate_id,
            contract_code=contract_code,
            direction=position.direction,
            quantity=fill.quantity,
            entry_time=position.entry.event.timestamp,
            exit_time=fill.event.timestamp,
            entry_price=position.entry.price,
            exit_price=fill.price,
            gross_pnl=gross,
            total_fee=total_fee,
            slippage=slippage,
            net_pnl=gross - total_fee,
        )
        self.cash += gross - fee
        self.margin_reserved -= released_margin
        remaining = position.quantity - fill.quantity
        if remaining == 0:
            del self.positions[contract_code]
        else:
            self.positions[contract_code] = LedgerPosition(
                contract_code=position.contract_code,
                candidate_id=position.candidate_id,
                direction=position.direction,
                quantity=remaining,
                planned_quantity=position.planned_quantity,
                entry=OrderFill(
                    position.entry.event,
                    remaining,
                    position.entry.price,
                    position.entry.reference_price,
                ),
                contract_multiplier=position.contract_multiplier,
                entry_fee=position.entry_fee - allocated_entry_fee,
                entry_slippage=position.entry_slippage - allocated_entry_slippage,
                margin=position.margin - released_margin,
            )
        self.round_trips.append(trade)
        return trade


class ConservativeBarMatcher:
    """Never fills before activation and resolves OHLC ambiguity adversely."""

    def match_entry(
        self,
        plan: OrderPlan,
        bar: Mapping[str, float],
        event: EventKey,
        metadata: Any,
        *,
        features: Mapping[str, Any],
        config: BrooksCycleConfig,
    ) -> MatchResult:
        if plan.oco_group_id is not None:
            return MatchResult(None, "OCO_GROUP_MATCH_REQUIRED")
        return self._match_entry(
            plan,
            bar,
            event,
            metadata,
            features=features,
            config=config,
        )

    def match_oco_entry(
        self,
        plans: Sequence[OrderPlan],
        bar: Mapping[str, float],
        event: EventKey,
        metadata: Any,
        *,
        features: Mapping[str, Any],
        config: BrooksCycleConfig,
    ) -> OcoMatchResult:
        OcoOrderCoordinator(plans)
        first = plans[0]
        if event < first.candidate.active_event:
            return OcoMatchResult(None, MatchResult(None, "ORDER_NOT_ACTIVE"))
        if event >= first.expires_at:
            return OcoMatchResult(None, MatchResult(None, "ORDER_EXPIRED"))
        if not _valid_match_metadata(metadata, require_hash=True):
            return OcoMatchResult(None, MatchResult(None, "BLOCKED_EXECUTION_METADATA"))
        if str(metadata.metadata_hash) != first.metadata_hash:
            return OcoMatchResult(None, MatchResult(None, "METADATA_CHANGED_REPLAN_REQUIRED"))
        touched = [
            plan
            for plan in plans
            if (
                float(bar["high"]) >= plan.geometry.entry
                if plan.candidate.direction == 1
                else float(bar["low"]) <= plan.geometry.entry
            )
        ]
        if not touched:
            return OcoMatchResult(None, MatchResult(None, "ENTRY_NOT_TOUCHED"))
        if len(touched) > 1:
            return OcoMatchResult(None, MatchResult(None, "OCO_AMBIGUOUS_NO_FILL"))
        selected = touched[0]
        match = self._match_entry(
            selected,
            bar,
            event,
            metadata,
            features=features,
            config=config,
        )
        if match.fill is None:
            return OcoMatchResult(None, match)
        cancelled = tuple(plan.order_id for plan in plans if plan is not selected)
        return OcoMatchResult(selected.order_id, match, cancelled)

    def _match_entry(
        self,
        plan: OrderPlan,
        bar: Mapping[str, float],
        event: EventKey,
        metadata: Any,
        *,
        features: Mapping[str, Any],
        config: BrooksCycleConfig,
    ) -> MatchResult:
        if event < plan.candidate.active_event:
            return MatchResult(None, "ORDER_NOT_ACTIVE")
        if event >= plan.expires_at:
            return MatchResult(None, "ORDER_EXPIRED")
        if not _valid_match_metadata(metadata, require_hash=True):
            return MatchResult(None, "BLOCKED_EXECUTION_METADATA")
        if str(metadata.metadata_hash) != plan.metadata_hash:
            return MatchResult(None, "METADATA_CHANGED_REPLAN_REQUIRED")
        direction = plan.candidate.direction
        trigger = plan.geometry.entry
        touched = float(bar["high"]) >= trigger if direction == 1 else float(bar["low"]) <= trigger
        if not touched:
            return MatchResult(None, "ENTRY_NOT_TOUCHED")
        if _limit_locked(bar, metadata, direction):
            return MatchResult(None, "LIMIT_LOCK_NO_COUNTERPARTY")
        reference = (
            max(float(bar["open"]), trigger)
            if direction == 1
            else min(float(bar["open"]), trigger)
        )
        evidence = plan.candidate.evidence
        slippage_embedded = evidence.get("entry_slippage_embedded") == 1.0
        adverse_gap = direction * (float(bar["open"]) - trigger) > 0
        fill_reference = reference
        if slippage_embedded and not adverse_gap:
            raw_fill = reference
            fill_reference = float(evidence["entry_slippage_reference"])
        else:
            raw_fill = reference + direction * (
                float(metadata.stressed_entry_slippage_ticks) * float(metadata.price_tick)
            )
        fill_price = round_for_order(
            raw_fill,
            "BUY" if direction == 1 else "SELL",
            "stop",
            "entry",
            float(metadata.price_tick),
        )
        try:
            geometry = build_plan_geometry(
                features,
                direction,
                fill_price,
                plan.candidate.initial_stop,
                plan.candidate.target_price,
                metadata,
                config,
            )
        except BlockedPlanError as exc:
            return MatchResult(None, f"GAP_REPRICE_{exc.reason_code}")
        if not geometry_tradeable(geometry, config):
            return MatchResult(None, "GAP_REPRICE_NOT_TRADEABLE", geometry)
        return MatchResult(
            OrderFill(event, plan.quantity, fill_price, fill_reference),
            "FILLED",
            geometry,
        )

    def match_exit(
        self,
        *,
        direction: int,
        quantity: int,
        stop: float,
        target: float,
        bar: Mapping[str, float],
        event: EventKey,
        metadata: Any,
    ) -> MatchResult:
        if direction not in (-1, 1) or quantity <= 0:
            raise ValueError("invalid exit request")
        if not _valid_match_metadata(metadata, require_hash=False):
            raise ValueError("execution metadata is incomplete or invalid")
        if _protective_limit_locked(bar, metadata, direction):
            return MatchResult(None, "LIMIT_LOCK_NO_COUNTERPARTY")
        stop_hit = float(bar["low"]) <= stop if direction == 1 else float(bar["high"]) >= stop
        target_hit = float(bar["high"]) > target if direction == 1 else float(bar["low"]) < target
        if not stop_hit and not target_hit:
            return MatchResult(None, "EXIT_NOT_TOUCHED")
        ambiguous = stop_hit and target_hit
        use_stop = stop_hit
        level = stop if use_stop else target
        if use_stop:
            reference = (
                min(float(bar["open"]), level)
                if direction == 1
                else max(float(bar["open"]), level)
            )
        else:
            reference = level
        exit_direction = -direction
        exit_slippage_ticks = (
            max(
                0.0,
                float(metadata.stressed_round_trip_slippage_ticks)
                - float(metadata.stressed_entry_slippage_ticks),
            )
            if use_stop
            else 0.0
        )
        raw_fill = reference + exit_direction * exit_slippage_ticks * float(metadata.price_tick)
        fill_price = round_for_order(
            raw_fill,
            "SELL" if direction == 1 else "BUY",
            "stop" if use_stop else "limit",
            "protective_stop" if use_stop else "profit_target",
            float(metadata.price_tick),
        )
        reason = (
            "STOP_AMBIGUOUS_ADVERSE"
            if ambiguous
            else "STOP"
            if use_stop
            else "TARGET"
        )
        return MatchResult(OrderFill(event, quantity, fill_price, reference), reason)


def _limit_locked(bar: Mapping[str, float], metadata: Any, direction: int) -> bool:
    limit_name = "limit_up" if direction == 1 else "limit_down"
    limit = float(getattr(metadata, limit_name))
    return all(float(bar[name]) == limit for name in ("open", "high", "low", "close"))


def _protective_limit_locked(
    bar: Mapping[str, float],
    metadata: Any,
    position_direction: int,
) -> bool:
    limit_name = "limit_down" if position_direction == 1 else "limit_up"
    limit = float(getattr(metadata, limit_name))
    return all(float(bar[name]) == limit for name in ("open", "high", "low", "close"))


def _fill_slippage_cash(fill: OrderFill, contract_multiplier: float) -> float:
    if fill.reference_price is None:
        return 0.0
    return abs(fill.price - fill.reference_price) * fill.quantity * contract_multiplier


def _valid_match_metadata(metadata: Any, *, require_hash: bool) -> bool:
    required = (
        "price_tick", "limit_up", "limit_down", "stressed_entry_slippage_ticks",
        "stressed_round_trip_slippage_ticks",
    )
    if require_hash:
        required = (*required, "metadata_hash")
    if any(not hasattr(metadata, name) for name in required):
        return False
    try:
        values = tuple(
            float(getattr(metadata, name))
            for name in required
            if name != "metadata_hash"
        )
    except (TypeError, ValueError):
        return False
    if not all(math.isfinite(value) for value in values):
        return False
    if (
        float(metadata.price_tick) <= 0
        or float(metadata.limit_down) >= float(metadata.limit_up)
        or float(metadata.stressed_entry_slippage_ticks) < 0
        or float(metadata.stressed_round_trip_slippage_ticks)
        < float(metadata.stressed_entry_slippage_ticks)
    ):
        return False
    return not require_hash or bool(str(metadata.metadata_hash))


__all__ = [
    "ActualContractLedger", "ConservativeBarMatcher", "LedgerPosition", "MatchResult",
    "OcoMatchResult", "RoundTrip",
]
