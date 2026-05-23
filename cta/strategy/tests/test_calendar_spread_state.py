"""Tests for calendar spread rollover state."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.strategy.calendar_spread_state import CalendarSpreadState


class TestCalendarSpreadState(unittest.TestCase):
    def test_initial_pair_from_main_contract(self) -> None:
        st = CalendarSpreadState(
            pair_key="rb_cal_1_3",
            months_ahead=2,
            rollover_days_before_expiry=10,
        )
        pair = st.resolve_pair_from_main_contract("RB2401.SHF")
        self.assertEqual(pair[0], "RB2401.SHF")
        self.assertEqual(pair[1], "RB2403.SHF")

    def test_should_force_rollover(self) -> None:
        st = CalendarSpreadState(
            pair_key="rb_cal_1_3",
            months_ahead=2,
            rollover_days_before_expiry=10,
        )
        self.assertTrue(st.should_force_rollover(9))
        self.assertFalse(st.should_force_rollover(10))

    def test_handle_rollover_updates_state(self) -> None:
        st = CalendarSpreadState(
            pair_key="rb_cal_1_3",
            months_ahead=2,
            rollover_days_before_expiry=10,
        )
        st.update_current_pair(
            as_of=pd.Timestamp("2024-01-10"),
            near_contract_code="RB2401.SHF",
            far_contract_code="RB2403.SHF",
        )
        decision = st.handle_rollover(
            as_of=pd.Timestamp("2024-01-11"),
            current_main_contract_code="RB2403.SHF",
            near_days_to_expiry=8,
        )
        self.assertTrue(decision.forced)
        self.assertEqual(decision.reason, "calendar_rollover_forced")
        self.assertEqual(decision.old_near_contract_code, "RB2401.SHF")
        self.assertEqual(decision.new_near_contract_code, "RB2403.SHF")
        self.assertEqual(st.current_near_contract_code, "RB2403.SHF")
        self.assertEqual(st.current_far_contract_code, "RB2405.SHF")

    def test_no_rollover_when_days_not_reached(self) -> None:
        st = CalendarSpreadState(
            pair_key="rb_cal_1_3",
            months_ahead=2,
            rollover_days_before_expiry=10,
        )
        st.update_current_pair(
            as_of=pd.Timestamp("2024-01-10"),
            near_contract_code="RB2401.SHF",
            far_contract_code="RB2403.SHF",
        )
        decision = st.handle_rollover(
            as_of=pd.Timestamp("2024-01-11"),
            current_main_contract_code="RB2401.SHF",
            near_days_to_expiry=12,
        )
        self.assertFalse(decision.forced)
        self.assertEqual(decision.reason, "")
        self.assertEqual(st.current_near_contract_code, "RB2401.SHF")


if __name__ == "__main__":
    unittest.main()

