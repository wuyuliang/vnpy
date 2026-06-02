"""LiquidityFloorGuard 测试。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.live.risk import RiskContext
from cta.risk.guards.config import LiquidityFloorGuardConfig
from cta.risk.guards.liquidity_floor_guard import LiquidityFloorGuard
from cta.risk.state.liquidity_snapshot_tracker import LiquiditySnapshotTracker


def _ctx() -> RiskContext:
    return RiskContext(pos={}, daily_pnl=0.0, capital=1_000_000.0, now=pd.Timestamp("2026-01-02"))


def _order(symbol="RB0", direction="long", offset="open", **kw) -> dict:
    o = {"symbol": symbol, "direction": direction, "offset": offset}
    o.update(kw)
    return o


class TestLiquidityFloorGuardOrderInline(unittest.TestCase):

    def setUp(self) -> None:
        self.guard = LiquidityFloorGuard()

    def test_no_indicators_fails_open(self) -> None:
        decision = self.guard.check(_order(), _ctx())
        self.assertTrue(decision.allowed)

    def test_volume_ratio_below_floor_blocked(self) -> None:
        decision = self.guard.check(_order(volume_ratio=0.20), _ctx())
        self.assertFalse(decision.allowed)
        self.assertIn("vol_ratio", decision.reason)

    def test_volume_ratio_above_floor_passes(self) -> None:
        decision = self.guard.check(_order(volume_ratio=0.50), _ctx())
        self.assertTrue(decision.allowed)

    def test_spread_too_wide_blocked(self) -> None:
        decision = self.guard.check(_order(bid_ask_spread_ticks=5.0), _ctx())
        self.assertFalse(decision.allowed)
        self.assertIn("spread", decision.reason)

    def test_turnover_too_low_blocked(self) -> None:
        decision = self.guard.check(_order(turnover_ratio=0.02), _ctx())
        self.assertFalse(decision.allowed)
        self.assertIn("turnover", decision.reason)

    def test_any_indicator_triggers_by_default(self) -> None:
        decision = self.guard.check(
            _order(volume_ratio=0.50, bid_ask_spread_ticks=5.0, turnover_ratio=0.10), _ctx(),
        )
        # spread 触发 → 拒（即使其他指标 OK）
        self.assertFalse(decision.allowed)

    def test_require_all_indicators(self) -> None:
        cfg = LiquidityFloorGuardConfig(require_all_indicators=True)
        guard = LiquidityFloorGuard(cfg)
        # 只有 spread 触发，其他 OK → 不拒
        decision = guard.check(
            _order(volume_ratio=0.50, bid_ask_spread_ticks=5.0, turnover_ratio=0.10), _ctx(),
        )
        self.assertTrue(decision.allowed)
        # 三个都触发 → 拒
        decision = guard.check(
            _order(volume_ratio=0.20, bid_ask_spread_ticks=5.0, turnover_ratio=0.02), _ctx(),
        )
        self.assertFalse(decision.allowed)

    def test_close_order_passes_by_default(self) -> None:
        decision = self.guard.check(
            _order(offset="close", volume_ratio=0.10, bid_ask_spread_ticks=10.0), _ctx(),
        )
        self.assertTrue(decision.allowed)

    def test_close_order_blocked_when_disallowed(self) -> None:
        cfg = LiquidityFloorGuardConfig(allow_close_orders=False)
        guard = LiquidityFloorGuard(cfg)
        decision = guard.check(_order(offset="close", volume_ratio=0.10), _ctx())
        self.assertFalse(decision.allowed)

    def test_bypass_symbol(self) -> None:
        cfg = LiquidityFloorGuardConfig(bypass_for_symbols=("RB0",))
        guard = LiquidityFloorGuard(cfg)
        decision = guard.check(_order(symbol="RB0", volume_ratio=0.05), _ctx())
        self.assertTrue(decision.allowed)
        # 但其他 symbol 仍会被拦
        decision = guard.check(_order(symbol="MA0", volume_ratio=0.05), _ctx())
        self.assertFalse(decision.allowed)


class TestLiquidityFloorGuardWithTracker(unittest.TestCase):

    def test_tracker_fallback(self) -> None:
        tracker = LiquiditySnapshotTracker()
        # 历史 4 根高量；当前 1 根低量
        for v in (1000, 1000, 1000, 1000):
            tracker.on_bar(symbol="RB0", volume=v, open_interest=10000.0, close=3000.0)
        tracker.on_bar(symbol="RB0", volume=100.0, open_interest=10000.0, close=3000.0)
        guard = LiquidityFloorGuard(tracker=tracker)
        decision = guard.check(_order(symbol="RB0"), _ctx())
        # vol_ratio = 100/1000 = 0.10 < 0.30 → 拒
        self.assertFalse(decision.allowed)

    def test_order_inline_overrides_tracker(self) -> None:
        tracker = LiquiditySnapshotTracker()
        tracker.on_bar(symbol="RB0", volume=100.0, open_interest=10000.0, close=3000.0)
        guard = LiquidityFloorGuard(tracker=tracker)
        # order 自带 volume_ratio=0.80 (高) → 应放行
        decision = guard.check(_order(symbol="RB0", volume_ratio=0.80), _ctx())
        self.assertTrue(decision.allowed)


class TestLiquidityFloorGuardConfig(unittest.TestCase):

    def test_invalid_volume_floor_raises(self) -> None:
        with self.assertRaises(ValueError):
            LiquidityFloorGuardConfig(volume_floor_ratio=0.0)
        with self.assertRaises(ValueError):
            LiquidityFloorGuardConfig(volume_floor_ratio=1.5)

    def test_invalid_spread_ticks_raises(self) -> None:
        with self.assertRaises(ValueError):
            LiquidityFloorGuardConfig(max_spread_ticks=-1)

    def test_invalid_turnover_raises(self) -> None:
        with self.assertRaises(ValueError):
            LiquidityFloorGuardConfig(min_turnover_ratio=0.0)


if __name__ == "__main__":
    unittest.main()
