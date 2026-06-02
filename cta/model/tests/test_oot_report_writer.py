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
    def test_summary_includes_reproducibility_info_when_provided(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_oot_report_repro_") as td:
            bundle = Path(td) / "bundle"
            bundle.mkdir(parents=True, exist_ok=True)
            pd.DataFrame(
                {
                    "datetime": pd.to_datetime(["2025-01-02"]),
                    "exit_datetime": pd.to_datetime(["2025-01-02"]),
                    "symbol": ["RB0"],
                    "interval": ["day"],
                    "signal_type": ["donchian_breakout"],
                    "execution_status": ["executed"],
                    "group_name": ["cluster_black"],
                    "net_pnl": [1000.0],
                    "gross_pnl": [1100.0],
                    "cost_pct": [0.0001],
                }
            ).to_csv(
                bundle / "unit_all_symbol_group_oot_trade_details.csv",
                index=False,
                encoding="utf-8-sig",
            )

            report_dir = write_oot_evaluation_report(
                bundle_dir=bundle,
                run_records=[],
                run_tag="repro",
                reproducibility_info={
                    "argv": ["--from-root", "cta/backtest", "--note", "strict quality check"],
                    "generated_at": "2026-05-30T10:00:00+08:00",
                    "note": "strict quality check",
                    "key_cfg": {"use_impact_cost": True, "impact_cost_k": 0.1},
                },
            )

            text = (report_dir / "00_overview" / "executive_summary.md").read_text(encoding="utf-8")
            self.assertIn("## 复现信息", text)
            self.assertIn("--from-root cta/backtest --note 'strict quality check'", text)
            self.assertIn("2026-05-30T10:00:00+08:00", text)
            self.assertIn("strict quality check", text)
            self.assertIn("use_impact_cost=True", text)

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
                "cost_to_gross_ratio",
                "slippage_pct_of_gross",
                "hard_stop_rate",
                "same_bar_stop_rate",
                "open_notional_at_entry_p95_pct",
                "git_sha",
            ):
                self.assertIn(col, headline.columns)

            cluster_cmp = pd.read_csv(report_dir / "02_by_cluster" / "_comparison.csv", encoding="utf-8-sig")
            self.assertEqual(len(cluster_cmp), 2)

            ranking = pd.read_csv(report_dir / "03_by_symbol" / "_ranking.csv", encoding="utf-8-sig")
            self.assertGreaterEqual(len(ranking), 2)
            pnl = pd.to_numeric(ranking["net_pnl"], errors="coerce").fillna(0.0).to_list()
            self.assertGreaterEqual(pnl[0], pnl[-1])

            by_signal = pd.read_csv(
                report_dir / "05_by_signal_type" / "_comparison.csv",
                encoding="utf-8-sig",
            )
            self.assertIn("avg_net_pnl_per_trade", by_signal.columns)
            for col in (
                "max_drawdown_pct",
                "monthly_sharpe",
                "annualized_return_pct",
                "calmar_like",
                "top5_trade_pnl_pct",
                "top5_symbol_pnl_pct",
            ):
                self.assertIn(col, by_signal.columns)
            avg = pd.to_numeric(by_signal["avg_net_pnl_per_trade"], errors="coerce")
            net = pd.to_numeric(by_signal["net_pnl"], errors="coerce")
            cnt = pd.to_numeric(by_signal["trade_count"], errors="coerce")
            check = (avg * cnt - net).abs().fillna(0.0)
            self.assertLessEqual(float(check.max()), 1e-9)
            top5_trade = pd.to_numeric(by_signal["top5_trade_pnl_pct"], errors="coerce")
            top5_symbol = pd.to_numeric(by_signal["top5_symbol_pnl_pct"], errors="coerce")
            if top5_trade.notna().any():
                self.assertTrue(bool(((top5_trade >= 0.0) & (top5_trade <= 1.0)).all()))
            if top5_symbol.notna().any():
                self.assertTrue(bool(((top5_symbol >= 0.0) & (top5_symbol <= 1.0)).all()))

            funnel = pd.read_csv(report_dir / "06_drilldown" / "gate_funnel.csv", encoding="utf-8-sig")
            counts = pd.to_numeric(funnel["count"], errors="coerce").fillna(0).astype(int).to_list()
            self.assertTrue(all(counts[i] >= counts[i + 1] for i in range(len(counts) - 1)))

            self.assertTrue((report_dir / "reports" / "brief.md").exists())
            self.assertTrue((report_dir / "reports" / "executive.html").exists())
            self.assertTrue((report_dir / "reports" / "analyst.html").exists())

    def test_write_oot_evaluation_report_writes_daily_position_time_distributions(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_oot_report_dist_") as td:
            root = Path(td)
            bundle = root / "bundle"
            bundle.mkdir(parents=True, exist_ok=True)

            pd.DataFrame(
                {
                    "datetime": pd.to_datetime(
                        [
                            "2025-01-06 09:00:00",
                            "2025-01-07 09:00:00",
                            "2025-01-08 09:00:00",
                        ]
                    ),
                    "entry_datetime": pd.to_datetime(
                        [
                            "2025-01-06 09:00:00",
                            "2025-01-07 09:00:00",
                            "2025-01-08 09:00:00",
                        ]
                    ),
                    "exit_datetime": pd.to_datetime(
                        [
                            "2025-01-06 10:00:00",
                            "2025-01-07 10:00:00",
                            "2025-01-08 10:00:00",
                        ]
                    ),
                    "symbol": ["RB0", "CU0", "RB0"],
                    "interval": ["day", "day", "day"],
                    "signal_type": ["donchian_breakout", "atr_breakout", "atr_breakout"],
                    "group_name": ["cluster_black", "cluster_metal", "cluster_black"],
                    "execution_status": ["executed", "executed", "blocked_trade_filter"],
                    "block_reason": ["", "", "blocked_trade_filter"],
                    "net_pnl": [1200.0, -400.0, 0.0],
                    "gross_pnl": [1300.0, -350.0, 0.0],
                }
            ).to_csv(
                bundle / "unit_all_symbol_group_oot_trade_details.csv",
                index=False,
                encoding="utf-8-sig",
            )

            report_dir = write_oot_evaluation_report(bundle_dir=bundle, run_records=[], run_tag="dist")
            diag = report_dir / "09_diagnostics"

            daily = pd.read_csv(diag / "daily_trade_position_distribution.csv", encoding="utf-8-sig")
            monthly = pd.read_csv(diag / "monthly_trade_position_distribution.csv", encoding="utf-8-sig")
            weekday = pd.read_csv(diag / "weekday_trade_position_distribution.csv", encoding="utf-8-sig")
            execution_risk = pd.read_csv(diag / "execution_risk_diagnostics.csv", encoding="utf-8-sig")
            risk_panel = pd.read_csv(diag / "risk_four_panel.csv", encoding="utf-8-sig")

            self.assertEqual(int(daily["trade_count"].sum()), 2)
            self.assertEqual(int(monthly["total_trade_count"].sum()), 2)
            self.assertEqual(int(weekday["days"].sum()), len(daily))
            self.assertEqual(int(execution_risk.iloc[0]["executed_trade_count"]), 2)
            self.assertEqual(int(risk_panel["trade_count"].sum()), 2)

            for col in (
                "position_notional_mean_pct",
                "margin_used_after_trade_mean_pct",
                "open_notional_at_entry_p95_pct",
                "hard_stop_rate",
                "same_bar_stop_rate",
                "eod_position_notional_pct",
                "eod_margin_used_after_trade_pct",
                "day_net_pnl",
            ):
                self.assertIn(col, daily.columns)

            for col in (
                "avg_eod_position_notional_pct",
                "max_eod_position_notional_pct",
                "avg_eod_margin_used_pct",
                "avg_open_notional_at_entry_p95_pct",
                "avg_hard_stop_rate",
                "avg_same_bar_stop_rate",
                "month_net_pnl",
            ):
                self.assertIn(col, monthly.columns)

            for col in (
                "weekday_num",
                "weekday",
                "avg_eod_position_notional_pct",
                "avg_eod_margin_used_pct",
                "avg_open_notional_at_entry_p95_pct",
                "avg_hard_stop_rate",
                "avg_same_bar_stop_rate",
            ):
                self.assertIn(col, weekday.columns)

            for col in (
                "hard_stop_rate",
                "same_bar_stop_rate",
                "open_notional_at_entry_p95_pct",
                "worst5_trade_net_pnl_sum",
            ):
                self.assertIn(col, execution_risk.columns)

            for col in (
                "open_notional_at_entry_p95_pct",
                "worst_trade_net_pnl",
                "hard_stop_rate",
                "same_bar_stop_rate",
                "top5_symbol_pnl_pct",
            ):
                self.assertIn(col, risk_panel.columns)


if __name__ == "__main__":
    unittest.main()
