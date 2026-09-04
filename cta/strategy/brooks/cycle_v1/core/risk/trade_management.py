"""Entry-plan-frozen trade management using only information known as of each bar."""
from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
import math

from ...config import BrooksCycleConfig
from ..types import EventKey, TradeMode
from .stops import allow_break_even, ratchet_stop


class ExitAction(str, Enum):
    HOLD = "HOLD"
    EXIT_FULL = "EXIT_FULL"
    PARTIAL_TARGET = "PARTIAL_TARGET"
    EXIT_ORDER_PENDING = "EXIT_ORDER_PENDING"
    RISK_REDUCTION_PENDING = "RISK_REDUCTION_PENDING"


@dataclass(frozen=True)
class ManagementDecision:
    action: ExitAction
    reason: str
    quantity: int
    trigger_price: float | None
    stop_after_decision: float
    running_mfe_R: float


class TradeManager:
    """Manage one position without changing its predeclared scalp/swing mode."""

    def __init__(
        self,
        *,
        direction: int,
        entry: float,
        initial_stop: float,
        target: float,
        quantity: int,
        mode: TradeMode,
        entry_event: EventKey,
        config: BrooksCycleConfig,
        partial_fraction: float = 0.0,
    ) -> None:
        if direction not in (-1, 1) or quantity <= 0:
            raise ValueError("invalid trade direction or quantity")
        initial_risk = direction * (entry - initial_stop)
        target_reward = direction * (target - entry)
        if initial_risk <= 0 or target_reward <= 0:
            raise ValueError("stop and target must bracket entry")
        if not 0.0 <= partial_fraction < 1.0:
            raise ValueError("partial_fraction must be in [0, 1)")
        self.direction = direction
        self.entry = float(entry)
        self.current_stop = float(initial_stop)
        self.target = float(target)
        self.initial_quantity = quantity
        self.remaining_quantity = quantity
        self.mode = mode
        self.entry_event = entry_event
        self.config = config
        self.partial_fraction = partial_fraction
        self.initial_risk = initial_risk
        self.running_mfe_R = 0.0
        self._partial_done = False
        self._last_event = entry_event
        self._last_execution_event: EventKey | None = None
        self._pending_action: ExitAction | None = None
        self._pending_reason = ""
        self._pending_quantity = 0
        self._pending_trigger: float | None = None
        self._pending_filled_quantity = 0

    def update(
        self,
        *,
        event: EventKey,
        high: float,
        low: float,
        close: float,
        remaining_space_R: float,
        favorable_structure_confirmed: bool,
        safety_block: str = "",
        setup_failed: bool = False,
        opposite_strong_breakout: bool = False,
        proposed_structural_stop: float | None = None,
        proposed_stop_known_at: EventKey | None = None,
        session_exit: bool = False,
        roll_exit: bool = False,
    ) -> ManagementDecision:
        latest_event = max(
            value
            for value in (self._last_event, self._last_execution_event)
            if value is not None
        )
        if event <= latest_event:
            raise ValueError("management events must be strictly increasing")
        if (
            not all(math.isfinite(value) for value in (high, low, close, remaining_space_R))
            or low > high
            or not low <= close <= high
        ):
            raise ValueError("invalid completed management bar")
        if self.remaining_quantity <= 0:
            raise ValueError("trade is already closed")
        self._last_event = event
        favorable = high - self.entry if self.direction == 1 else self.entry - low
        self.running_mfe_R = max(self.running_mfe_R, favorable / self.initial_risk)

        if safety_block:
            return self._decision(
                ExitAction.RISK_REDUCTION_PENDING,
                safety_block,
                0,
                None,
            )
        if self._stop_touched(high, low):
            return self._queue_exit(
                ExitAction.EXIT_FULL,
                "PROTECTIVE_STOP",
                self.remaining_quantity,
                self.current_stop,
                replace_pending=True,
            )
        if setup_failed or opposite_strong_breakout:
            return self._queue_exit(
                ExitAction.EXIT_FULL,
                "SETUP_FAILURE" if setup_failed else "OPPOSITE_STRONG_BREAKOUT",
                self.remaining_quantity,
                close,
                replace_pending=True,
            )
        if roll_exit or session_exit:
            return self._queue_exit(
                ExitAction.EXIT_FULL,
                "ROLL_EXIT" if roll_exit else "SESSION_EXIT",
                self.remaining_quantity,
                close,
                replace_pending=True,
            )
        if self._pending_action is not None:
            return self._decision(
                ExitAction.EXIT_ORDER_PENDING,
                self._pending_reason,
                0,
                self._pending_trigger,
            )
        if self._target_touched(high, low):
            if (
                self.mode is TradeMode.SWING
                and not self._partial_done
                and self.partial_fraction > 0
                and self.remaining_quantity >= 2
            ):
                quantity = max(1, int(self.initial_quantity * self.partial_fraction))
                quantity = min(quantity, self.remaining_quantity - 1)
                return self._queue_exit(
                    ExitAction.PARTIAL_TARGET,
                    "PREDECLARED_PARTIAL_TARGET",
                    quantity,
                    self.target,
                )
            if not (
                self.mode is TradeMode.SWING
                and self._partial_done
                and self.partial_fraction > 0
            ):
                return self._queue_exit(
                    ExitAction.EXIT_FULL,
                    "TARGET",
                    self.remaining_quantity,
                    self.target,
                )

        structural_trail_applied = (
            proposed_structural_stop is not None
            and proposed_stop_known_at is not None
            and proposed_stop_known_at <= event
        )
        if structural_trail_applied:
            self.current_stop = ratchet_stop(
                direction=self.direction,
                current_stop=self.current_stop,
                proposed_stop=float(proposed_structural_stop),
            )
        if not structural_trail_applied and allow_break_even(
            running_mfe_R=self.running_mfe_R,
            favorable_structure_confirmed=favorable_structure_confirmed,
            remaining_space_R=remaining_space_R,
            config=self.config,
        ):
            self.current_stop = ratchet_stop(
                direction=self.direction,
                current_stop=self.current_stop,
                proposed_stop=self.entry,
            )
        return self._decision(ExitAction.HOLD, "", 0, None)

    def apply_exit_fill(self, *, event: EventKey, quantity: int) -> None:
        """Apply only actual exit fills; decisions alone never change position state."""
        if self._pending_action is None:
            raise ValueError("no exit order is pending")
        latest_event = max(
            value
            for value in (self._last_event, self._last_execution_event)
            if value is not None
        )
        if event <= latest_event:
            raise ValueError("exit fill must follow the latest management event")
        if quantity <= 0 or quantity > min(
            self._pending_quantity,
            self.remaining_quantity,
        ):
            raise ValueError("invalid exit fill quantity")
        pending_action = self._pending_action
        self.remaining_quantity -= quantity
        self._pending_quantity -= quantity
        self._pending_filled_quantity += quantity
        self._last_execution_event = event
        if pending_action is ExitAction.PARTIAL_TARGET:
            self._partial_done = True
        if self._pending_quantity == 0 or self.remaining_quantity == 0:
            self._clear_pending()

    def cancel_exit_order(self, *, event: EventKey, reason: str) -> None:
        if self._pending_action is None:
            raise ValueError("no exit order is pending")
        if not reason:
            raise ValueError("exit cancellation reason is required")
        latest_event = max(
            value
            for value in (self._last_event, self._last_execution_event)
            if value is not None
        )
        if event <= latest_event:
            raise ValueError("exit cancellation must follow the latest management event")
        self._last_execution_event = event
        self._clear_pending()

    def _stop_touched(self, high: float, low: float) -> bool:
        return low <= self.current_stop if self.direction == 1 else high >= self.current_stop

    def _target_touched(self, high: float, low: float) -> bool:
        return high >= self.target if self.direction == 1 else low <= self.target

    def _decision(
        self,
        action: ExitAction,
        reason: str,
        quantity: int,
        trigger_price: float | None,
    ) -> ManagementDecision:
        return ManagementDecision(
            action=action,
            reason=reason,
            quantity=quantity,
            trigger_price=trigger_price,
            stop_after_decision=self.current_stop,
            running_mfe_R=self.running_mfe_R,
        )

    def _queue_exit(
        self,
        action: ExitAction,
        reason: str,
        quantity: int,
        trigger_price: float,
        *,
        replace_pending: bool = False,
    ) -> ManagementDecision:
        if self._pending_action is not None and not replace_pending:
            raise ValueError("an exit order is already pending")
        if quantity <= 0 or not math.isfinite(trigger_price):
            raise ValueError("exit intent requires finite price and positive quantity")
        self._pending_action = action
        self._pending_reason = reason
        self._pending_quantity = quantity
        self._pending_trigger = trigger_price
        self._pending_filled_quantity = 0
        return self._decision(action, reason, quantity, trigger_price)

    def _clear_pending(self) -> None:
        self._pending_action = None
        self._pending_reason = ""
        self._pending_quantity = 0
        self._pending_trigger = None
        self._pending_filled_quantity = 0


__all__ = ["ExitAction", "ManagementDecision", "TradeManager"]
