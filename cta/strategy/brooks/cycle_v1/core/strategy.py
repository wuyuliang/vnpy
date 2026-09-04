"""Thin orchestration over shared cycle, setup, execution, and risk rules."""
from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
import hashlib
import math
from typing import Any

import pandas as pd

from ..config import BrooksCycleConfig
from .always_in import AlwaysInMachine, AlwaysInSnapshot
from .market_cycle import directional_view
from .multitimeframe import assess_direction_permission
from .risk.portfolio import PortfolioRiskSnapshot, portfolio_allows
from .risk.sizing import size_position
from .setup_engine import SetupDecision, SetupEngine, deduplicate_decisions
from .trackers.second_entry import SecondEntryPatternTracker, SecondEntryState
from .types import (
    CycleSnapshot,
    EventKey,
    MarketCycle,
    OrderPlan,
    OrderStatus,
    RangeSubtype,
)


@dataclass(frozen=True)
class StrategyInput:
    vt_symbol: str
    root_symbol: str
    sector: str
    contract_code: str
    features: Mapping[str, Any]
    causal_history: pd.DataFrame
    medium_cycle: CycleSnapshot
    large_cycle: CycleSnapshot
    metadata: Any
    portfolio: PortfolioRiskSnapshot
    active_event: EventKey
    expiry_event: EventKey
    small_cycle: CycleSnapshot | None = None
    current_symbol_position: int = 0
    safety_blocks: tuple[str, ...] = ()

    def __post_init__(self) -> None:
        if self.current_symbol_position < 0:
            raise ValueError("current_symbol_position must be nonnegative")
        if any(not reason for reason in self.safety_blocks):
            raise ValueError("safety block reason codes must be nonempty")


@dataclass(frozen=True)
class RejectionAudit:
    rejection_index: int
    reason_code: str
    candidate_id: str = ""
    risk_budget: float | None = None
    loss_per_lot: float | None = None
    detail: str = ""


@dataclass(frozen=True)
class StrategyDecision:
    feature_event: EventKey
    medium_cycle: CycleSnapshot
    always_in: AlwaysInSnapshot
    setup_decisions: tuple[SetupDecision, ...]
    order_plans: tuple[OrderPlan, ...]
    rejections: tuple[str, ...]
    rejection_audits: tuple[RejectionAudit, ...]

    def to_audit_dict(self) -> dict[str, Any]:
        return {
            "feature_event": (
                self.feature_event.timestamp.isoformat(), self.feature_event.sequence
            ),
            "cycle": self.medium_cycle.cycle.value,
            "always_in": self.always_in.state.value,
            "candidates": [
                {
                    "candidate_id": item.candidate.candidate_id,
                    "setup_type": item.candidate.setup_type.value,
                    "direction": item.candidate.direction,
                    "trigger": item.candidate.trigger_price,
                    "stop": item.candidate.initial_stop,
                    "target": item.candidate.target_price,
                }
                for item in self.setup_decisions
                if item.accepted and item.candidate is not None
            ],
            "orders": [
                {
                    "order_id": plan.order_id,
                    "candidate_id": plan.candidate.candidate_id,
                    "quantity": plan.quantity,
                    "status": plan.status.value,
                    "expires": (
                        plan.expires_at.timestamp.isoformat(), plan.expires_at.sequence
                    ),
                    "metadata_hash": plan.metadata_hash,
                }
                for plan in self.order_plans
            ],
            "rejections": list(self.rejections),
            "rejection_audits": [
                {
                    "rejection_index": item.rejection_index,
                    "reason_code": item.reason_code,
                    "candidate_id": item.candidate_id,
                    "risk_budget": item.risk_budget,
                    "loss_per_lot": item.loss_per_lot,
                    "detail": item.detail,
                }
                for item in self.rejection_audits
            ],
        }


