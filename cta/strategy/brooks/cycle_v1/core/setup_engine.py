"""V0 setup gates and dispatch policy."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

from ..config import BrooksCycleConfig
from .execution.planner import (
    BlockedPlanError,
    build_plan_geometry,
    geometry_tradeable,
    round_for_order,
)
from .market_cycle import DirectionalView, gate_breakout
from .trade_mode import select_trade_mode
from .trackers.second_entry import PatternAttempt
from .types import (
    CycleSnapshot,
    EventKey,
    MarketCycle,
    PlanGeometry,
    RangeSubtype,
    SetupCandidate,
    SetupType,
    TradeMode,
)


@dataclass(frozen=True)
class SetupDecision:
    accepted: bool
    candidate: SetupCandidate | None
    geometry: PlanGeometry | None
    reason_code: str


class SetupEngine:
    """Convert enabled price events into one shared candidate contract."""

    def __init__(self, config: BrooksCycleConfig) -> None:
        self.config = config

    def build_breakout(
        self,
        *,
        vt_symbol: str,
        contract_code: str,
        features: Mapping[str, Any],
        view: DirectionalView,
        medium_cycle: CycleSnapshot,
        large_cycle: CycleSnapshot,
        metadata: Any,
        active_event: EventKey,
    ) -> SetupDecision:
        if not gate_breakout(features, view, self.config):
            return _rejected("BREAKOUT_GATE_FAILED")
        direction = view.d
        if (
            large_cycle.direction not in (0, direction)
            or large_cycle.confidence < self.config.risk.min_cycle_confidence
        ):
            return _rejected(
                "LARGE_DIRECTION_CONFLICT"
                if large_cycle.direction == -direction
                else "CYCLE_CONFIDENCE_BLOCKED"
            )
        mode = select_trade_mode(
            medium_cycle,
            range_pct=_optional_float(features.get("range_pct")),
            lower_zone=self.config.cycle.range_lower_zone,
            upper_zone=self.config.cycle.range_upper_zone,
        )
        if mode is TradeMode.NO_TRADE:
            return _rejected("TRADE_MODE_NO_TRADE")
        tick = float(metadata.price_tick)
        raw_entry = float(features["close"]) + direction * (
            float(metadata.stressed_entry_slippage_ticks) * tick
        )
        entry = round_for_order(
            raw_entry,
            "BUY" if direction == 1 else "SELL",
            "stop",
            "entry",
            tick,
        )
        breakout_level = float(features["prior_high" if direction == 1 else "prior_low"])
        latest = _optional_float(
            features.get(
                "latest_confirmed_swing_low" if direction == 1
                else "latest_confirmed_swing_high"
            )
        )
        latest = breakout_level if latest is None else latest
        structural_stop = min(breakout_level, latest) if direction == 1 else max(
            breakout_level, latest
        )
        measured_target = entry + direction * (
            float(features["prior_high"]) - float(features["prior_low"])
        )
        geometry = self._geometry(
            features, direction, entry, structural_stop, measured_target, metadata
        )
        if isinstance(geometry, str):
            return _rejected(geometry)
        return self._candidate(
            vt_symbol=vt_symbol,
            contract_code=contract_code,
            setup_type=SetupType.BREAKOUT,
            direction=direction,
            cycle=medium_cycle,
            signal_event=_feature_event(features),
            known_event=_next_sequence(_feature_event(features)),
            active_event=active_event,
            geometry=geometry,
            mode=mode,
            evidence={
                "pressure": view.pressure,
                "breakout_distance": view.breakout_distance,
                "obstacle_kind": geometry.obstacle_kind,
                "entry_slippage_embedded": 1.0,
                "entry_slippage_reference": float(features["close"]),
            },
            invalidation={"lost_level": breakout_level},
            holding=(2, 30),
        )

    def build_tight_range_breakout(
        self,
        *,
        vt_symbol: str,
        contract_code: str,
        features: Mapping[str, Any],
        direction: int,
        cycle: CycleSnapshot,
        metadata: Any,
        active_event: EventKey,
    ) -> SetupDecision:
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or 1")
        if (
            cycle.cycle is not MarketCycle.TRADING_RANGE
            or cycle.range_subtype is not RangeSubtype.TIGHT_BREAKOUT_MODE
        ):
            return _rejected("TIGHT_RANGE_BREAKOUT_CONTEXT_REQUIRED")
        tick = float(metadata.price_tick)
        range_high = float(features["range_high"])
        range_low = float(features["range_low"])
        if not all(math.isfinite(value) for value in (tick, range_high, range_low)) or (
            tick <= 0 or range_low >= range_high
        ):
            return _rejected("INVALID_TIGHT_RANGE_BOUNDARY")
        trigger_reference = range_high + tick if direction == 1 else range_low - tick
        raw_entry = trigger_reference + direction * (
            float(metadata.stressed_entry_slippage_ticks) * tick
        )
        entry = round_for_order(
            raw_entry,
            "BUY" if direction == 1 else "SELL",
            "stop",
            "entry",
            tick,
        )
        structural_key = (
            "latest_confirmed_swing_low"
            if direction == 1
            else "latest_confirmed_swing_high"
        )
        structural_stop = _optional_float(features.get(structural_key))
        if structural_stop is None:
            structural_stop = (range_high + range_low) / 2.0
        measured_target = entry + direction * (range_high - range_low)
        geometry = self._geometry(
            features,
            direction,
            entry,
            structural_stop,
            measured_target,
            metadata,
        )
        if isinstance(geometry, str):
            return _rejected(geometry)
        return self._candidate(
            vt_symbol=vt_symbol,
            contract_code=contract_code,
            setup_type=SetupType.BREAKOUT,
            direction=direction,
            cycle=cycle,
            signal_event=_feature_event(features),
            known_event=_next_sequence(_feature_event(features)),
            active_event=active_event,
            geometry=geometry,
            mode=TradeMode.SWING,
            evidence={
                "tight_range_oco": 1.0,
                "range_high": range_high,
                "range_low": range_low,
                "entry_slippage_embedded": 1.0,
                "entry_slippage_reference": trigger_reference,
                "obstacle_kind": geometry.obstacle_kind,
            },
            invalidation={"lost_level": range_high if direction == 1 else range_low},
            holding=(2, 30),
        )

    def build_failed_breakout(
        self,
        *,
        vt_symbol: str,
        contract_code: str,
        features: Mapping[str, Any],
        direction: int,
        cycle: CycleSnapshot,
        metadata: Any,
        active_event: EventKey,
    ) -> SetupDecision:
        if not gate_failed_breakout(features, direction, self.config):
            return _rejected("FAILED_BREAKOUT_GATE_FAILED")
        tick = float(metadata.price_tick)
        raw_trigger = (
            float(features["high"]) + tick
            if direction == 1
            else float(features["low"]) - tick
        )
        entry = round_for_order(
            raw_trigger,
            "BUY" if direction == 1 else "SELL",
            "stop",
            "entry",
            tick,
        )
        structural_stop = (
            float(features["low"]) - self.config.execution.stop_min_ticks * tick
            if direction == 1
            else float(features["high"]) + self.config.execution.stop_min_ticks * tick
        )
        geometry = self._geometry(
            features,
            direction,
            entry,
            structural_stop,
            float(features["range_mid"]),
            metadata,
        )
        if isinstance(geometry, str):
            return _rejected(geometry)
        event = _feature_event(features)
        return self._candidate(
            vt_symbol=vt_symbol,
            contract_code=contract_code,
            setup_type=SetupType.FAILED_BREAKOUT,
            direction=direction,
            cycle=cycle,
            signal_event=event,
            known_event=_next_sequence(event),
            active_event=active_event,
            geometry=geometry,
            mode=TradeMode.SCALP,
            evidence={"reclaimed_range_boundary": 1.0, "obstacle_kind": geometry.obstacle_kind},
            invalidation={
                "deeper_breakout": float(features["low" if direction == 1 else "high"]),
                "range_mid": float(features["range_mid"]),
            },
            holding=(1, 10),
        )

    def build_second_entry(
        self,
        *,
        vt_symbol: str,
        contract_code: str,
        attempt: PatternAttempt,
        signal_features: Mapping[str, Any],
        current_features: Mapping[str, Any],
        cycle: CycleSnapshot,
        metadata: Any,
        active_event: EventKey,
    ) -> SetupDecision:
        if not setup_allowed_in_cycle(attempt.setup_type, cycle.cycle, attempt.direction):
            return _rejected("SETUP_CYCLE_NOT_ALLOWED")
        mode = select_trade_mode(
            cycle,
            range_pct=_optional_float(current_features.get("range_pct")),
            lower_zone=self.config.cycle.range_lower_zone,
            upper_zone=self.config.cycle.range_upper_zone,
        )
        if mode is TradeMode.NO_TRADE:
            return _rejected("TRADE_MODE_NO_TRADE")
        atr = float(current_features["atr"])
        signal_range = float(signal_features["high"]) - float(signal_features["low"])
        if signal_range > self.config.setup.max_signal_bar_atr * atr:
            return _rejected("SIGNAL_BAR_TOO_LARGE")
        if strong_directional_bar(signal_features, -attempt.direction, self.config):
            return _rejected("STRONG_OPPOSITE_SIGNAL_BAR")
        tick = float(metadata.price_tick)
        entry = round_for_order(
            attempt.trigger_price,
            "BUY" if attempt.direction == 1 else "SELL",
            "stop",
            "entry",
            tick,
        )
        structural_stop = (
            attempt.pullback_extreme - self.config.execution.stop_min_ticks * tick
            if attempt.direction == 1
            else attempt.pullback_extreme + self.config.execution.stop_min_ticks * tick
        )
        geometry = self._geometry(
            current_features,
            attempt.direction,
            entry,
            structural_stop,
            attempt.pullback_origin_extreme,
            metadata,
        )
        if isinstance(geometry, str):
            return _rejected(geometry)
        signal_event = EventKey(
            pd.Timestamp(attempt.signal_bar_end).to_pydatetime(), attempt.signal_sequence
        )
        crossing_event = EventKey(
            pd.Timestamp(attempt.crossed_at).to_pydatetime(), attempt.cross_sequence
        )
        return self._candidate(
            vt_symbol=vt_symbol,
            contract_code=contract_code,
            setup_type=attempt.setup_type,
            direction=attempt.direction,
            cycle=cycle,
            signal_event=signal_event,
            known_event=crossing_event,
            active_event=active_event,
            geometry=geometry,
            mode=mode,
            evidence={
                "attempt_ordinal": float(attempt.ordinal),
                "pattern_crossed_at": attempt.crossed_at.isoformat(),
                "obstacle_kind": geometry.obstacle_kind,
            },
            invalidation={"pullback_extreme": attempt.pullback_extreme},
            holding=(2, 30),
        )

    def _geometry(
        self,
        features: Mapping[str, Any],
        direction: int,
        entry: float,
        stop: float,
        target: float,
        metadata: Any,
    ) -> PlanGeometry | str:
        try:
            geometry = build_plan_geometry(
                features,
                direction,
                entry,
                stop,
                target,
                metadata,
                self.config,
            )
        except BlockedPlanError as exc:
            return exc.reason_code
        if not geometry_tradeable(geometry, self.config):
            return "PLAN_GEOMETRY_NOT_TRADEABLE"
        return geometry

    def _candidate(
        self,
        *,
        vt_symbol: str,
        contract_code: str,
        setup_type: SetupType,
        direction: int,
        cycle: CycleSnapshot,
        signal_event: EventKey,
        known_event: EventKey,
        active_event: EventKey,
        geometry: PlanGeometry,
        mode: TradeMode,
        evidence: Mapping[str, float | str],
        invalidation: Mapping[str, float | str],
        holding: tuple[int, int],
    ) -> SetupDecision:
        decision_event = _next_sequence(known_event)
        candidate = SetupCandidate.create(
            rule_version=f"{self.config.version}:{self.config.config_hash}",
            vt_symbol=vt_symbol,
            contract_code=contract_code,
            setup_type=setup_type,
            direction=direction,
            context_cycle=cycle.cycle,
            signal_event=signal_event,
            known_event=known_event,
            decision_event=decision_event,
            active_event=active_event,
            entry_type="STOP",
            trigger_price=geometry.entry,
            initial_stop=geometry.stop,
            target_price=geometry.target,
            trade_mode=mode,
            expected_holding_bars=holding,
            evidence=evidence,
            invalidation=invalidation,
        )
        return SetupDecision(True, candidate, geometry, "")


def setup_availability(
    setup_type: SetupType,
    config: BrooksCycleConfig,
) -> tuple[bool, str]:
    if setup_type not in config.enabled_setups:
        return False, "RESEARCH_ONLY_NOT_ENABLED"
    return True, ""


def strong_directional_bar(
    features: Mapping[str, Any],
    direction: int,
    config: BrooksCycleConfig,
) -> bool:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    close_pos = float(
        features["close_pos_long" if direction == 1 else "close_pos_short"]
    )
    return bool(
        direction * float(features["body"]) > 0
        and float(features["body_ratio"]) >= config.setup.strong_opposite_body_ratio
        and close_pos >= config.setup.strong_opposite_close_pos
    )


def gate_failed_breakout(
    features: Mapping[str, Any],
    direction: int,
    config: BrooksCycleConfig,
) -> bool:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    cycle = _enum_value(features.get("cycle"))
    subtype = _enum_value(features.get("range_subtype"))
    if cycle != MarketCycle.TRADING_RANGE.value or subtype != RangeSubtype.BROAD.value:
        return False
    excursion = (
        float(features["range_low"]) - float(features["low"])
        if direction == 1
        else float(features["high"]) - float(features["range_high"])
    )
    reclaim = (
        float(features["close"]) - float(features["range_low"])
        if direction == 1
        else float(features["range_high"]) - float(features["close"])
    )
    close_pos = float(
        features["close_pos_long" if direction == 1 else "close_pos_short"]
    )
    atr = float(features["atr"])
    return bool(
        excursion > 0
        and excursion <= config.setup.failed_breakout_max_excursion_atr * atr
        and reclaim >= config.setup.failed_breakout_min_reclaim_atr * atr
        and direction * float(features["body"]) > 0
        and float(features["body_ratio"]) >= config.setup.failed_breakout_min_body_ratio
        and close_pos >= config.setup.failed_breakout_min_close_pos
    )


def gate_breakout_failure(
    features: Mapping[str, Any],
    previous_features: Mapping[str, Any],
    direction: int,
    config: BrooksCycleConfig,
) -> bool:
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    breakout_level = float(features["breakout_level"])
    atr = float(features["atr"])
    lost_level = (
        float(features["close"])
        < breakout_level - config.cycle.breakout_tolerance_atr * atr
        if direction == 1
        else float(features["close"])
        > breakout_level + config.cycle.breakout_tolerance_atr * atr
    )
    opposite_follow_through = strong_directional_bar(
        previous_features, -direction, config
    ) and strong_directional_bar(features, -direction, config)
    return bool(
        lost_level
        or opposite_follow_through
        or _enum_value(features.get("cycle")) == MarketCycle.TRANSITION.value
    )


def setup_allowed_in_cycle(
    setup_type: SetupType,
    cycle: MarketCycle,
    direction: int,
) -> bool:
    bull = direction == 1
    if setup_type is SetupType.BREAKOUT:
        return cycle not in {MarketCycle.TRANSITION, MarketCycle.UNAVAILABLE}
    if setup_type is SetupType.H1:
        return cycle in (
            {MarketCycle.STRONG_BULL_BREAKOUT, MarketCycle.BULL_TIGHT_CHANNEL}
            if bull
            else {MarketCycle.STRONG_BEAR_BREAKOUT, MarketCycle.BEAR_TIGHT_CHANNEL}
        )
    if setup_type is SetupType.H2:
        return cycle in (
            {MarketCycle.BULL_TIGHT_CHANNEL, MarketCycle.BULL_BROAD_CHANNEL}
            if bull
            else {MarketCycle.BEAR_TIGHT_CHANNEL, MarketCycle.BEAR_BROAD_CHANNEL}
        )
    if setup_type is SetupType.FAILED_BREAKOUT:
        return cycle is MarketCycle.TRADING_RANGE
    return False


def _enum_value(value: object) -> str:
    return str(value.value) if hasattr(value, "value") else str(value)


def deduplicate_decisions(decisions: list[SetupDecision]) -> list[SetupDecision]:
    priority = {
        SetupType.BREAKOUT: 0,
        SetupType.FIRST_PULLBACK: 1,
        SetupType.H2: 2,
        SetupType.H1: 3,
        SetupType.TREND_RESUMPTION: 4,
        SetupType.FAILED_BREAKOUT: 0,
        SetupType.RANGE_REVERSAL: 1,
    }
    selected: dict[tuple[str, int, EventKey], SetupDecision] = {}
    for decision in decisions:
        if not decision.accepted or decision.candidate is None:
            continue
        candidate = decision.candidate
        key = (candidate.vt_symbol, candidate.direction, candidate.signal_event)
        current = selected.get(key)
        if current is None or priority.get(candidate.setup_type, 99) < priority.get(
            current.candidate.setup_type, 99  # type: ignore[union-attr]
        ):
            selected[key] = decision
    return list(selected.values())


def _feature_event(features: Mapping[str, Any]) -> EventKey:
    timestamp = pd.Timestamp(features["bar_end"])
    if timestamp.tzinfo is None:
        raise ValueError("feature bar_end must be timezone-aware")
    return EventKey(timestamp.to_pydatetime(), int(features["feature_sequence"]))


def _next_sequence(event: EventKey) -> EventKey:
    return EventKey(event.timestamp, event.sequence + 1)


def _optional_float(value: object) -> float | None:
    if value is None:
        return None
    number = float(value)
    return number if math.isfinite(number) else None


def _rejected(reason: str) -> SetupDecision:
    return SetupDecision(False, None, None, reason)


__all__ = [
    "SetupDecision", "SetupEngine", "deduplicate_decisions", "gate_breakout_failure",
    "gate_failed_breakout", "setup_allowed_in_cycle", "setup_availability",
    "strong_directional_bar",
]
