"""DailyVaRBudgetSizer + tracker tests (W10)."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.sizing.config import DailyVaRBudgetConfig
from cta.risk.sizing.daily_var_budget import DailyVaRBudgetSizer
from cta.risk.state.daily_var_tracker import DailyVaRTracker


def _ctx(
    *,
    cluster: str = "metal",
    daily_pnl: float = 0.0,
    by_cluster: dict[str, float] | None = None,
) -> SignalContext:
    return SignalContext(
        candidate={"symbol": "CU0", "cluster": cluster, "interval": "day"},
        portfolio={
            "equity": 1_000_000.0,
            "daily_pnl": daily_pnl,
            "daily_pnl_by_cluster": by_cluster or {},
        },
        bar_dt=pd.Timestamp("2026-01-05 10:00:00"),
    )


class TestDailyVaRBudgetSizer(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = DailyVaRBudgetConfig(
            budget_bp_by_cluster={"metal": 80.0},
            default_budget_bp=60.0,
            account_budget_bp=250.0,
            warning_ratio=0.95,
            warning_mult=0.5,
        )
        self.sizer = DailyVaRBudgetSizer(self.cfg)

    def test_warning_zone_scales_down(self) -> None:
        # metal budget=80bp on 1,000,000 => 8,000; warning@95%=7,600
        new_lots, reason = self.sizer.scale(
            _ctx(by_cluster={"metal": -7_700.0}),
            10,
        )
        self.assertEqual(new_lots, 5)
        self.assertIn("warning", reason)

    def test_cluster_hard_stop_blocks(self) -> None:
        new_lots, reason = self.sizer.scale(
            _ctx(by_cluster={"metal": -8_100.0}),
            10,
        )
        self.assertEqual(new_lots, 0)
        self.assertIn("cluster_hard_stop", reason)

    def test_account_hard_stop_blocks(self) -> None:
        new_lots, reason = self.sizer.scale(
            _ctx(daily_pnl=-26_000.0, by_cluster={"metal": -1_000.0}),
            10,
        )
        self.assertEqual(new_lots, 0)
        self.assertIn("account_hard_stop", reason)


class TestDailyVaRTracker(unittest.TestCase):
    def test_tracker_accumulates_and_resets_on_new_day(self) -> None:
        tr = DailyVaRTracker()
        tr.on_trade(
            {
                "dt": "2026-01-05 10:00:00",
                "cluster": "metal",
                "net_pnl": -1000.0,
            }
        )
        tr.on_trade(
            {
                "dt": "2026-01-05 11:00:00",
                "cluster": "metal",
                "net_pnl": -500.0,
            }
        )
        self.assertAlmostEqual(tr.account_daily_pnl(pd.Timestamp("2026-01-05 12:00:00")), -1500.0)
        self.assertAlmostEqual(tr.cluster_daily_pnl("metal", pd.Timestamp("2026-01-05 12:00:00")), -1500.0)
        # 新交易日自动 reset
        self.assertEqual(tr.account_daily_pnl(pd.Timestamp("2026-01-06 09:00:00")), 0.0)


class TestDailyVaRBudgetConfig(unittest.TestCase):
    def test_invalid_warning_ratio_raises(self) -> None:
        with self.assertRaises(ValueError):
            DailyVaRBudgetConfig(warning_ratio=1.2)


if __name__ == "__main__":
    unittest.main()
