from __future__ import annotations

import unittest

import pandas as pd

from cta.model.reporting.walk_forward_diagnostics import build_walk_forward_summary


class TestWalkForwardEval(unittest.TestCase):
    def test_build_walk_forward_summary_splits_oot_period_without_using_future_windows(self) -> None:
        trades = pd.DataFrame(
            {
                "execution_status": ["executed"] * 8,
                "exit_datetime": pd.to_datetime(
                    [
                        "2024-01-10",
                        "2024-02-10",
                        "2024-04-10",
                        "2024-05-10",
                        "2024-07-10",
                        "2024-08-10",
                        "2024-10-10",
                        "2024-11-10",
                    ]
                ),
                "net_pnl": [1000.0, 1200.0, -500.0, 700.0, 2000.0, 1500.0, -800.0, -200.0],
            }
        )

        out = build_walk_forward_summary(trades, n_windows=4, initial_capital=100_000.0)

        window_rows = out.loc[out["row_type"] == "window"].copy()
        aggregate = out.loc[out["row_type"] == "aggregate"].iloc[0]
        self.assertEqual(len(window_rows), 4)
        self.assertEqual(window_rows["window_id"].tolist(), [0, 1, 2, 3])
        self.assertAlmostEqual(float(aggregate["win_window_ratio"]), 0.75)
        self.assertIn("single_segment_overfit_warning", out.columns)

    def test_forward_window_placeholder_emitted_when_no_2026_data(self) -> None:
        """2026 无数据时仍输出 [2026-01-01,2026-05-31] 前瞻窗占位行（trade_count=0），
        且 in-sample 等分窗数量与稳定性统计不受前瞻窗影响。"""
        trades = pd.DataFrame(
            {
                "execution_status": ["executed"] * 4,
                "exit_datetime": pd.to_datetime(["2024-03-01", "2024-09-01", "2025-03-01", "2025-12-30"]),
                "net_pnl": [1000.0, 2000.0, 1500.0, 800.0],
            }
        )
        out = build_walk_forward_summary(trades, n_windows=4, initial_capital=100_000.0)
        fwd = out.loc[out["row_type"] == "forward_window"]
        self.assertEqual(len(fwd), 1)
        self.assertEqual(str(fwd.iloc[0]["start_date"]), "2026-01-01")
        self.assertEqual(str(fwd.iloc[0]["end_date"]), "2026-05-31")
        self.assertEqual(int(fwd.iloc[0]["trade_count"]), 0)
        # in-sample 自动等分窗仍为 4，且范围不越过 2026
        self.assertEqual(int((out["row_type"] == "window").sum()), 4)

    def test_forward_window_populates_with_2026_trades(self) -> None:
        """补入 2026 H1 成交后，前瞻窗填入对应交易；in-sample 窗仍只覆盖 2026 前。"""
        trades = pd.DataFrame(
            {
                "execution_status": ["executed"] * 6,
                "exit_datetime": pd.to_datetime(
                    ["2024-03-01", "2024-09-01", "2025-06-01", "2025-12-30", "2026-02-15", "2026-04-20"]
                ),
                "net_pnl": [1000.0, 2000.0, 1500.0, 800.0, 400.0, 250.0],
            }
        )
        out = build_walk_forward_summary(trades, n_windows=4, initial_capital=100_000.0)
        fwd = out.loc[out["row_type"] == "forward_window"].iloc[0]
        self.assertEqual(int(fwd["trade_count"]), 2)
        self.assertAlmostEqual(float(fwd["net_pnl"]), 650.0)
        # in-sample 等分窗的右端不应越过 2026-01-01
        insample = out.loc[out["row_type"] == "window"]
        self.assertTrue((insample["end_date"].astype(str) < "2026-01-01").all())


if __name__ == "__main__":
    unittest.main()
