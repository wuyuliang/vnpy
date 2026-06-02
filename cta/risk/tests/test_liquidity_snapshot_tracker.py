"""LiquiditySnapshotTracker 测试。"""
from __future__ import annotations

import unittest

from cta.risk.state.liquidity_snapshot_tracker import (
    LiquiditySnapshot,
    LiquiditySnapshotTracker,
)


class TestLiquiditySnapshotTracker(unittest.TestCase):

    def test_empty_snapshot(self) -> None:
        t = LiquiditySnapshotTracker(window_size=20)
        snap = t.snapshot("RB0")
        self.assertIsNone(snap.volume_ratio)
        self.assertIsNone(snap.spread_ticks)
        self.assertIsNone(snap.turnover_ratio)
        self.assertEqual(snap.sample_count, 0)

    def test_single_bar_snapshot(self) -> None:
        t = LiquiditySnapshotTracker(window_size=20)
        t.on_bar(symbol="RB0", volume=1000.0, open_interest=10000.0, close=3000.0)
        snap = t.snapshot("RB0")
        # 单 bar 中位数 = volume → ratio=1.0
        self.assertAlmostEqual(snap.volume_ratio, 1.0, places=6)
        self.assertAlmostEqual(snap.turnover_ratio, 1000 * 3000 / 10000, places=4)

    def test_volume_ratio_against_median(self) -> None:
        t = LiquiditySnapshotTracker(window_size=5)
        # 历史 4 根：1000 each；当前 200 → ratio 0.2
        for v in (1000, 1000, 1000, 1000):
            t.on_bar(symbol="RB0", volume=v, open_interest=10000.0, close=3000.0)
        t.on_bar(symbol="RB0", volume=200.0, open_interest=10000.0, close=3000.0)
        snap = t.snapshot("RB0")
        # 5 个值 (1000,1000,1000,1000,200) 中位数 = 1000；当前 200/1000=0.2
        self.assertAlmostEqual(snap.volume_ratio, 0.20, places=4)

    def test_window_size_limit(self) -> None:
        t = LiquiditySnapshotTracker(window_size=3)
        for v in (100, 200, 300, 400, 500):
            t.on_bar(symbol="RB0", volume=v, open_interest=10000.0, close=3000.0)
        snap = t.snapshot("RB0")
        # window=3 保留 (300, 400, 500), 中位数 = 400, 当前 500/400=1.25
        self.assertEqual(snap.sample_count, 3)
        self.assertAlmostEqual(snap.volume_ratio, 500 / 400.0, places=4)

    def test_quote_spread_ticks(self) -> None:
        t = LiquiditySnapshotTracker()
        t.on_bar(symbol="RB0", volume=1000.0, open_interest=10000.0, close=3000.0)
        t.update_quote(symbol="RB0", bid_price=2999.0, ask_price=3001.0, tick_size=1.0)
        snap = t.snapshot("RB0")
        self.assertAlmostEqual(snap.spread_ticks, 2.0, places=4)

    def test_invalid_quote_skipped(self) -> None:
        t = LiquiditySnapshotTracker()
        t.on_bar(symbol="RB0", volume=1000.0, open_interest=10000.0, close=3000.0)
        # ask < bid 异常
        t.update_quote(symbol="RB0", bid_price=3002.0, ask_price=3000.0, tick_size=1.0)
        snap = t.snapshot("RB0")
        self.assertIsNone(snap.spread_ticks)

    def test_negative_values_skipped(self) -> None:
        t = LiquiditySnapshotTracker()
        t.on_bar(symbol="RB0", volume=-100.0, open_interest=10000.0, close=3000.0)
        snap = t.snapshot("RB0")
        self.assertEqual(snap.sample_count, 0)

    def test_zero_open_interest_no_turnover(self) -> None:
        t = LiquiditySnapshotTracker()
        t.on_bar(symbol="RB0", volume=1000.0, open_interest=0.0, close=3000.0)
        snap = t.snapshot("RB0")
        self.assertIsNone(snap.turnover_ratio)

    def test_zero_close_skipped(self) -> None:
        t = LiquiditySnapshotTracker()
        t.on_bar(symbol="RB0", volume=1000.0, open_interest=10000.0, close=0.0)
        snap = t.snapshot("RB0")
        self.assertEqual(snap.sample_count, 0)

    def test_multiple_symbols_isolated(self) -> None:
        t = LiquiditySnapshotTracker()
        t.on_bar(symbol="RB0", volume=1000.0, open_interest=10000.0, close=3000.0)
        t.on_bar(symbol="CU0", volume=500.0, open_interest=5000.0, close=70000.0)
        rb = t.snapshot("RB0")
        cu = t.snapshot("CU0")
        self.assertEqual(rb.sample_count, 1)
        self.assertEqual(cu.sample_count, 1)
        self.assertNotEqual(rb.turnover_ratio, cu.turnover_ratio)

    def test_replay_batch(self) -> None:
        t = LiquiditySnapshotTracker()
        bars = [
            {"symbol": "RB0", "volume": 100 * (i + 1), "open_interest": 10000.0, "close": 3000.0}
            for i in range(5)
        ]
        n = t.replay(bars)
        self.assertEqual(n, 5)
        self.assertEqual(t.snapshot("RB0").sample_count, 5)

    def test_window_size_zero_raises(self) -> None:
        with self.assertRaises(ValueError):
            LiquiditySnapshotTracker(window_size=0)

    def test_case_insensitive_symbol(self) -> None:
        t = LiquiditySnapshotTracker()
        t.on_bar(symbol="rb0", volume=1000.0, open_interest=10000.0, close=3000.0)
        self.assertEqual(t.snapshot("RB0").sample_count, 1)


if __name__ == "__main__":
    unittest.main()
