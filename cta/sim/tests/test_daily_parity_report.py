"""Tests for daily parity report utility."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.sim.daily_parity_report import build_daily_parity_report
from cta.sim.parity_check import SignalRecord


class TestDailyParityReport(unittest.TestCase):
    def test_build_daily_parity_report_writes_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_daily_parity_") as td:
            out = build_daily_parity_report(
                live_signals=[
                    SignalRecord(pd.Timestamp("2024-01-02 09:00:00"), "long", 1),
                    SignalRecord(pd.Timestamp("2024-01-02 14:00:00"), "flat", 1),
                ],
                replay_signals=[
                    SignalRecord(pd.Timestamp("2024-01-02 09:00:10"), "long", 1),
                    SignalRecord(pd.Timestamp("2024-01-02 14:00:05"), "flat", 1),
                ],
                out_dir=Path(td),
                title="parity smoke",
                mismatch_alert_threshold=0.05,
            )
            self.assertTrue(Path(out.report_path).exists())
            self.assertTrue(Path(out.diff_csv_path).exists())
            self.assertFalse(bool(out.alert_triggered))
            self.assertAlmostEqual(float(out.mismatch_rate), 0.0, places=9)

    def test_build_daily_parity_report_triggers_alert_when_mismatch_high(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_daily_parity_alert_") as td:
            out = build_daily_parity_report(
                live_signals=[
                    SignalRecord(pd.Timestamp("2024-01-02 09:00:00"), "long", 1),
                ],
                replay_signals=[
                    SignalRecord(pd.Timestamp("2024-01-02 09:00:00"), "short", 1),
                ],
                out_dir=Path(td),
                title="parity alert",
                mismatch_alert_threshold=0.05,
            )
            self.assertTrue(bool(out.alert_triggered))
            self.assertGreater(float(out.mismatch_rate), 0.05)


if __name__ == "__main__":
    unittest.main()

