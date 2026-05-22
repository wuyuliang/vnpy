from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from typing import Any

import pandas as pd

from cta.model.reporting.oot_report_writer import write_oot_evaluation_report


def _touch(path: Path, text: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


class TestOotReportWriter(unittest.TestCase):
    def test_write_oot_evaluation_report_creates_layout_and_core_outputs(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_oot_report_writer_") as td:
            root = Path(td)
            bundle = root / "20260517_GROUP_POOL_CLUSTER_BOTH_portfolio_logic_runtime"
            bundle.mkdir(parents=True, exist_ok=True)

            # aggregate reports from group_pool_aggregate
            pd.DataFrame(
                {
                    "month": ["2025-01", "2025-02"],
                    "trade_count": [2, 1],
                    "win_rate": [0.5, 1.0],
                    "net_pnl": [1000.0, 800.0],
                    "return_pct": [0.001, 0.0008],
                    "cum_return_pct": [0.001, 0.0018],
                    "drawdown_pct": [0.0, -0.0002],
                }
            ).to_csv(
                bundle / "20260517_group_pool_cluster_both_aggregate_monthly_metrics.csv",
                index=False,
                encoding="utf-8-sig",
            )
            pd.DataFrame(
                {
                    "week": ["2025-01-06", "2025-01-13"],
                    "trade_count": [1, 2],
                    "win_rate": [1.0, 0.5],
                    "net_pnl": [800.0, 1000.0],
                    "return_pct": [0.0008, 0.0010],
                    "cum_return_pct": [0.0008, 0.0018],
                    "drawdown_pct": [0.0, -0.0003],
                }
            ).to_csv(
                bundle / "20260517_group_pool_cluster_both_aggregate_weekly_metrics.csv",
                index=False,
                encoding="utf-8-sig",
            )
            pd.DataFrame(
                [
                    {
                        "trade_count": 3,
                        "win_rate": 2 / 3,
                        "total_return_pct": 0.0018,
                        "monthly_sharpe": 1.2,
                        "max_dd_pct_monthly": -0.01,
                    }
                ]
            ).to_csv(
                bundle / "20260517_group_pool_cluster_both_aggregate_summary.csv",
                index=False,
                encoding="utf-8-sig",
            )

            # all trade details
            trades = pd.DataFrame(
                {
                    "datetime": pd.to_datetime(
                        [
                            "2025-01-06 09:00:00",
                            "2025-01-07 09:00:00",
                            "2025-01-08 09:00:00",
                            "2025-01-09 09:00:00",
                        ]
                    ),
                    "entry_datetime": pd.to_datetime(
                        [
                            "2025-01-06 09:00:00",
                            "2025-01-07 09:00:00",
                            "2025-01-08 09:00:00",
                            "2025-01-09 09:00:00",
                        ]
                    ),
                    "exit_datetime": pd.to_datetime(
                        [
                            "2025-01-06 11:00:00",
                            "2025-01-07 11:00:00",
                            "2025-01-08 11:00:00",
                            "2025-01-09 11:00:00",
                        ]
                    ),
                    "symbol": ["RB0", "CU0", "RB0", "CU0"],
                    "exchange": ["SHFE", "SHFE", "SHFE", "SHFE"],
                    "interval": ["60min", "60min", "day", "day"],
                    "signal_type": ["donchian_breakout", "atr_breakout", "donchian_breakout", "atr_breakout"],
                    "side": ["long", "short", "long", "short"],
                    "execution_status": ["executed", "executed", "blocked_htf_gate", "blocked_ranker"],
                    "block_reason": ["", "", "htf_missing", "ranker_dropped"],
                    "group_name": ["cluster_black", "cluster_metal", "cluster_black", "cluster_metal"],
                    "pool_name": ["GRP_CLUSTER_BLACK", "GRP_CLUSTER_METAL", "GRP_CLUSTER_BLACK", "GRP_CLUSTER_METAL"],
                    "net_pnl": [1200.0, 600.0, 0.0, 0.0],
                    "gross_pnl": [1300.0, 650.0, 0.0, 0.0],
                    "cost_pct": [0.0001, 0.0001, 0.0001, 0.0001],
                }
            )
            trades.to_csv(
                bundle / "20260517_group_pool_cluster_both_all_symbol_group_oot_trade_details.csv",
                index=False,
                encoding="utf-8-sig",
            )

            # symbol_group_details compatibility copy source
            detail_root = bundle / "symbol_group_details"
            detail_root.mkdir(parents=True, exist_ok=True)
            for cluster in ("cluster_black_60min", "cluster_metal_60min"):
                d = detail_root / cluster
                d.mkdir(parents=True, exist_ok=True)
                trades.to_csv(d / "oot_trade_details.csv", index=False, encoding="utf-8-sig")

            run_records: list[dict[str, Any]] = [
                {
                    "group_name": "cluster_black",
                    "pool_name": "GRP_CLUSTER_BLACK",
                    "interval": "60min",
                    "members": [("RB0", "SHFE")],
                },
                {
                    "group_name": "cluster_metal",
                    "pool_name": "GRP_CLUSTER_METAL",
                    "interval": "60min",
                    "members": [("CU0", "SHFE")],
                },
            ]
            report_dir = write_oot_evaluation_report(
                bundle_dir=bundle,
                run_records=run_records,
                run_tag="prod",
            )
            self.assertTrue(report_dir.exists())

            must_dirs = [
                "00_overview",
                "01_aggregate",
                "02_by_cluster",
                "03_by_symbol",
                "04_by_interval",
                "05_by_signal_type",
                "06_drilldown",
                "07_benchmark",
                "08_models",
                "09_diagnostics",
                "reports",
                "raw",
                "meta",
            ]
            for d in must_dirs:
                self.assertTrue((report_dir / d).exists(), d)

            headline = pd.read_csv(report_dir / "00_overview" / "headline_metrics.csv", encoding="utf-8-sig")
            for col in (
                "start_date",
                "end_date",
                "trade_count",
                "win_rate",
                "annualized_return_pct",
                "max_drawdown_pct",
                "monthly_sharpe",
                "calmar_like",
                "total_return_pct",
                "avg_holding_days",
                "longest_dd_recovery_days",
                "cluster_count",
                "symbol_count",
                "interval_count",
                "commission_pct_of_gross",
                "slippage_pct_of_gross",
                "git_sha",
            ):
                self.assertIn(col, headline.columns)

            cluster_cmp = pd.read_csv(report_dir / "02_by_cluster" / "_comparison.csv", encoding="utf-8-sig")
            self.assertEqual(len(cluster_cmp), 2)

            ranking = pd.read_csv(report_dir / "03_by_symbol" / "_ranking.csv", encoding="utf-8-sig")
            self.assertGreaterEqual(len(ranking), 2)
            pnl = pd.to_numeric(ranking["net_pnl"], errors="coerce").fillna(0.0).to_list()
            self.assertGreaterEqual(pnl[0], pnl[-1])

            funnel = pd.read_csv(report_dir / "06_drilldown" / "gate_funnel.csv", encoding="utf-8-sig")
            counts = pd.to_numeric(funnel["count"], errors="coerce").fillna(0).astype(int).to_list()
            self.assertTrue(all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1)))

            self.assertTrue((report_dir / "reports" / "brief.md").exists())
            self.assertTrue((report_dir / "reports" / "executive.html").exists())
            self.assertTrue((report_dir / "reports" / "analyst.html").exists())


if __name__ == "__main__":
    unittest.main()

