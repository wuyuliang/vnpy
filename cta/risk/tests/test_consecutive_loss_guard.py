"""ConsecutiveLossGuard 测试。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.live.risk import RiskContext
from cta.risk.guards.config import ConsecutiveLossGuardConfig
from cta.risk.guards.consecutive_loss_guard import ConsecutiveLossGuard
from cta.risk.state.consecutive_loss_tracker import ConsecutiveLossTracker


def _ctx(now: str = "2026-01-03") -> RiskContext:
    return RiskContext(pos={}, daily_pnl=0.0, capital=1_000_000.0, now=pd.Timestamp(now))


def _order(cluster="black", symbol="RB0", signal_type="momentum",
           offset="open", direction="long") -> dict:
    return {
        "cluster": cluster, "symbol": symbol, "signal_type": signal_type,
        "offset": offset, "direction": direction,
    }


def _stuff_tracker_losses(tracker: ConsecutiveLossTracker, n: int) -> None:
    for i in range(n):
        tracker.on_trade({
            "cluster": "black", "symbol": "RB0", "signal_type": "momentum",
            "dt": pd.Timestamp("2026-01-01") + pd.Timedelta(days=i),
            "net_pnl": -100.0,
        })


class TestConsecutiveLossGuard(unittest.TestCase):

    def test_no_trades_passes(self) -> None:
        guard = ConsecutiveLossGuard()
        decision = guard.check(_order(), _ctx())
        self.assertTrue(decision.allowed)

    def test_three_losses_triggers_cooldown_block(self) -> None:
        tracker = ConsecutiveLossTracker(n_consecutive_losses=3, cooldown_hours=24)
        _stuff_tracker_losses(tracker, 3)
        guard = ConsecutiveLossGuard(tracker=tracker)
        # 第 4 个开仓 → 应被 cooldown 拦
        decision = guard.check(_order(), _ctx("2026-01-03 12:00"))
        self.assertFalse(decision.allowed)
        self.assertIn("cooldown", decision.reason)

    def test_cooldown_expires_after_24h(self) -> None:
        tracker = ConsecutiveLossTracker(n_consecutive_losses=3, cooldown_hours=24)
        _stuff_tracker_losses(tracker, 3)
        guard = ConsecutiveLossGuard(tracker=tracker)
        # 26 小时后
        decision = guard.check(_order(), _ctx("2026-01-04 02:00"))
        self.assertTrue(decision.allowed)

    def test_close_passes_by_default(self) -> None:
        tracker = ConsecutiveLossTracker(n_consecutive_losses=3, cooldown_hours=24)
        _stuff_tracker_losses(tracker, 3)
        guard = ConsecutiveLossGuard(tracker=tracker)
        decision = guard.check(_order(offset="close"), _ctx("2026-01-03 12:00"))
        self.assertTrue(decision.allowed)

    def test_close_blocked_when_cfg(self) -> None:
        cfg = ConsecutiveLossGuardConfig(block_close_orders=True)
        tracker = ConsecutiveLossTracker(n_consecutive_losses=3, cooldown_hours=24)
        _stuff_tracker_losses(tracker, 3)
        guard = ConsecutiveLossGuard(cfg, tracker=tracker)
        decision = guard.check(_order(offset="close"), _ctx("2026-01-03 12:00"))
        self.assertFalse(decision.allowed)

    def test_different_key_not_blocked(self) -> None:
        tracker = ConsecutiveLossTracker(n_consecutive_losses=3, cooldown_hours=24)
        _stuff_tracker_losses(tracker, 3)
        guard = ConsecutiveLossGuard(tracker=tracker)
        # 不同 symbol → 不同 key → 不拦
        decision = guard.check(_order(symbol="CU0", cluster="metal"), _ctx("2026-01-03 12:00"))
        self.assertTrue(decision.allowed)


class TestConsecutiveLossGuardConfig(unittest.TestCase):

    def test_invalid_n_raises(self) -> None:
        with self.assertRaises(ValueError):
            ConsecutiveLossGuardConfig(n_consecutive_losses=0)

    def test_invalid_apply_to_groups_raises(self) -> None:
        with self.assertRaises(ValueError):
            ConsecutiveLossGuardConfig(apply_to_groups=("invalid_field",))


if __name__ == "__main__":
    unittest.main()
