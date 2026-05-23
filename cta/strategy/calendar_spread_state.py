"""Calendar spread rollover state machine."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd

from cta.data_code.main_secondary_resolver import add_months_to_contract


@dataclass(frozen=True)
class CalendarRolloverDecision:
    """Rollover decision snapshot."""

    forced: bool
    reason: str
    as_of: pd.Timestamp
    old_near_contract_code: str
    old_far_contract_code: str
    new_near_contract_code: str
    new_far_contract_code: str


class CalendarSpreadState:
    """Hold calendar spread near/far legs and rollover transition."""

    def __init__(
        self,
        *,
        pair_key: str,
        months_ahead: int = 2,
        rollover_days_before_expiry: int = 10,
    ) -> None:
        self.pair_key = str(pair_key).strip().lower()
        self.months_ahead = int(months_ahead)
        self.rollover_days_before_expiry = int(rollover_days_before_expiry)
        if self.months_ahead <= 0:
            raise ValueError("months_ahead must be > 0")
        if self.rollover_days_before_expiry <= 0:
            raise ValueError("rollover_days_before_expiry must be > 0")
        self.current_near_contract_code: str = ""
        self.current_far_contract_code: str = ""
        self.last_update_ts: pd.Timestamp | None = None
        self.last_rollover_ts: pd.Timestamp | None = None

    def resolve_pair_from_main_contract(self, main_contract_code: str) -> tuple[str, str]:
        """Infer near/far from the current main contract."""
        near = str(main_contract_code).strip().upper()
        far = add_months_to_contract(near, int(self.months_ahead))
        return near, far

    def update_current_pair(
        self,
        *,
        as_of: pd.Timestamp,
        near_contract_code: str,
        far_contract_code: str,
    ) -> None:
        self.current_near_contract_code = str(near_contract_code).strip().upper()
        self.current_far_contract_code = str(far_contract_code).strip().upper()
        self.last_update_ts = pd.Timestamp(as_of)

    def should_force_rollover(self, near_days_to_expiry: int | float | None) -> bool:
        if near_days_to_expiry is None:
            return False
        value = float(near_days_to_expiry)
        return value < float(self.rollover_days_before_expiry)

    def handle_rollover(
        self,
        *,
        as_of: pd.Timestamp,
        current_main_contract_code: str,
        near_days_to_expiry: int | float | None,
    ) -> CalendarRolloverDecision:
        """Roll state to new near/far when threshold is crossed."""
        ts = pd.Timestamp(as_of)
        old_near = str(self.current_near_contract_code or "").strip().upper()
        old_far = str(self.current_far_contract_code or "").strip().upper()
        if not self.should_force_rollover(near_days_to_expiry):
            return CalendarRolloverDecision(
                forced=False,
                reason="",
                as_of=ts,
                old_near_contract_code=old_near,
                old_far_contract_code=old_far,
                new_near_contract_code=old_near,
                new_far_contract_code=old_far,
            )
        new_near, new_far = self.resolve_pair_from_main_contract(current_main_contract_code)
        self.update_current_pair(as_of=ts, near_contract_code=new_near, far_contract_code=new_far)
        self.last_rollover_ts = ts
        return CalendarRolloverDecision(
            forced=True,
            reason="calendar_rollover_forced",
            as_of=ts,
            old_near_contract_code=old_near,
            old_far_contract_code=old_far,
            new_near_contract_code=new_near,
            new_far_contract_code=new_far,
        )


__all__ = ["CalendarSpreadState", "CalendarRolloverDecision"]

