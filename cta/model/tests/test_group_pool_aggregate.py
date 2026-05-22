"""Tests for cta.model.reporting.group_pool_aggregate."""
from __future__ import annotations

import math
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.model.reporting.group_pool_aggregate import (
    AggregateConfig,
    build_aggregate_reports,
    write_aggregate_reports,
)


def _mk_trades(records: list[dict]) -> pd.DataFrame:
    """Helper: build a trade_details DataFrame with sensible defaults."""
    defaults = {
        "execution_status": "executed",
        "group_name": "GRP_TIER_A",
        "group_interval": "60min",
        "gross_pnl": None,  # falls back to net_pnl
    }
    rows = []
    for r in records:
        row = dict(defaults)
        row.update(r)
        if row["gross_pnl"] is None:
            row["gross_pnl"] = row.get("net_pnl", 0.0)
        rows.append(row)
    return pd.DataFrame(rows)


class TestBuildAggregateReports(unittest.TestCase):
    def test_empty_input_returns_empty_buckets_and_nan_summary(self) -> None:
        out = build_aggregate_reports(pd.DataFrame())
        self.assertTrue(out["monthly"].empty)
        self.assertTrue(out["weekly"].empty)
        self.assertEqual(int(out["summary"].iloc[0]["trade_count"]), 0)
        self.assertTrue(math.isnan(float(out["summary"].iloc[0]["win_rate"])))

    def test_filters_to_executed_trades_only(self) -> None:
        df = _mk_trades(
            [
                {"final_exit_datetime": "2026-01-15", "net_pnl": 100, "execution_status": "executed"},
                {"final_exit_datetime": "2026-01-20", "net_pnl": -50, "execution_status": "blocked"},
                {"final_exit_datetime": "2026-01-25", "net_pnl": 200, "execution_status": "filled"},
            ]
        )
        out = build_aggregate_reports(df)
        # 第 2 行 status=blocked，应被过滤
        self.assertEqual(int(out["summary"].iloc[0]["trade_count"]), 2)
        self.assertEqual(int(out["summary"].iloc[0]["win_count"]), 2)

    def test_monthly_bucket_groups_by_calendar_month(self) -> None:
        df = _mk_trades(
            [
                {"final_exit_datetime": "2026-01-05", "net_pnl": 1000},
                {"final_exit_datetime": "2026-01-25", "net_pnl": -500},
                {"final_exit_datetime": "2026-02-10", "net_pnl": 2000},
            ]
        )
        out = build_aggregate_reports(df)
        monthly = out["monthly"]
        self.assertEqual(list(monthly["month"]), ["2026-01", "2026-02"])
        self.assertEqual(list(monthly["trade_count"]), [2, 1])
        self.assertEqual(list(monthly["net_pnl"]), [500, 2000])

    def test_win_rate_and_return_pct_correct(self) -> None:
        df = _mk_trades(
            [
                {"final_exit_datetime": "2026-01-05", "net_pnl": 10_000},  # win
                {"final_exit_datetime": "2026-01-15", "net_pnl": -5_000},  # loss
                {"final_exit_datetime": "2026-01-25", "net_pnl": 5_000},   # win
            ]
        )
        cfg = AggregateConfig(initial_capital=1_000_000.0)
        out = build_aggregate_reports(df, cfg=cfg)
        monthly = out["monthly"]
        # 1 个月，3 笔，2 胜 1 负 → win_rate=2/3
        self.assertEqual(int(monthly.iloc[0]["trade_count"]), 3)
        self.assertAlmostEqual(float(monthly.iloc[0]["win_rate"]), 2 / 3)
        # net_pnl = 10000 - 5000 + 5000 = 10000，return = 10000/1_000_000 = 0.01
        self.assertAlmostEqual(float(monthly.iloc[0]["net_pnl"]), 10_000)
        self.assertAlmostEqual(float(monthly.iloc[0]["return_pct"]), 0.01)

    def test_drawdown_pct_uses_running_peak(self) -> None:
        # 月度：+10k, -30k, +5k
        df = _mk_trades(
            [
                {"final_exit_datetime": "2026-01-15", "net_pnl": 10_000},
                {"final_exit_datetime": "2026-02-15", "net_pnl": -30_000},
                {"final_exit_datetime": "2026-03-15", "net_pnl": 5_000},
            ]
        )
        cfg = AggregateConfig(initial_capital=1_000_000.0)
        out = build_aggregate_reports(df, cfg=cfg)
        monthly = out["monthly"]
        # peak = 1_010_000，谷底 = 1_010_000 - 30_000 = 980_000
        # drawdown = (980_000 - 1_010_000) / 1_010_000 ≈ -0.0297
        self.assertAlmostEqual(
            float(monthly.iloc[1]["drawdown_pct"]),
            (1_010_000 - 30_000 - 1_010_000) / 1_010_000,
            places=6,
        )
        # summary 的 max_dd_pct_monthly 应等于最小（最负）的 drawdown
        self.assertAlmostEqual(
            float(out["summary"].iloc[0]["max_dd_pct_monthly"]),
            float(monthly["drawdown_pct"].min()),
            places=6,
        )

    def test_monthly_sharpe_formula(self) -> None:
        # 构造 6 个月：每月 return = [0.02, 0.03, -0.01, 0.04, 0.02, 0.01]
        # excess (rf=0.02/12) = each - 0.001667
        # sharpe = mean(excess)/std(excess) * sqrt(12)
        net_pnls = [20_000, 30_000, -10_000, 40_000, 20_000, 10_000]
        dates = ["2026-01-15", "2026-02-15", "2026-03-15", "2026-04-15", "2026-05-15", "2026-06-15"]
        df = _mk_trades(
            [{"final_exit_datetime": d, "net_pnl": p} for d, p in zip(dates, net_pnls)]
        )
        cfg = AggregateConfig(initial_capital=1_000_000.0, risk_free_annual_return=0.02)
        out = build_aggregate_reports(df, cfg=cfg)
        rets = np.array([0.02, 0.03, -0.01, 0.04, 0.02, 0.01])
        rf_m = 0.02 / 12
        excess = rets - rf_m
        expected_sharpe = excess.mean() / excess.std(ddof=1) * math.sqrt(12)
        self.assertAlmostEqual(
            float(out["summary"].iloc[0]["monthly_sharpe"]),
            float(expected_sharpe),
            places=4,
        )

    def test_monthly_sharpe_uses_epsilon_guard_for_near_zero_std(self) -> None:
        """收益几乎常数时，Sharpe 应返回 NaN，避免被浮点噪声放大。"""
        base = 10_000.0
        eps = 1e-10
        df = _mk_trades(
            [
                {"final_exit_datetime": "2026-01-15", "net_pnl": base + eps},
                {"final_exit_datetime": "2026-02-15", "net_pnl": base - eps},
                {"final_exit_datetime": "2026-03-15", "net_pnl": base + eps},
                {"final_exit_datetime": "2026-04-15", "net_pnl": base - eps},
            ]
        )
        cfg = AggregateConfig(initial_capital=1_000_000.0, risk_free_annual_return=0.0)
        out = build_aggregate_reports(df, cfg=cfg)
        self.assertTrue(math.isnan(float(out["summary"].iloc[0]["monthly_sharpe"])))

    def test_weekly_bucket_separate_from_monthly(self) -> None:
        # 同月不同周 → monthly 合并为 1 行，weekly 应有 2 行
        df = _mk_trades(
            [
                {"final_exit_datetime": "2026-01-05", "net_pnl": 1000},  # 第 1 周
                {"final_exit_datetime": "2026-01-15", "net_pnl": -500},  # 第 3 周
            ]
        )
        out = build_aggregate_reports(df)
        self.assertEqual(len(out["monthly"]), 1)
        self.assertEqual(len(out["weekly"]), 2)
        self.assertIn("week", out["weekly"].columns)

    def test_group_count_reflects_distinct_groups(self) -> None:
        df = _mk_trades(
            [
                {"final_exit_datetime": "2026-01-05", "net_pnl": 100, "group_name": "GRP_A", "group_interval": "60min"},
                {"final_exit_datetime": "2026-01-10", "net_pnl": 200, "group_name": "GRP_B", "group_interval": "60min"},
                {"final_exit_datetime": "2026-01-15", "net_pnl": 300, "group_name": "GRP_A", "group_interval": "day"},
            ]
        )
        out = build_aggregate_reports(df)
        self.assertEqual(int(out["summary"].iloc[0]["group_count"]), 2)
        self.assertEqual(int(out["summary"].iloc[0]["interval_count"]), 2)


