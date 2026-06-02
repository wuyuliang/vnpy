"""LimitMoveGuard 测试。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.live.risk import RiskContext
from cta.risk.guards.config import LimitMoveGuardConfig
from cta.risk.guards.limit_move_guard import LimitMoveGuard


def _ctx() -> RiskContext:
    return RiskContext(pos={}, daily_pnl=0.0, capital=1_000_000.0, now=pd.Timestamp("2026-01-02"))


def _order(symbol="RB0", direction="long", offset="open",
           price=3000.0, prev_close=3000.0, cluster=None) -> dict:
    o = {
        "symbol": symbol, "direction": direction, "offset": offset,
        "price": price, "prev_close": prev_close,
    }
    if cluster:
        o["cluster"] = cluster
    return o


class TestLimitMoveGuard(unittest.TestCase):

    def setUp(self) -> None:
        # RB → black cluster → limit_pct=0.07
        self.guard = LimitMoveGuard()

    def test_normal_price_passes(self) -> None:
        decision = self.guard.check(_order(price=3000.0, prev_close=3000.0), _ctx())
        self.assertTrue(decision.allowed)

    def test_at_upper_limit_blocks_long_open(self) -> None:
        # RB → black cluster, limit_pct=0.06 → 涨停 = 3000 × 1.06 = 3180
        decision = self.guard.check(_order(price=3180.0, prev_close=3000.0, direction="long"), _ctx())
        self.assertFalse(decision.allowed)
        self.assertIn("at_upper", decision.reason)

    def test_at_lower_limit_blocks_short_open(self) -> None:
        # 跌停 = 3000 × 0.94 = 2820
        decision = self.guard.check(_order(price=2820.0, prev_close=3000.0, direction="short"), _ctx())
        self.assertFalse(decision.allowed)
        self.assertIn("at_lower", decision.reason)

    def test_at_upper_limit_blocks_short_open_too(self) -> None:
        # 涨停时开 short 也拒（追涨不智）
        decision = self.guard.check(_order(price=3180.0, prev_close=3000.0, direction="short"), _ctx())
        self.assertFalse(decision.allowed)

    def test_near_upper_blocks_open(self) -> None:
        # upper=3180; upper_near=3180×0.997=3170.46; price=3175 ∈ (3170.46, 3180) → near_upper
        decision = self.guard.check(_order(price=3175.0, prev_close=3000.0), _ctx())
        self.assertFalse(decision.allowed)
        self.assertIn("near_upper", decision.reason)

    def test_close_at_limit_default_passes(self) -> None:
        decision = self.guard.check(
            _order(price=3180.0, prev_close=3000.0, direction="long", offset="close"), _ctx(),
        )
        self.assertTrue(decision.allowed)

    def test_close_at_limit_blocked_if_cfg_strict(self) -> None:
        cfg = LimitMoveGuardConfig(block_close_at_unfavorable_limit=True)
        guard = LimitMoveGuard(cfg)
        decision = guard.check(
            _order(price=3180.0, prev_close=3000.0, direction="long", offset="close"), _ctx(),
        )
        self.assertFalse(decision.allowed)

    def test_missing_prev_close_fails_open(self) -> None:
        decision = self.guard.check(_order(price=3000.0, prev_close=0.0), _ctx())
        self.assertTrue(decision.allowed)

    def test_missing_price_fails_open(self) -> None:
        decision = self.guard.check(_order(price=0.0, prev_close=3000.0), _ctx())
        self.assertTrue(decision.allowed)

    def test_cluster_override(self) -> None:
        # 给 bond 自定义更小 limit_pct=0.015 (国债)
        cfg = LimitMoveGuardConfig(limit_pct_by_cluster={"bond": 0.015})
        guard = LimitMoveGuard(cfg)
        # T0 → bond cluster → 用 override 0.015
        # 涨停价 100 × 1.015 = 101.5
        decision = guard.check(
            _order(symbol="T0", price=101.5, prev_close=100.0, cluster="bond"), _ctx(),
        )
        self.assertFalse(decision.allowed)
        self.assertIn("at_upper", decision.reason)

    def test_cluster_inferred_from_symbol(self) -> None:
        # RB 默认推断为 black cluster → limit 0.06 → upper=3180
        decision = self.guard.check(_order(symbol="RB0", price=3180.0, prev_close=3000.0), _ctx())
        self.assertFalse(decision.allowed)

    def test_vt_symbol_field_used(self) -> None:
        order = _order(price=3180.0, prev_close=3000.0)
        order.pop("symbol")
        order["vt_symbol"] = "RB0.SHFE"
        decision = self.guard.check(order, _ctx())
        self.assertFalse(decision.allowed)

    def test_unrecognizable_order_fails_open(self) -> None:
        decision = self.guard.check({}, _ctx())
        self.assertTrue(decision.allowed)

    def test_invalid_cfg_raises(self) -> None:
        with self.assertRaises(ValueError):
            LimitMoveGuardConfig(near_limit_tolerance_pct=0.5)   # > 5%
        with self.assertRaises(ValueError):
            LimitMoveGuardConfig(limit_pct_by_cluster={"bond": 0.30})  # > 20%

    def test_block_open_when_at_false_allows(self) -> None:
        cfg = LimitMoveGuardConfig(block_open_when_at=False, block_open_when_near=False)
        guard = LimitMoveGuard(cfg)
        decision = guard.check(_order(price=3180.0, prev_close=3000.0), _ctx())
        self.assertTrue(decision.allowed)


if __name__ == "__main__":
    unittest.main()
