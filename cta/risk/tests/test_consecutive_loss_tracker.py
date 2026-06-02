"""ConsecutiveLossTracker 测试。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.risk.state.consecutive_loss_tracker import ConsecutiveLossTracker, LossKey


def _trade(cluster="black", symbol="RB0", signal_type="momentum",
           dt="2026-01-02 10:00:00", net_pnl=100.0) -> dict:
    return {
        "cluster": cluster, "symbol": symbol, "signal_type": signal_type,
        "dt": pd.Timestamp(dt), "net_pnl": net_pnl,
    }


class TestLossKey(unittest.TestCase):

    def test_to_from_str_roundtrip(self) -> None:
        k = LossKey("black", "RB0", "momentum", "")
        self.assertEqual(LossKey.from_str(k.to_str()), k)

    def test_to_str_empty_fields(self) -> None:
        k = LossKey("", "RB0", "", "")
        s = k.to_str()
        self.assertIn("RB0", s)
        # roundtrip 仍 OK
        self.assertEqual(LossKey.from_str(s), k)


class TestConsecutiveLossTracker(unittest.TestCase):

    def test_no_trades_zero_loss_count(self) -> None:
        t = ConsecutiveLossTracker()
        key = t.make_key(cluster="black", symbol="RB0", signal_type="momentum")
        self.assertEqual(t.consecutive_loss_count(key), 0)

    def test_consecutive_losses_count(self) -> None:
        t = ConsecutiveLossTracker(n_consecutive_losses=3, cooldown_hours=24, lookback_days=7)
        for i in range(2):
            t.on_trade(_trade(dt=f"2026-01-0{i+1}", net_pnl=-100.0))
        key = t.make_key(cluster="black", symbol="RB0", signal_type="momentum")
        self.assertEqual(t.consecutive_loss_count(key, now=pd.Timestamp("2026-01-02")), 2)

    def test_third_consecutive_loss_triggers_cooldown(self) -> None:
        t = ConsecutiveLossTracker(n_consecutive_losses=3, cooldown_hours=24)
        for i in range(3):
            t.on_trade(_trade(dt=f"2026-01-0{i+1}", net_pnl=-100.0))
        key = t.make_key(cluster="black", symbol="RB0", signal_type="momentum")
        in_cd, until = t.is_in_cooldown(key, now=pd.Timestamp("2026-01-03"))
        self.assertTrue(in_cd)
        self.assertEqual(until, pd.Timestamp("2026-01-04"))

    def test_win_breaks_streak(self) -> None:
        t = ConsecutiveLossTracker(n_consecutive_losses=3)
        t.on_trade(_trade(dt="2026-01-01", net_pnl=-100.0))
        t.on_trade(_trade(dt="2026-01-02", net_pnl=-100.0))
        t.on_trade(_trade(dt="2026-01-03", net_pnl=50.0))    # win 打断
        key = t.make_key(cluster="black", symbol="RB0", signal_type="momentum")
        in_cd, _ = t.is_in_cooldown(key, now=pd.Timestamp("2026-01-04"))
        self.assertFalse(in_cd)
        # 当前连续亏 = 0（最近一笔 win）
        self.assertEqual(t.consecutive_loss_count(key, now=pd.Timestamp("2026-01-04")), 0)

    def test_cooldown_expires(self) -> None:
        t = ConsecutiveLossTracker(n_consecutive_losses=3, cooldown_hours=24)
        for i in range(3):
            t.on_trade(_trade(dt=f"2026-01-0{i+1}", net_pnl=-100.0))
        key = t.make_key(cluster="black", symbol="RB0", signal_type="momentum")
        # 25 小时后 → 已过 cooldown
        in_cd, _ = t.is_in_cooldown(key, now=pd.Timestamp("2026-01-04 01:00:00"))
        self.assertFalse(in_cd)

    def test_lookback_eviction(self) -> None:
        t = ConsecutiveLossTracker(n_consecutive_losses=3, lookback_days=2)
        t.on_trade(_trade(dt="2026-01-01", net_pnl=-100.0))
        t.on_trade(_trade(dt="2026-01-10", net_pnl=-100.0))
        key = t.make_key(cluster="black", symbol="RB0", signal_type="momentum")
        # 01-01 在 lookback 外 → 应被踢；只剩 1 笔
        self.assertEqual(t.consecutive_loss_count(key, now=pd.Timestamp("2026-01-10")), 1)

    def test_different_keys_isolated(self) -> None:
        t = ConsecutiveLossTracker(n_consecutive_losses=3)
        for i in range(3):
            t.on_trade(_trade(symbol="RB0", dt=f"2026-01-0{i+1}", net_pnl=-100.0))
        cu_key = t.make_key(cluster="metal", symbol="CU0", signal_type="momentum")
        rb_key = t.make_key(cluster="black", symbol="RB0", signal_type="momentum")
        cu_in_cd, _ = t.is_in_cooldown(cu_key, now=pd.Timestamp("2026-01-03"))
        rb_in_cd, _ = t.is_in_cooldown(rb_key, now=pd.Timestamp("2026-01-03"))
        self.assertFalse(cu_in_cd)
        self.assertTrue(rb_in_cd)

    def test_save_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            t = ConsecutiveLossTracker(state_path=p, n_consecutive_losses=3, cooldown_hours=24)
            for i in range(3):
                t.on_trade(_trade(dt=f"2026-01-0{i+1}", net_pnl=-100.0))
            t.save()
            t2 = ConsecutiveLossTracker.from_path(p)
        key = t2.make_key(cluster="black", symbol="RB0", signal_type="momentum")
        in_cd, until = t2.is_in_cooldown(key, now=pd.Timestamp("2026-01-03 12:00"))
        self.assertTrue(in_cd)
        self.assertEqual(until, pd.Timestamp("2026-01-04"))

    def test_load_missing_file(self) -> None:
        t = ConsecutiveLossTracker.from_path("/nonexistent/state.json")
        self.assertEqual(len(t.known_keys()), 0)

    def test_replay_batch(self) -> None:
        t = ConsecutiveLossTracker(n_consecutive_losses=3)
        trades = [_trade(dt=f"2026-01-0{i+1}", net_pnl=-100.0) for i in range(5)]
        n = t.replay(trades)
        self.assertEqual(n, 5)

    def test_apply_to_groups_subset(self) -> None:
        # 仅按 cluster 分组（不按 symbol/signal_type）
        t = ConsecutiveLossTracker(
            n_consecutive_losses=3, apply_to_groups=("cluster",),
        )
        # 不同 symbol 但同 cluster 也算同 key
        for i in range(3):
            t.on_trade(_trade(cluster="black", symbol=f"RB{i}", dt=f"2026-01-0{i+1}", net_pnl=-100.0))
        key = t.make_key(cluster="black", symbol="HC0", signal_type="anything")
        # apply_to_groups=("cluster",) → key 只看 cluster
        in_cd, _ = t.is_in_cooldown(key, now=pd.Timestamp("2026-01-03"))
        self.assertTrue(in_cd)


if __name__ == "__main__":
    unittest.main()
