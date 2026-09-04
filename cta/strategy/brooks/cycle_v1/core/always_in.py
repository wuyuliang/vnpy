"""Hysteretic Always-In direction permission, separate from positions."""
from __future__ import annotations

from dataclasses import dataclass

from .types import AlwaysIn, CycleSnapshot, EventKey, MarketCycle


BULL_CYCLES = {
    MarketCycle.STRONG_BULL_BREAKOUT,
    MarketCycle.BULL_TIGHT_CHANNEL,
    MarketCycle.BULL_BROAD_CHANNEL,
}
BEAR_CYCLES = {
    MarketCycle.STRONG_BEAR_BREAKOUT,
    MarketCycle.BEAR_TIGHT_CHANNEL,
    MarketCycle.BEAR_BROAD_CHANNEL,
}


@dataclass(frozen=True)
class AlwaysInSnapshot:
    state: AlwaysIn
    prior_state: AlwaysIn
    bars_in_state: int
    feature_event: EventKey
    entered_at: EventKey
    last_changed_at: EventKey
    trigger_evidence: str
    invalidation_price: float | None


class AlwaysInMachine:
    def __init__(self, *, confirm_bars: int, pressure_margin: float) -> None:
        if confirm_bars < 1 or pressure_margin < 0:
            raise ValueError("invalid Always-In configuration")
        self.confirm_bars = confirm_bars
        self.pressure_margin = pressure_margin
        self.state = AlwaysIn.NEUTRAL
        self.bars_in_state = 0
        self._pending: AlwaysIn | None = None
        self._pending_count = 0
        self._entered_at: EventKey | None = None
        self._last_changed_at: EventKey | None = None
        self._last_event: EventKey | None = None

    def update(
        self,
        medium: CycleSnapshot,
        large: CycleSnapshot,
        *,
        major_structure_broken: bool = False,
        failed_breakout_direction: int = 0,
        follow_through_direction: int = 0,
        invalidation_price: float | None = None,
    ) -> AlwaysInSnapshot:
        event = medium.feature_event
        if large.feature_event > event:
            raise ValueError("large-cycle event cannot be newer than medium-cycle event")
        if self._last_event is not None and event <= self._last_event:
            raise ValueError("Always-In events must be strictly increasing")
        self._last_event = event
        if self._entered_at is None:
            self._entered_at = event
            self._last_changed_at = event
        prior = self.state
        desired, immediate = self._desired(
            medium,
            large,
            major_structure_broken=major_structure_broken,
            failed_breakout_direction=failed_breakout_direction,
            follow_through_direction=follow_through_direction,
        )
        if desired == self.state:
            self.bars_in_state += 1
            self._pending = None
            self._pending_count = 0
        else:
            if desired == self._pending:
                self._pending_count += 1
            else:
                self._pending = desired
                self._pending_count = 1
            required = 1 if immediate else self.confirm_bars
            if self._pending_count >= required:
                self.state = desired
                self.bars_in_state = 1
                self._entered_at = event
                self._last_changed_at = event
                self._pending = None
                self._pending_count = 0
            else:
                self.bars_in_state += 1
        return AlwaysInSnapshot(
            state=self.state,
            prior_state=prior,
            bars_in_state=self.bars_in_state,
            feature_event=event,
            entered_at=self._entered_at,
            last_changed_at=self._last_changed_at,
            trigger_evidence=(
                f"medium={medium.cycle.value};large={large.cycle.value};"
                f"desired={desired.value};immediate={int(immediate)}"
            ),
            invalidation_price=invalidation_price,
        )

    def _desired(
        self,
        medium: CycleSnapshot,
        large: CycleSnapshot,
        *,
        major_structure_broken: bool,
        failed_breakout_direction: int,
        follow_through_direction: int,
    ) -> tuple[AlwaysIn, bool]:
        bull_margin = medium.bull_pressure - medium.bear_pressure
        bear_margin = -bull_margin
        bull_allowed = large.cycle not in BEAR_CYCLES and large.direction >= 0
        bear_allowed = large.cycle not in BULL_CYCLES and large.direction <= 0
        if major_structure_broken:
            return AlwaysIn.NEUTRAL, True
        if self.state is AlwaysIn.LONG:
            if failed_breakout_direction == -1:
                return AlwaysIn.NEUTRAL, True
            if medium.cycle is MarketCycle.TRADING_RANGE or bear_margin >= self.pressure_margin:
                return AlwaysIn.NEUTRAL, False
            return AlwaysIn.LONG, False
        if self.state is AlwaysIn.SHORT:
            if failed_breakout_direction == 1:
                return AlwaysIn.NEUTRAL, True
            if medium.cycle is MarketCycle.TRADING_RANGE or bull_margin >= self.pressure_margin:
                return AlwaysIn.NEUTRAL, False
            return AlwaysIn.SHORT, False
        if medium.cycle in BULL_CYCLES and bull_allowed and bull_margin >= self.pressure_margin:
            return AlwaysIn.LONG, False
        if medium.cycle in BEAR_CYCLES and bear_allowed and bear_margin >= self.pressure_margin:
            return AlwaysIn.SHORT, False
        if follow_through_direction == 1 and bull_allowed:
            return AlwaysIn.LONG, True
        if follow_through_direction == -1 and bear_allowed:
            return AlwaysIn.SHORT, True
        return AlwaysIn.NEUTRAL, False


__all__ = ["AlwaysInMachine", "AlwaysInSnapshot"]