class BrooksCycleV1Core:
    """Single source of decisions for offline replay and online transport."""

    def __init__(self, config: BrooksCycleConfig) -> None:
        self.config = config
        self.setup_engine = SetupEngine(config)
        self._always_in: dict[str, AlwaysInMachine] = {}
        self._always_snapshots: dict[str, AlwaysInSnapshot] = {}
        self._always_medium_events: dict[str, EventKey] = {}
        self._trackers: dict[tuple[str, int], SecondEntryPatternTracker] = {}
        self._feature_rows: dict[tuple[str, EventKey], Mapping[str, Any]] = {}

    def on_snapshot(self, item: StrategyInput) -> StrategyDecision:
        feature_event = _feature_event(item.features)
        small_cycle = item.small_cycle or item.medium_cycle
        if feature_event != small_cycle.feature_event:
            raise ValueError("feature and small-cycle events must match")
        if item.medium_cycle.feature_event > feature_event:
            raise ValueError("medium-cycle event cannot be newer than small-cycle event")
        if item.large_cycle.feature_event > item.medium_cycle.feature_event:
            raise ValueError("large-cycle event cannot be newer than medium-cycle event")
        if item.active_event <= feature_event:
            raise ValueError("active event must follow the completed feature event")
        if item.expiry_event <= item.active_event:
            raise ValueError("expiry event must follow activation")
        _validate_execution_metadata(item, feature_event)
        always = self._always_snapshot(item)
        if item.safety_blocks:
            self._discard_symbol_pattern_state(item.vt_symbol)
            return StrategyDecision(
                feature_event=feature_event,
                medium_cycle=item.medium_cycle,
                always_in=always,
                setup_decisions=(),
                order_plans=(),
                rejections=item.safety_blocks,
                rejection_audits=(),
            )
        self._remember_feature_row(item.vt_symbol, feature_event, item.features)

        setup_decisions: list[SetupDecision] = []
        tight_range = (
            item.medium_cycle.cycle is MarketCycle.TRADING_RANGE
            and item.medium_cycle.range_subtype is RangeSubtype.TIGHT_BREAKOUT_MODE
        )
        for direction in (1, -1):
            if tight_range:
                setup_decisions.append(
                    self.setup_engine.build_tight_range_breakout(
                        vt_symbol=item.vt_symbol,
                        contract_code=item.contract_code,
                        features=item.features,
                        direction=direction,
                        cycle=item.medium_cycle,
                        metadata=item.metadata,
                        active_event=item.active_event,
                    )
                )
                continue
            try:
                view = directional_view(item.features, item.causal_history, direction)
            except (KeyError, ValueError):
                setup_decisions.append(SetupDecision(False, None, None, "DIRECTIONAL_VIEW_UNAVAILABLE"))
            else:
                setup_decisions.append(
                    self.setup_engine.build_breakout(
                        vt_symbol=item.vt_symbol,
                        contract_code=item.contract_code,
                        features=item.features,
                        view=view,
                        medium_cycle=item.medium_cycle,
                        large_cycle=item.large_cycle,
                        metadata=item.metadata,
                        active_event=item.active_event,
                    )
                )

        if item.medium_cycle.cycle is MarketCycle.TRADING_RANGE:
            failed_features = dict(item.features)
            failed_features["cycle"] = item.medium_cycle.cycle.value
            failed_features["range_subtype"] = (
                item.medium_cycle.range_subtype.value
                if item.medium_cycle.range_subtype is not None
                else ""
            )
            for direction in (1, -1):
                setup_decisions.append(
                    self.setup_engine.build_failed_breakout(
                        vt_symbol=item.vt_symbol,
                        contract_code=item.contract_code,
                        features=failed_features,
                        direction=direction,
                        cycle=item.medium_cycle,
                        metadata=item.metadata,
                        active_event=item.active_event,
                    )
                )

        for direction in (1, -1):
            context_valid = _pullback_context(item.medium_cycle.cycle, direction)
            tracker = self._tracker(item, direction, context_valid)
            update = tracker.update(item.features, context_valid=context_valid)
            if update.attempt is None:
                continue
            signal_features = self._feature_rows.get(
                (
                    item.vt_symbol,
                    EventKey(
                        pd.Timestamp(update.attempt.signal_bar_end).to_pydatetime(),
                        update.attempt.signal_sequence,
                    ),
                )
            )
            if signal_features is None:
                setup_decisions.append(SetupDecision(False, None, None, "SIGNAL_FEATURE_SNAPSHOT_MISSING"))
                continue
            setup_decisions.append(
                self.setup_engine.build_second_entry(
                    vt_symbol=item.vt_symbol,
                    contract_code=item.contract_code,
                    attempt=update.attempt,
                    signal_features=signal_features,
                    current_features=item.features,
                    cycle=item.medium_cycle,
                    metadata=item.metadata,
                    active_event=item.active_event,
                )
            )

        selected = deduplicate_decisions(setup_decisions)
        order_plans: list[OrderPlan] = []
        rejection_audits: list[RejectionAudit] = []
        rejections = [decision.reason_code for decision in setup_decisions if not decision.accepted]
        accepted_decisions = [
            decision
            for decision in setup_decisions
            if decision.accepted and decision.candidate is not None
        ]
        selected_candidate_ids = {
            decision.candidate.candidate_id
            for decision in selected
            if decision.candidate is not None
        }
        rejections.extend(
            f"DEDUPLICATED_CANDIDATE:{decision.candidate.candidate_id}"
            for decision in accepted_decisions
            if decision.candidate.candidate_id not in selected_candidate_ids
        )
        pretrade_risk = portfolio_allows(
            item.portfolio,
            symbol=item.root_symbol,
            sector=item.sector,
            incremental_open_risk=0.0,
            incremental_margin=0.0,
            config=self.config,
        )
        for decision in selected:
            assert decision.candidate is not None and decision.geometry is not None
            candidate = decision.candidate
            permission = assess_direction_permission(
                large=item.large_cycle,
                medium=item.medium_cycle,
                always_in=always.state,
                setup_type=candidate.setup_type,
                direction=candidate.direction,
                min_confidence=self.config.risk.min_cycle_confidence,
            )
            if not permission.allowed:
                rejections.append(permission.reason_code)
                rejection_audits.append(
                    RejectionAudit(
                        rejection_index=len(rejections) - 1,
                        reason_code=permission.reason_code,
                        candidate_id=candidate.candidate_id,
                        detail=(
                            f"large_cycle={item.large_cycle.cycle.value};"
                            f"medium_cycle={item.medium_cycle.cycle.value}"
                        ),
                    )
                )
                continue
            if not pretrade_risk.allowed:
                rejections.extend(pretrade_risk.reason_codes)
                continue
            metadata = item.metadata
            entry_cost, exit_cost = _split_stressed_cost(metadata)
            sizing = size_position(
                equity=item.portfolio.equity,
                entry=decision.geometry.entry,
                stop=decision.geometry.stop,
                contract_multiplier=float(metadata.contract_size),
                estimated_entry_cost=entry_cost,
                stressed_exit_cost=exit_cost,
                lot_step=int(getattr(metadata.instrument, "lot_step", 1))
                if hasattr(metadata, "instrument") else 1,
                cycle=item.medium_cycle.cycle,
                large_confidence=item.large_cycle.confidence,
                medium_confidence=item.medium_cycle.confidence,
                drawdown_multiplier=pretrade_risk.drawdown_multiplier,
                new_order_risk_multiplier=permission.risk_multiplier,
                config=self.config,
            )
            if sizing.quantity <= 0:
                rejections.append(sizing.reason)
                rejection_audits.append(
                    RejectionAudit(
                        rejection_index=len(rejections) - 1,
                        reason_code=sizing.reason,
                        candidate_id=candidate.candidate_id,
                        risk_budget=sizing.risk_budget,
                        loss_per_lot=sizing.loss_per_lot,
                        detail=(
                            f"entry={decision.geometry.entry};"
                            f"stop={decision.geometry.stop};"
                            f"contract_multiplier={metadata.contract_size}"
                        ),
                    )
                )
                continue
            capability = metadata.capability.get(candidate.entry_type)
            if capability is None or not bool(getattr(capability, "supported", True)):
                rejections.append("BLOCKED_ORDER_CAPABILITY")
                continue
            max_order_size = getattr(capability, "max_order_size", None)
            quantity = sizing.quantity if max_order_size is None else min(
                sizing.quantity, int(max_order_size)
            )
            max_position = getattr(getattr(metadata, "status", None), "max_position", None)
            if max_position is not None:
                quantity = min(
                    quantity,
                    max(0, int(max_position) - item.current_symbol_position),
                )
            lot_step = (
                int(getattr(metadata.instrument, "lot_step", 1))
                if hasattr(metadata, "instrument")
                else 1
            )
            quantity = quantity // lot_step * lot_step
            if quantity <= 0:
                rejections.append("ORDER_OR_POSITION_LIMIT_EXHAUSTED")
                continue
            margin_rate = float(
                metadata.daily.margin_rate_long
                if candidate.direction == 1
                else metadata.daily.margin_rate_short
            )
            margin = quantity * decision.geometry.entry * float(metadata.contract_size) * margin_rate
            risk = quantity * sizing.loss_per_lot
            portfolio = portfolio_allows(
                item.portfolio,
                symbol=item.root_symbol,
                sector=item.sector,
                incremental_open_risk=risk,
                incremental_margin=margin,
                config=self.config,
            )
            if not portfolio.allowed:
                for reason in portfolio.reason_codes:
                    rejections.append(reason)
                    rejection_audits.append(
                        RejectionAudit(
                            rejection_index=len(rejections) - 1,
                            reason_code=reason,
                            candidate_id=candidate.candidate_id,
                            risk_budget=sizing.risk_budget,
                            loss_per_lot=sizing.loss_per_lot,
                            detail=(
                                f"quantity={quantity};"
                                f"incremental_open_risk={round(risk, 10)};"
                                f"incremental_margin={round(margin, 10)}"
                            ),
                        )
                    )
                continue
            order_plans.append(
                OrderPlan(
                    order_id=f"order-{candidate.candidate_id}",
                    candidate=candidate,
                    geometry=decision.geometry,
                    quantity=quantity,
                    status=OrderStatus.PLANNED,
                    expires_at=item.expiry_event,
                    metadata_hash=str(metadata.metadata_hash),
                )
            )
        tight_plans = [
            plan
            for plan in order_plans
            if plan.candidate.evidence.get("tight_range_oco") == 1.0
        ]
        if tight_range:
            if len(tight_plans) != 2 or {
                plan.candidate.direction for plan in tight_plans
            } != {-1, 1}:
                order_plans = [plan for plan in order_plans if plan not in tight_plans]
                rejections.append("OCO_PAIR_INCOMPLETE")
            else:
                encoded_ids = "|".join(sorted(plan.candidate.candidate_id for plan in tight_plans))
                group_id = f"oco-{hashlib.sha256(encoded_ids.encode()).hexdigest()[:24]}"
                order_plans = [
                    OrderPlan(
                        order_id=plan.order_id,
                        candidate=plan.candidate,
                        geometry=plan.geometry,
                        quantity=plan.quantity,
                        status=plan.status,
                        expires_at=plan.expires_at,
                        metadata_hash=plan.metadata_hash,
                        rejection_reason=plan.rejection_reason,
                        oco_group_id=group_id,
                    )
                    if plan in tight_plans
                    else plan
                    for plan in order_plans
                ]
        return StrategyDecision(
            feature_event=feature_event,
            medium_cycle=item.medium_cycle,
            always_in=always,
            setup_decisions=tuple(accepted_decisions),
            order_plans=tuple(order_plans),
            rejections=tuple(rejections),
            rejection_audits=tuple(rejection_audits),
        )

    def _always(self, symbol: str) -> AlwaysInMachine:
        if symbol not in self._always_in:
            self._always_in[symbol] = AlwaysInMachine(
                confirm_bars=self.config.cycle.confirm_bars,
                pressure_margin=self.config.cycle.direction_min_pressure_margin,
            )
        return self._always_in[symbol]

    def _always_snapshot(self, item: StrategyInput) -> AlwaysInSnapshot:
        event = item.medium_cycle.feature_event
        previous_event = self._always_medium_events.get(item.vt_symbol)
        if previous_event is not None:
            if event < previous_event:
                raise ValueError("medium-cycle events must not move backward")
            if event == previous_event:
                return self._always_snapshots[item.vt_symbol]
        snapshot = self._always(item.vt_symbol).update(
            item.medium_cycle,
            item.large_cycle,
            major_structure_broken=(
                bool(item.safety_blocks)
                or bool(item.features.get("major_structure_broken", False))
            ),
            failed_breakout_direction=int(
                item.features.get("failed_breakout_direction", 0)
            ),
            follow_through_direction=int(
                item.features.get("follow_through_direction", 0)
            ),
            invalidation_price=_optional_float(
                item.features.get("always_in_invalidation_price")
            ),
        )
        self._always_medium_events[item.vt_symbol] = event
        self._always_snapshots[item.vt_symbol] = snapshot
        return snapshot

    def _tracker(
        self,
        item: StrategyInput,
        direction: int,
        context_valid: bool,
    ) -> SecondEntryPatternTracker:
        key = (item.vt_symbol, direction)
        tracker = self._trackers.get(key)
        if tracker is None or (tracker.state is SecondEntryState.INVALID and context_valid):
            tracker = SecondEntryPatternTracker(
                vt_symbol=item.vt_symbol,
                direction=direction,
                price_tick=float(item.metadata.price_tick),
                max_pullback_bars=self.config.setup.max_pullback_bars,
            )
            self._trackers[key] = tracker
        return tracker

    def _remember_feature_row(
        self,
        symbol: str,
        event: EventKey,
        features: Mapping[str, Any],
    ) -> None:
        self._feature_rows[(symbol, event)] = features
        symbol_keys = sorted(
            (key for key in self._feature_rows if key[0] == symbol),
            key=lambda key: key[1],
        )
        keep = self.config.setup.max_pullback_bars + 2
        for key in symbol_keys[:-keep]:
            del self._feature_rows[key]

    def _discard_symbol_pattern_state(self, symbol: str) -> None:
        for key in [key for key in self._trackers if key[0] == symbol]:
            del self._trackers[key]
        for key in [key for key in self._feature_rows if key[0] == symbol]:
            del self._feature_rows[key]


