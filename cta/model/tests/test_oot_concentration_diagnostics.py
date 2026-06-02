from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.model.reporting.oot_concentration import (
    compute_concentration_diagnostics,
    compute_deployable_capital_metrics,
)
from cta.model.reporting.oot_report_writer import write_oot_evaluation_report


class TestOotConcentrationDiagnostics(unittest.TestCase):
    def test_concentration_metrics_expose_fat_tail_dependency(self) -> None:
        trades = pd.DataFrame(
            {
                "execution_status": ["executed"] * 5,
                "symbol": ["EC0", "RB0", "CU0", "RB0", "AU0"],
                "net_pnl": [80_000.0, 10_000.0, 5_000.0, -1_000.0, 2_000.0],
                "datetime": pd.to_datetime(
                    ["2024-01-01", "2024-02-01", "2024-03-01", "2024-04-01", "2024-05-01"]
                ),
            }
        )
        out = compute_concentration_diagnostics(trades, initial_capital=1_000_000.0)

        self.assertAlmostEqual(float(out["net_excl_top1_pct"]), 16_000.0 / 1_000_000.0)
        self.assertAlmostEqual(float(out["top1_trade_pnl_pct"]), 80_000.0 / 96_000.0)
        self.assertGreater(float(out["top5_symbol_pnl_pct"]), 0.95)
        self.assertGreater(float(out["pnl_gini"]), 0.0)
        self.assertTrue(bool(out["concentration_warning"]))

    def test_deployable_capital_metrics_use_peak_margin_not_nominal_capital(self) -> None:
        trades = pd.DataFrame(
            {
                "execution_status": ["executed", "executed", "blocked_trade_filter"],
                "net_pnl": [10_000.0, 5_000.0, 0.0],
                "entry_margin": [120_000.0, 80_000.0, 0.0],
                "margin_used_before_entry": [0.0, 120_000.0, 0.0],
                "position_notional_after_trade": [500_000.0, 700_000.0, 0.0],
            }
        )
        out = compute_deployable_capital_metrics(trades, risk_capital_multiplier=2.0)

        self.assertAlmostEqual(float(out["peak_margin_used"]), 200_000.0)
        self.assertAlmostEqual(float(out["return_on_peak_margin"]), 15_000.0 / 200_000.0)
        self.assertAlmostEqual(float(out["return_on_risk_capital"]), 15_000.0 / 400_000.0)

    def test_writer_adds_concentration_and_deployable_metrics_to_headline(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_oot_concentration_") as td:
            root = Path(td)
            bundle = root / "bundle"
            bundle.mkdir(parents=True, exist_ok=True)
            trades = pd.DataFrame(
                {
                    "datetime": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
                    "entry_datetime": pd.to_datetime(["2024-01-01", "2024-02-01", "2024-03-01"]),
                    "exit_datetime": pd.to_datetime(["2024-01-02", "2024-02-02", "2024-03-02"]),
                    "execution_status": ["executed", "executed", "executed"],
                    "symbol": ["EC0", "RB0", "CU0"],
                    "interval": ["day", "day", "day"],
                    "signal_type": ["x", "x", "x"],
                    "group_name": ["cluster_other", "cluster_black", "cluster_metal"],
                    "net_pnl": [80_000.0, 10_000.0, -1_000.0],
                    "gross_pnl": [81_000.0, 11_000.0, -500.0],
                    "entry_margin": [100_000.0, 150_000.0, 80_000.0],
                    "margin_used_before_entry": [0.0, 100_000.0, 250_000.0],
                    "position_notional_after_trade": [300_000.0, 450_000.0, 200_000.0],
                }
            )
            trades.to_csv(
                bundle / "x_all_symbol_group_oot_trade_details.csv",
                index=False,
                encoding="utf-8-sig",
            )

            report_dir = write_oot_evaluation_report(bundle, [], run_tag="diagnostic")
            headline = pd.read_csv(report_dir / "00_overview" / "headline_metrics.csv", encoding="utf-8-sig")
            for col in (
                "net_excl_top1_pct",
                "annualized_excl_top20",
                "top1_trade_pnl_pct",
                "top5_symbol_pnl_pct",
                "pnl_gini",
                "peak_margin_used",
                "return_on_peak_margin",
                "return_on_risk_capital",
            ):
                self.assertIn(col, headline.columns)
            diag = pd.read_csv(report_dir / "09_diagnostics" / "concentration_diagnostics.csv", encoding="utf-8-sig")
            self.assertEqual(len(diag), 1)


if __name__ == "__main__":
    unittest.main()
