"""DdSignalProvider 测试。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.portfolio_logic.risk_throttle import EquityTracker
from cta.risk.state.dd_signal_provider import DdSignalProvider


class TestDdSignalProviderStatic(unittest.TestCase):

    def test_static_values_returned(self) -> None:
        p = DdSignalProvider.from_static(cumulative_dd_pct=0.05, weekly_dd_pct=0.02)
        self.assertEqual(p.cumulative_dd_pct(), 0.05)
        self.assertEqual(p.weekly_dd_pct(), 0.02)
        self.assertEqual(p.effective_dd_pct(), 0.05)

    def test_effective_max_weekly(self) -> None:
        p = DdSignalProvider.from_static(cumulative_dd_pct=0.02, weekly_dd_pct=0.05)
        self.assertEqual(p.effective_dd_pct(), 0.05)

    def test_zero_static_default(self) -> None:
        p = DdSignalProvider.from_static()
        self.assertEqual(p.cumulative_dd_pct(), 0.0)
        self.assertEqual(p.weekly_dd_pct(), 0.0)
        self.assertEqual(p.effective_dd_pct(), 0.0)

    def test_set_static_partial_update(self) -> None:
        p = DdSignalProvider.from_static(cumulative_dd_pct=0.05, weekly_dd_pct=0.02)
        p.set_static(weekly_dd_pct=0.10)
        self.assertEqual(p.cumulative_dd_pct(), 0.05)
        self.assertEqual(p.weekly_dd_pct(), 0.10)
        self.assertEqual(p.effective_dd_pct(), 0.10)


class TestDdSignalProviderTracker(unittest.TestCase):

    def setUp(self) -> None:
        self.tracker = EquityTracker()
        # 一周走势：高点 100w → 跌到 95w
        for i, eq in enumerate([1_000_000, 980_000, 950_000]):
            self.tracker.on_bar(eq, pd.Timestamp("2026-01-01") + pd.Timedelta(days=i))

    def test_cumulative_dd_from_tracker(self) -> None:
        p = DdSignalProvider(self.tracker)
        # 95w / 100w = 0.95 → dd 0.05
        self.assertAlmostEqual(p.cumulative_dd_pct(), 0.05, places=6)

    def test_weekly_dd_within_window(self) -> None:
        p = DdSignalProvider(self.tracker, weekly_window_days=7)
        # 7天内 max=100w，当下 95w → 0.05
        self.assertAlmostEqual(p.weekly_dd_pct(), 0.05, places=6)

    def test_weekly_dd_outside_window(self) -> None:
        # 历史 100w 在 30 天前；近期回升不会被 weekly window 看见
        tracker = EquityTracker()
        tracker.on_bar(1_000_000, pd.Timestamp("2026-01-01"))
        tracker.on_bar(900_000, pd.Timestamp("2026-02-10"))   # 40 天后回落
        tracker.on_bar(880_000, pd.Timestamp("2026-02-15"))
        p = DdSignalProvider(tracker, weekly_window_days=7)
        # 7天 window 内 max=900_000，当下 880_000 → dd ≈ 0.022
        self.assertAlmostEqual(p.weekly_dd_pct(), 1 - 880_000 / 900_000, places=4)
        # cumulative dd 应是 880_000 / 1_000_000 = 0.12
        self.assertAlmostEqual(p.cumulative_dd_pct(), 0.12, places=4)

    def test_empty_tracker_returns_zero(self) -> None:
        p = DdSignalProvider(EquityTracker())
        self.assertEqual(p.cumulative_dd_pct(), 0.0)
        self.assertEqual(p.weekly_dd_pct(), 0.0)
        self.assertEqual(p.effective_dd_pct(), 0.0)

    def test_none_tracker_fail_open(self) -> None:
        p = DdSignalProvider(None)
        self.assertEqual(p.cumulative_dd_pct(), 0.0)
        self.assertEqual(p.weekly_dd_pct(), 0.0)


if __name__ == "__main__":
    unittest.main()
