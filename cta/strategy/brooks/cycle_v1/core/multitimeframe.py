"""Event-key alignment that never exposes incomplete higher-timeframe bars."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd

from .types import AlwaysIn, CycleSnapshot, EventKey, MarketCycle, RangeSubtype, SetupType


@dataclass(frozen=True)
class DirectionPermission:
    allowed: bool
    risk_multiplier: float
    reason_code: str


def assess_direction_permission(
    *,
    large: CycleSnapshot,
    medium: CycleSnapshot,
    always_in: AlwaysIn,
    setup_type: SetupType,
    direction: int,
    min_confidence: float,
) -> DirectionPermission:
    """Apply the frozen large/medium/Always-In conflict table before sizing."""
    if direction not in (-1, 1):
        raise ValueError("direction must be -1 or 1")
    if large.cycle in {MarketCycle.UNAVAILABLE, MarketCycle.TRANSITION}:
        return DirectionPermission(False, 0.0, "LARGE_CYCLE_UNAVAILABLE")
    if medium.cycle in {MarketCycle.UNAVAILABLE, MarketCycle.TRANSITION}:
        return DirectionPermission(False, 0.0, "MEDIUM_CYCLE_UNAVAILABLE")
    if min(large.confidence, medium.confidence) < min_confidence:
        return DirectionPermission(False, 0.0, "CYCLE_CONFIDENCE_BLOCKED")
    if large.direction == -direction:
        return DirectionPermission(False, 0.0, "LARGE_DIRECTION_CONFLICT")

    expected_always = AlwaysIn.LONG if direction == 1 else AlwaysIn.SHORT
    if setup_type in {SetupType.H1, SetupType.H2} and always_in is not expected_always:
        return DirectionPermission(False, 0.0, "ALWAYS_IN_NOT_CONFIRMED")

    if (
        setup_type is SetupType.BREAKOUT
        and medium.cycle is MarketCycle.TRADING_RANGE
        and medium.range_subtype is RangeSubtype.TIGHT_BREAKOUT_MODE
        and large.cycle is MarketCycle.TRADING_RANGE
        and large.direction == 0
    ):
        return DirectionPermission(True, 1.0, "TIGHT_RANGE_OCO_CYCLE_RISK")

    if large.direction == direction:
        return DirectionPermission(True, 1.0, "")
    if setup_type is SetupType.FAILED_BREAKOUT and large.cycle is MarketCycle.TRADING_RANGE:
        return DirectionPermission(True, 1.0, "")
    if (
        setup_type in {SetupType.BREAKOUT, SetupType.H1}
        and medium.direction == direction
        and medium.cycle
        in {MarketCycle.STRONG_BULL_BREAKOUT, MarketCycle.STRONG_BEAR_BREAKOUT}
    ):
        return DirectionPermission(True, 0.5, "LARGE_NEUTRAL_REDUCED_RISK")
    return DirectionPermission(False, 0.0, "LARGE_DIRECTION_NEUTRAL")


def align_completed_snapshots(
    decisions: pd.DataFrame,
    higher: pd.DataFrame,
) -> pd.DataFrame:
    required_decision = {"decision_asof", "decision_sequence"}
    required_higher = {"feature_asof", "feature_sequence"}
    if not required_decision.issubset(decisions) or not required_higher.issubset(higher):
        raise ValueError("multi-timeframe event columns are missing")
    higher_rows: list[tuple[EventKey, dict[str, Any]]] = []
    for row in higher.to_dict("records"):
        event = _event(row["feature_asof"], row["feature_sequence"])
        higher_rows.append((event, row))
    if any(left[0] >= right[0] for left, right in zip(higher_rows, higher_rows[1:], strict=False)):
        raise ValueError("higher-timeframe events must be strictly increasing")
    decision_rows = [
        (
            _event(row["decision_asof"], row["decision_sequence"]),
            row,
        )
        for row in decisions.to_dict("records")
    ]
    if any(
        left[0] >= right[0]
        for left, right in zip(decision_rows, decision_rows[1:], strict=False)
    ):
        raise ValueError("decision events must be strictly increasing")
    output: list[dict[str, Any]] = []
    higher_index = 0
    selected: dict[str, Any] = {}
    for decision, decision_row in decision_rows:
        while (
            higher_index < len(higher_rows)
            and higher_rows[higher_index][0] <= decision
        ):
            selected = higher_rows[higher_index][1]
            higher_index += 1
        combined = dict(decision_row)
        for key, value in selected.items():
            if key not in combined:
                combined[key] = value
        output.append(combined)
    return pd.DataFrame(output, index=decisions.index)


def _event(timestamp: object, sequence: object) -> EventKey:
    value = pd.Timestamp(timestamp)
    if value.tzinfo is None:
        raise ValueError("multi-timeframe timestamp must be timezone-aware")
    return EventKey(value.to_pydatetime(), int(sequence))


__all__ = [
    "DirectionPermission", "align_completed_snapshots", "assess_direction_permission"
]
