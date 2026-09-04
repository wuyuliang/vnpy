"""Pure-price H1/H2 tracker with crossing-time ordinals."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import Enum
from typing import Any

import pandas as pd

from ..types import SetupType


class SecondEntryState(str, Enum):
    IDLE = "IDLE"
    PULLBACK = "PULLBACK"
    ATTEMPT_1_ARMED = "ATTEMPT_1_ARMED"
    ATTEMPT_1_CONFIRMED = "ATTEMPT_1_CONFIRMED"
    RESET_AFTER_1 = "RESET_AFTER_1"
    ATTEMPT_2_ARMED = "ATTEMPT_2_ARMED"
    ATTEMPT_2_CONFIRMED = "ATTEMPT_2_CONFIRMED"
    COMPLETE = "COMPLETE"
    INVALID = "INVALID"


@dataclass(frozen=True)
class PatternAttempt:
    ordinal: int
    setup_type: SetupType
    direction: int
    signal_bar_end: pd.Timestamp
    signal_sequence: int
    trigger_price: float
    crossed_at: pd.Timestamp
    cross_sequence: int
    pullback_extreme: float
    pullback_origin_extreme: float
    candidate_id: str | None = None
    order_id: str | None = None


@dataclass(frozen=True)
class SecondEntryUpdate:
    state: SecondEntryState
    attempt: PatternAttempt | None = None
    reason: str = ""


class SecondEntryPatternTracker:
    """Count market recovery attempts regardless of strategy participation."""

    def __init__(
        self,
        *,
        vt_symbol: str,
        direction: int,
        price_tick: float,
        max_pullback_bars: int,
    ) -> None:
        if not vt_symbol:
            raise ValueError("vt_symbol is required")
        if direction not in (-1, 1):
            raise ValueError("direction must be -1 or 1")
        if price_tick <= 0 or max_pullback_bars < 1:
            raise ValueError("invalid tracker mechanics")
        self.vt_symbol = vt_symbol
        self.direction = direction
        self.price_tick = price_tick
        self.max_pullback_bars = max_pullback_bars
        self.state = SecondEntryState.IDLE
        self.attempt_count = 0
        self.raw_trigger: float | None = None
        self.pullback_extreme: float | None = None
        self.pullback_origin_extreme: float | None = None
        self._last_bar: dict[str, Any] | None = None
        self._observation_bar: dict[str, Any] | None = None
        self._pullback_bars = 0
        self._contract_code = ""
        self._session_id = ""

    def update(
        self,
        bar: Mapping[str, Any],
        *,
        context_valid: bool,
        roll_blocked: bool = False,
        data_interrupted: bool = False,
    ) -> SecondEntryUpdate:
        row = dict(bar)
        _validate_bar(row)
        if self._last_bar is not None and _bar_event(row) <= _bar_event(self._last_bar):
            raise ValueError("tracker price events must be strictly increasing")
        reason = self._blocking_reason(
            row,
            context_valid=context_valid,
            roll_blocked=roll_blocked,
            data_interrupted=data_interrupted,
        )
        if reason:
            self.state = SecondEntryState.INVALID
            self._record(row)
            return SecondEntryUpdate(self.state, reason=reason)
        if self.state in {SecondEntryState.INVALID, SecondEntryState.COMPLETE}:
            self._record(row)
            return SecondEntryUpdate(self.state)

        if self.state is not SecondEntryState.IDLE:
            self._pullback_bars += 1
            self._update_extreme(row)
            if self._pullback_bars > self.max_pullback_bars:
                self.state = SecondEntryState.INVALID
                self._record(row)
                return SecondEntryUpdate(self.state, reason="MAX_PULLBACK_BARS")

        attempt: PatternAttempt | None = None
        if self.state is SecondEntryState.IDLE:
            if self._last_bar is not None and self._starts_pullback(row, self._last_bar):
                self.state = SecondEntryState.PULLBACK
                self._pullback_bars = 1
                self.pullback_origin_extreme = float(
                    self._last_bar["high" if self.direction > 0 else "low"]
                )
                self.pullback_extreme = float(row["low" if self.direction > 0 else "high"])
                self._arm_observation(row, ordinal=1)
        elif self.state in {
            SecondEntryState.PULLBACK,
            SecondEntryState.ATTEMPT_1_ARMED,
            SecondEntryState.ATTEMPT_2_ARMED,
        }:
            ordinal = 1 if self.attempt_count == 0 else 2
            if self._crossed(row):
                attempt = self._confirm_attempt(row, ordinal)
                self.attempt_count = ordinal
                if ordinal == 1:
                    self.state = SecondEntryState.ATTEMPT_1_CONFIRMED
                else:
                    self.state = SecondEntryState.ATTEMPT_2_CONFIRMED
                    self.state = SecondEntryState.COMPLETE
            else:
                self._arm_observation(row, ordinal=ordinal)
        elif self.state is SecondEntryState.ATTEMPT_1_CONFIRMED:
            assert self._last_bar is not None
            if self._reset_after_first(row, self._last_bar):
                self.state = SecondEntryState.RESET_AFTER_1
                self._arm_observation(row, ordinal=2)

        self._record(row)
        return SecondEntryUpdate(self.state, attempt=attempt)

    def _blocking_reason(
        self,
        row: dict[str, Any],
        *,
        context_valid: bool,
        roll_blocked: bool,
        data_interrupted: bool,
    ) -> str:
        if not context_valid:
            return "CONTEXT_INVALID"
        if roll_blocked:
            return "ROLL_BLOCKED"
        if data_interrupted:
            return "DATA_INTERRUPTED"
        contract = str(row.get("contract_code", ""))
        if self._contract_code and contract != self._contract_code:
            return "CONTRACT_CHANGED"
        session = str(row.get("session_id", ""))
        if self._session_id and session and session != self._session_id:
            return "SESSION_CHANGED"
        return ""

    def _starts_pullback(self, row: dict[str, Any], prior: dict[str, Any]) -> bool:
        if self.direction > 0:
            return float(row["low"]) < float(prior["low"])
        return float(row["high"]) > float(prior["high"])

    def _crossed(self, row: dict[str, Any]) -> bool:
        if self.raw_trigger is None or self._observation_bar is None:
            return False
        if self.direction > 0:
            return float(row["high"]) >= self.raw_trigger
        return float(row["low"]) <= self.raw_trigger

    def _reset_after_first(self, row: dict[str, Any], prior: dict[str, Any]) -> bool:
        if self.direction > 0:
            return float(row["high"]) < float(prior["high"])
        return float(row["low"]) > float(prior["low"])

    def _arm_observation(self, row: dict[str, Any], *, ordinal: int) -> None:
        self._observation_bar = row
        self.raw_trigger = (
            float(row["high"]) + self.price_tick
            if self.direction > 0
            else float(row["low"]) - self.price_tick
        )
        self.state = (
            SecondEntryState.ATTEMPT_1_ARMED
            if ordinal == 1
            else SecondEntryState.ATTEMPT_2_ARMED
        )

    def _confirm_attempt(self, row: dict[str, Any], ordinal: int) -> PatternAttempt:
        assert self._observation_bar is not None
        assert self.raw_trigger is not None
        assert self.pullback_extreme is not None
        assert self.pullback_origin_extreme is not None
        return PatternAttempt(
            ordinal=ordinal,
            setup_type=SetupType.H1 if ordinal == 1 else SetupType.H2,
            direction=self.direction,
            signal_bar_end=pd.Timestamp(self._observation_bar["bar_end"]),
            signal_sequence=int(self._observation_bar.get("feature_sequence", 0)),
            trigger_price=self.raw_trigger,
            crossed_at=pd.Timestamp(row["bar_end"]),
            cross_sequence=int(row.get("feature_sequence", 0)),
            pullback_extreme=self.pullback_extreme,
            pullback_origin_extreme=self.pullback_origin_extreme,
        )

    def _update_extreme(self, row: dict[str, Any]) -> None:
        value = float(row["low" if self.direction > 0 else "high"])
        if self.pullback_extreme is None:
            self.pullback_extreme = value
        elif self.direction > 0:
            self.pullback_extreme = min(self.pullback_extreme, value)
        else:
            self.pullback_extreme = max(self.pullback_extreme, value)

    def _record(self, row: dict[str, Any]) -> None:
        self._last_bar = row
        self._contract_code = str(row.get("contract_code", self._contract_code))
        if row.get("session_id"):
            self._session_id = str(row["session_id"])


def _validate_bar(row: dict[str, Any]) -> None:
    missing = [name for name in ("bar_end", "open", "high", "low", "close") if name not in row]
    if missing:
        raise ValueError(f"missing tracker bar fields: {','.join(missing)}")
    timestamp = pd.Timestamp(row["bar_end"])
    if timestamp.tzinfo is None:
        raise ValueError("tracker bar_end must be timezone-aware")
    if float(row["high"]) < float(row["low"]):
        raise ValueError("bar high must be at least low")


def _bar_event(row: Mapping[str, Any]) -> tuple[pd.Timestamp, int]:
    return pd.Timestamp(row["bar_end"]), int(row.get("feature_sequence", 0))


__all__ = [
    "PatternAttempt", "SecondEntryPatternTracker", "SecondEntryState", "SecondEntryUpdate"
]