class TestWriteAggregateReports(unittest.TestCase):
    def test_writes_three_csv_files(self) -> None:
        df = _mk_trades(
            [
                {"final_exit_datetime": "2026-01-15", "net_pnl": 1000},
                {"final_exit_datetime": "2026-02-15", "net_pnl": -500},
            ]
        )
        with tempfile.TemporaryDirectory(prefix="cta_agg_test_") as td:
            paths = write_aggregate_reports(
                Path(td),
                df,
                run_date_tag="20260517",
                group_key="cluster",
                side_key="both",
            )
        # 路径不应再存在（tempdir 已清理），但 path 字典内容是固定的
        self.assertEqual(set(paths.keys()), {"monthly", "weekly", "summary"})
        for name, p in paths.items():
            self.assertTrue(str(p).endswith(f"_aggregate_{name}_metrics.csv") or str(p).endswith("_aggregate_summary.csv"))

    def test_csv_contents_round_trip(self) -> None:
        df = _mk_trades(
            [
                {"final_exit_datetime": "2026-01-15", "net_pnl": 1000},
                {"final_exit_datetime": "2026-02-15", "net_pnl": -500},
            ]
        )
        with tempfile.TemporaryDirectory(prefix="cta_agg_test_") as td:
            paths = write_aggregate_reports(
                Path(td),
                df,
                run_date_tag="20260517",
                group_key="cluster",
                side_key="both",
            )
            monthly = pd.read_csv(paths["monthly"], encoding="utf-8-sig")
            summary = pd.read_csv(paths["summary"], encoding="utf-8-sig")
        self.assertEqual(len(monthly), 2)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 2)
        self.assertEqual(int(summary.iloc[0]["win_count"]), 1)
        self.assertEqual(int(summary.iloc[0]["loss_count"]), 1)


class TestExecutionStatusFallback(unittest.TestCase):
    def test_no_status_column_uses_net_pnl_notna(self) -> None:
        df = pd.DataFrame(
            {
                "final_exit_datetime": ["2026-01-15", "2026-01-20", "2026-01-25"],
                "net_pnl": [100.0, np.nan, 200.0],
            }
        )
        out = build_aggregate_reports(df)
        # 第 2 行 net_pnl NaN → 被过滤
        self.assertEqual(int(out["summary"].iloc[0]["trade_count"]), 2)


if __name__ == "__main__":
    unittest.main()
