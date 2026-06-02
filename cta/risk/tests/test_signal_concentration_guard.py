"""SignalConcentrationGuard 测试。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.live.risk import RiskContext
from cta.risk.guards.config import SignalConcentrationGuardConfig
from cta.risk.guards.signal_concentration_guard import SignalConcentrationGuard


def _ctx(now: str = "2026-01-02 10:00:00") -> RiskContext:
    return RiskContext(pos={}, daily_pnl=0.0, capital=1_000_000.0, now=pd.Timestamp(now))


def _order(signal_type="momentum", cluster="black", offset="open") -> dict:
    return {
        "signal_type": signal_type, "cluster": cluster, "offset": offset,
        "direction": "long",
    }


class TestSignalConcentrationGuard(unittest.TestCase):

    def setUp(self) -> None:
        cfg = SignalConcentrationGuardConfig(
            max_per_signal_type_per_bar=3,
            max_per_cluster_per_bar=2,
        )
        self.guard = SignalConcentrationGuard(cfg)

    def test_first_order_passes(self) -> None:
        decision = self.guard.check(_order(), _ctx())
        self.assertTrue(decision.allowed)

    def test_signal_type_cap_hit(self) -> None:
        # 同 bar 同 signal_type 累计：使用不同 cluster 让 cluster cap 不阻挡
        clusters = ["black", "metal", "agri", "chemical"]
        for cl in clusters[:3]:
            d = self.guard.check(_order(cluster=cl), _ctx())
            self.assertTrue(d.allowed, msg=f"cluster={cl}")
        # 第 4 个同 signal_type → 拒
        d = self.guard.check(_order(cluster=clusters[3]), _ctx())
        self.assertFalse(d.allowed)
        self.assertIn("signal_full", d.reason)

    def test_cluster_cap_hit(self) -> None:
        # 同 bar 同 cluster 累计：用不同 signal_type 让 signal cap 不阻挡
        signals = ["momentum", "breakout", "reversal"]
        for st in signals[:2]:
            d = self.guard.check(_order(signal_type=st, cluster="black"), _ctx())
            self.assertTrue(d.allowed)
        # 第 3 个同 cluster → 拒
        d = self.guard.check(_order(signal_type=signals[2], cluster="black"), _ctx())
        self.assertFalse(d.allowed)
        self.assertIn("cluster_full", d.reason)

    def test_close_orders_dont_count(self) -> None:
        # 5 个 close 都通过且不增加计数
        for _ in range(5):
            d = self.guard.check(_order(offset="close"), _ctx())
            self.assertTrue(d.allowed)
        # 接下来 1 个 open 仍能通过（计数还是 0）
        d = self.guard.check(_order(), _ctx())
        self.assertTrue(d.allowed)

    def test_new_bar_resets_counter(self) -> None:
        # 在 bar1 把 signal_type cap 顶满（用不同 cluster 避开 cluster cap）
        for cl in ("black", "metal", "agri"):
            d = self.guard.check(_order(cluster=cl), _ctx("2026-01-02 10:00:00"))
            self.assertTrue(d.allowed)
        # bar2 → counter 自动 reset
        d = self.guard.check(_order(cluster="chemical"), _ctx("2026-01-02 10:01:00"))
        self.assertTrue(d.allowed)

    def test_reset_for_new_bar_manual(self) -> None:
        for cl in ("black", "metal", "agri"):
            self.guard.check(_order(cluster=cl), _ctx())
        self.guard.reset_for_new_bar(pd.Timestamp("2026-01-02 10:01:00"))
        d = self.guard.check(_order(cluster="chemical"), _ctx("2026-01-02 10:01:00"))
        self.assertTrue(d.allowed)

    def test_unknown_signal_type_grouped_as_underscore(self) -> None:
        # 缺 signal_type 字段全部归入 "_unknown" 计数
        for cl in ("black", "metal", "agri"):
            d = self.guard.check({"cluster": cl, "offset": "open", "direction": "long"}, _ctx())
            self.assertTrue(d.allowed)
        d = self.guard.check({"cluster": "chemical", "offset": "open", "direction": "long"}, _ctx())
        self.assertFalse(d.allowed)
        self.assertIn("signal_full:_unknown", d.reason)


class TestSignalConcentrationGuardConfig(unittest.TestCase):

    def test_invalid_max_raises(self) -> None:
        with self.assertRaises(ValueError):
            SignalConcentrationGuardConfig(max_per_signal_type_per_bar=0)
        with self.assertRaises(ValueError):
            SignalConcentrationGuardConfig(max_per_cluster_per_bar=0)


if __name__ == "__main__":
    unittest.main()
