"""Tests for live periodic/review report helpers (sim_plan3 S-J)."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from cta.live.reporting.periodic_report import write_periodic_reports
from cta.live.reporting.review_report import write_review_report


class TestPeriodicReports(unittest.TestCase):
    def test_write_periodic_reports_uses_executed_rows(self) -> None:
        trades = pd.DataFrame(
            [
                {"datetime": "2026-01-03 10:00:00", "execution_status": "filled", "net_pnl": 10.0},
                {"datetime": "2026-01-05 10:00:00", "execution_status": "blocked", "net_pnl": 999.0},
                {"datetime": "2026-01-10 10:00:00", "execution_status": "executed", "net_pnl": -5.0},
            ]
        )
        with TemporaryDirectory(prefix="periodic_report_") as td:
            out = write_periodic_reports(
                trade_details=trades,
                out_dir=Path(td),
                run_tag="sim_plan3",
                initial_capital=1_000_000.0,
            )
            self.assertTrue(Path(out["monthly"]).exists())
            self.assertTrue(Path(out["weekly"]).exists())
            self.assertTrue(Path(out["summary"]).exists())
            summary = pd.read_csv(out["summary"])
            self.assertEqual(int(summary.loc[0, "trade_count"]), 2)
            self.assertAlmostEqual(float(summary.loc[0, "net_pnl_total"]), 5.0, places=8)


class TestReviewReports(unittest.TestCase):
    def test_review_components_close_to_delta(self) -> None:
        live = pd.DataFrame(
            [
                {"datetime": "2026-01-03 10:00:00", "symbol": "RB0", "side": "long", "execution_status": "filled", "net_pnl": 10.0},
                {"datetime": "2026-01-04 10:00:00", "symbol": "RB0", "side": "long", "execution_status": "blocked", "block_reason": "risk_limit", "net_pnl": 0.0},
                {"datetime": "2026-01-05 10:00:00", "symbol": "CU0", "side": "short", "execution_status": "filled", "net_pnl": 5.0},
            ]
        )
        oot = pd.DataFrame(
            [
                {"datetime": "2026-01-03 10:00:00", "symbol": "RB0", "side": "long", "execution_status": "executed", "net_pnl": 12.0},
                {"datetime": "2026-01-04 10:00:00", "symbol": "RB0", "side": "long", "execution_status": "executed", "net_pnl": -2.0},
                {"datetime": "2026-01-06 10:00:00", "symbol": "AG0", "side": "long", "execution_status": "executed", "net_pnl": 4.0},
            ]
        )
        with TemporaryDirectory(prefix="review_report_") as td:
            out = write_review_report(
                live_trade_details=live,
                oot_trade_details=oot,
                out_dir=Path(td),
                run_tag="sim_plan3",
            )
            summary = pd.read_csv(out["summary"])
            self.assertIn("delta_pnl", summary.columns)
            self.assertIn("signal_gap_pnl", summary.columns)
            self.assertIn("execution_gap_pnl", summary.columns)
            self.assertIn("risk_gap_pnl", summary.columns)
            delta = float(summary.loc[0, "delta_pnl"])
            comp = float(summary.loc[0, "signal_gap_pnl"]) + float(summary.loc[0, "execution_gap_pnl"]) + float(summary.loc[0, "risk_gap_pnl"])
            self.assertAlmostEqual(delta, comp, places=8)
            self.assertTrue(Path(out["details"]).exists())


if __name__ == "__main__":
    unittest.main()