def run_offline(
    core: BrooksCycleV1Core,
    inputs: Sequence[StrategyInput],
) -> list[StrategyDecision]:
    return [core.on_snapshot(item) for item in inputs]


def _feature_event(features: Mapping[str, Any]) -> EventKey:
    timestamp = pd.Timestamp(features["bar_end"])
    if timestamp.tzinfo is None:
        raise ValueError("feature bar_end must be timezone-aware")
    return EventKey(timestamp.to_pydatetime(), int(features["feature_sequence"]))


def _pullback_context(cycle: MarketCycle, direction: int) -> bool:
    if direction == 1:
        return cycle in {
            MarketCycle.STRONG_BULL_BREAKOUT,
            MarketCycle.BULL_TIGHT_CHANNEL,
            MarketCycle.BULL_BROAD_CHANNEL,
        }
    return cycle in {
        MarketCycle.STRONG_BEAR_BREAKOUT,
        MarketCycle.BEAR_TIGHT_CHANNEL,
        MarketCycle.BEAR_BROAD_CHANNEL,
    }


def _split_stressed_cost(metadata: Any) -> tuple[float, float]:
    fee = float(metadata.stressed_round_trip_fee_cash)
    total_slippage = (
        float(metadata.stressed_round_trip_slippage_ticks)
        * float(metadata.price_tick)
        * float(metadata.contract_size)
    )
    entry_slippage = (
        float(metadata.stressed_entry_slippage_ticks)
        * float(metadata.price_tick)
        * float(metadata.contract_size)
    )
    return fee / 2.0 + entry_slippage, fee / 2.0 + max(0.0, total_slippage - entry_slippage)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _validate_execution_metadata(item: StrategyInput, feature_event: EventKey) -> None:
    metadata = item.metadata
    required = (
        "instrument", "daily", "lifecycle", "capability", "status",
        "contract_size", "price_tick", "limit_up", "limit_down",
        "stressed_round_trip_fee_cash", "stressed_entry_slippage_ticks",
        "stressed_round_trip_slippage_ticks", "assembled_at", "order_event",
        "metadata_hash",
    )
    missing = [name for name in required if not hasattr(metadata, name)]
    if missing:
        raise ValueError(
            f"execution metadata snapshot is incomplete: {','.join(missing)}"
        )
    mechanics = (
        float(metadata.contract_size),
        float(metadata.price_tick),
        float(metadata.limit_up),
        float(metadata.limit_down),
        float(metadata.stressed_round_trip_fee_cash),
        float(metadata.stressed_entry_slippage_ticks),
        float(metadata.stressed_round_trip_slippage_ticks),
        float(metadata.daily.margin_rate_long),
        float(metadata.daily.margin_rate_short),
    )
    if not all(math.isfinite(value) for value in mechanics):
        raise ValueError("execution metadata mechanics must be finite")
    if (
        metadata.contract_size <= 0
        or metadata.price_tick <= 0
        or metadata.limit_down >= metadata.limit_up
        or min(metadata.daily.margin_rate_long, metadata.daily.margin_rate_short) <= 0
        or min(
            metadata.stressed_round_trip_fee_cash,
            metadata.stressed_entry_slippage_ticks,
            metadata.stressed_round_trip_slippage_ticks,
        ) < 0
        or metadata.stressed_entry_slippage_ticks
        > metadata.stressed_round_trip_slippage_ticks
    ):
        raise ValueError("execution metadata mechanics have invalid signs or ordering")
    if getattr(metadata.lifecycle, "contract_code", None) != item.contract_code:
        raise ValueError("execution metadata contract does not match strategy input")
    lot_step = getattr(metadata.instrument, "lot_step", None)
    if not isinstance(lot_step, int) or lot_step <= 0:
        raise ValueError("execution metadata lot_step must be a positive integer")
    if not isinstance(metadata.capability, Mapping) or not metadata.capability:
        raise ValueError("execution metadata capability map is required")
    if not hasattr(metadata.status, "max_position"):
        raise ValueError("execution metadata trading status is incomplete")
    digest = str(metadata.metadata_hash)
    if len(digest) != 64 or any(character not in "0123456789abcdef" for character in digest):
        raise ValueError("execution metadata hash must be a SHA-256 hex digest")
    order_event = pd.Timestamp(metadata.order_event)
    assembled_at = pd.Timestamp(metadata.assembled_at)
    if order_event.tzinfo is None or assembled_at.tzinfo is None:
        raise ValueError("execution metadata events must be timezone-aware")
    if order_event != pd.Timestamp(item.active_event.timestamp):
        raise ValueError("execution metadata snapshot must be effective at the active event")
    if assembled_at > pd.Timestamp(feature_event.timestamp):
        raise ValueError("execution metadata snapshot was assembled after the decision event")


__all__ = [
    "BrooksCycleV1Core", "RejectionAudit", "StrategyDecision", "StrategyInput",
    "run_offline",
]
