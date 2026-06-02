"""Tests for cta.live.reporting.build_reports CLI."""
from __future__ import annotations

import unittest
from pathlib import Path
from tempfile import TemporaryDirectory

import pandas as pd

from cta.live.reporting.build_reports import main


class TestReportingCli(unittest.TestCase):
    def test_main_writes_periodic_and_review_outputs(self) -> None:
        with TemporaryDirectory(prefix="live_reporting_cli_") as td:
            root = Path(td)
            live_csv = root / "live.csv"
            oot_csv = root / "oot.csv"
            pd.DataFrame(
                [
                    {"datetime": "2026-01-02 10:00:00", "symbol": "RB0", "side": "long", "execution_status": "filled", "net_pnl": 12.0}
                ]
            ).to_csv(live_csv, index=False)
            pd.DataFrame(
                [
                    {"datetime": "2026-01-02 10:00:00", "symbol": "RB0", "side": "long", "execution_status": "executed", "net_pnl": 11.0}
                ]
            ).to_csv(oot_csv, index=False)
            out_dir = root / "out"
            rc = main(
                [
                    "--live-trades-csv",
                    str(live_csv),
                    "--oot-trades-csv",
                    str(oot_csv),
                    "--out-dir",
                    str(out_dir),
                    "--run-tag",
                    "sim_plan3",
                ]
            )
            self.assertEqual(rc, 0)
            self.assertTrue((out_dir / "sim_plan3_live_summary.csv").exists())
            self.assertTrue((out_dir / "sim_plan3_live_vs_oot_review_summary.csv").exists())


if __name__ == "__main__":
    unittest.main()

