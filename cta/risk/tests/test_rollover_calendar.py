"""RolloverCalendar 测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.risk.state.rollover_calendar import RolloverCalendar, RolloverEntry


def _write_csv(rows: list[dict]) -> Path:
    tmpdir = Path(tempfile.mkdtemp())
    p = tmpdir / "rollover.csv"
    pd.DataFrame(rows).to_csv(p, index=False)
    return p


class TestRolloverCalendar(unittest.TestCase):

    def test_empty_from_missing_file(self) -> None:
        cal = RolloverCalendar.from_csv("/nonexistent/missing.csv")
        self.assertEqual(len(cal), 0)
        self.assertIsNone(cal.lookup("RB2401"))
        self.assertIsNone(cal.days_to_expiry("RB2401", pd.Timestamp("2026-01-01")))

    def test_load_basic_entries(self) -> None:
        p = _write_csv([
            {"symbol": "RB2401", "expiry_date": "2024-01-15", "main_switch_date": "2023-12-01"},
            {"symbol": "RB2405", "expiry_date": "2024-05-15", "main_switch_date": ""},
        ])
        cal = RolloverCalendar.from_csv(p)
        self.assertEqual(len(cal), 2)
        e = cal.lookup("RB2401")
        self.assertIsNotNone(e)
        self.assertEqual(e.expiry_date, pd.Timestamp("2024-01-15"))
        self.assertEqual(e.main_switch_date, pd.Timestamp("2023-12-01"))

    def test_days_to_expiry_positive(self) -> None:
        p = _write_csv([{"symbol": "RB2401", "expiry_date": "2024-01-15", "main_switch_date": ""}])
        cal = RolloverCalendar.from_csv(p)
        self.assertEqual(cal.days_to_expiry("RB2401", pd.Timestamp("2024-01-01")), 14)

    def test_days_to_expiry_negative_after_expiry(self) -> None:
        p = _write_csv([{"symbol": "RB2401", "expiry_date": "2024-01-15", "main_switch_date": ""}])
        cal = RolloverCalendar.from_csv(p)
        self.assertEqual(cal.days_to_expiry("RB2401", pd.Timestamp("2024-02-01")), -17)

    def test_days_since_main_switch(self) -> None:
        p = _write_csv([{"symbol": "RB2401", "expiry_date": "2024-01-15", "main_switch_date": "2023-12-01"}])
        cal = RolloverCalendar.from_csv(p)
        self.assertEqual(cal.days_since_main_switch("RB2401", pd.Timestamp("2023-12-05")), 4)

    def test_days_since_main_switch_none_when_missing(self) -> None:
        p = _write_csv([{"symbol": "RB2405", "expiry_date": "2024-05-15", "main_switch_date": ""}])
        cal = RolloverCalendar.from_csv(p)
        # 注：空字符串 main_switch_date 应被视为 None
        self.assertIsNone(cal.days_since_main_switch("RB2405", pd.Timestamp("2024-04-01")))

    def test_case_insensitive_lookup(self) -> None:
        p = _write_csv([{"symbol": "rb2401", "expiry_date": "2024-01-15", "main_switch_date": ""}])
        cal = RolloverCalendar.from_csv(p)
        self.assertIsNotNone(cal.lookup("RB2401"))
        self.assertIsNotNone(cal.lookup("rb2401"))
        self.assertIsNotNone(cal.lookup("Rb2401"))

    def test_invalid_expiry_date_skipped(self) -> None:
        p = _write_csv([
            {"symbol": "RB2401", "expiry_date": "2024-01-15", "main_switch_date": ""},
            {"symbol": "BADROW", "expiry_date": "not_a_date", "main_switch_date": ""},
        ])
        cal = RolloverCalendar.from_csv(p)
        self.assertEqual(len(cal), 1)

    def test_corrupt_csv_returns_empty(self) -> None:
        tmpdir = Path(tempfile.mkdtemp())
        p = tmpdir / "corrupt.csv"
        p.write_text("this,is,not,valid\nrows\n", encoding="utf-8")
        cal = RolloverCalendar.from_csv(p)
        # missing required columns → empty
        self.assertEqual(len(cal), 0)

    def test_from_entries_constructor(self) -> None:
        cal = RolloverCalendar([
            RolloverEntry(symbol="RB2401", expiry_date=pd.Timestamp("2024-01-15"),
                          main_switch_date=pd.Timestamp("2023-12-01")),
        ])
        self.assertEqual(cal.days_to_expiry("RB2401", pd.Timestamp("2024-01-10")), 5)


if __name__ == "__main__":
    unittest.main()
