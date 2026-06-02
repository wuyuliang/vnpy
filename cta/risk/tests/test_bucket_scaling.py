"""BucketScalingSizer 测试。

覆盖：
1. trade_count < min → mult=1.0 不动
2. trade_count >= min + pnl_bp 各档位 → 对应 mult
3. score 分数低于 edges[0] → 不进桶 不缩
4. floor 兜底
5. round-down 把 1 手缩到 0 时保留 1（不直接 BLOCK）
6. cluster/interval 缺失 → 不动
7. cfg 校验
"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.sizing.bucket_scaling import BucketScalingSizer
from cta.risk.state.bucket_pnl_tracker import BucketKey, BucketPnlTracker


def _ctx(prob_pctl: float = 75.0, cluster="metal", interval="day") -> SignalContext:
    return SignalContext(
        candidate={
            "cluster": cluster, "symbol": "CU0", "interval": interval,
            "trade_filter_prob_pctl": prob_pctl,
        },
        portfolio={"equity": 1_000_000.0},
        bar_dt=pd.Timestamp("2026-01-30"),
    )


def _stuff_bucket(tracker: BucketPnlTracker, *, count: int, per_trade_pnl: float, equity: float = 1_000_000.0) -> None:
    for i in range(count):
        tracker.on_trade({
            "cluster": "metal", "interval": "day", "score_bucket": "p70-80",
            "dt": pd.Timestamp("2026-01-15") + pd.Timedelta(hours=i),
            "net_pnl": per_trade_pnl, "portfolio_equity": equity,
        })


class TestBucketScalingSizer(unittest.TestCase):

    def setUp(self) -> None:
        self.tracker = BucketPnlTracker(window_days=30)

    def test_low_score_does_not_enter_bucket(self) -> None:
        sizer = BucketScalingSizer(self.tracker, min_trades=1)
        ctx = _ctx(prob_pctl=55.0)
        new_lots, reason = sizer.scale(ctx, 10)
        self.assertEqual(new_lots, 10)
        self.assertIn("below_edges", reason)

    def test_insufficient_trades_no_scale(self) -> None:
        sizer = BucketScalingSizer(self.tracker, min_trades=20)
        _stuff_bucket(self.tracker, count=5, per_trade_pnl=-50.0)
        new_lots, reason = sizer.scale(_ctx(), 10)
        self.assertEqual(new_lots, 10)
        self.assertIn("sample_insufficient", reason)

    def test_positive_pnl_no_scale(self) -> None:
        sizer = BucketScalingSizer(self.tracker, min_trades=5)
        _stuff_bucket(self.tracker, count=10, per_trade_pnl=200.0)
        new_lots, reason = sizer.scale(_ctx(), 10)
        # 10 trades × 200 / 1e6 * 1e4 = 20 bp ≥ 0 → mult=1
        self.assertEqual(new_lots, 10)
        self.assertIn("mult=1.00", reason)

    def test_pnl_slightly_negative_mult_0_9(self) -> None:
        # -3 bp total: 6 trades × (-50) / 1e6 * 1e4 = -3
        sizer = BucketScalingSizer(self.tracker, min_trades=5)
        _stuff_bucket(self.tracker, count=6, per_trade_pnl=-50.0)
        new_lots, reason = sizer.scale(_ctx(), 10)
        # -3 bp ∈ [-5, 0) → mult=0.9 → 10*0.9=9
        self.assertEqual(new_lots, 9)
        self.assertIn("mult=0.90", reason)

    def test_pnl_moderate_negative_mult_0_7(self) -> None:
        # -7 bp: 14 trades × -50 = -700 / 1e6 = -7e-4 = -7 bp
        sizer = BucketScalingSizer(self.tracker, min_trades=5)
        _stuff_bucket(self.tracker, count=14, per_trade_pnl=-50.0)
        new_lots, reason = sizer.scale(_ctx(), 10)
        # -7 ∈ [-10, -5) → mult=0.7 → 10*0.7=7
        self.assertEqual(new_lots, 7)
        self.assertIn("mult=0.70", reason)

    def test_pnl_heavy_negative_mult_0_5(self) -> None:
        # -15 bp: 30 trades × -50 = -1500
        sizer = BucketScalingSizer(self.tracker, min_trades=5)
        _stuff_bucket(self.tracker, count=30, per_trade_pnl=-50.0)
        new_lots, reason = sizer.scale(_ctx(), 10)
        # -15 < -10 → mult=0.5 → 10*0.5=5
        self.assertEqual(new_lots, 5)
        self.assertIn("mult=0.50", reason)

    def test_round_down_to_zero_protects_min_lot(self) -> None:
        sizer = BucketScalingSizer(self.tracker, min_trades=5)
        _stuff_bucket(self.tracker, count=30, per_trade_pnl=-50.0)
        new_lots, reason = sizer.scale(_ctx(), 1)
        # 1 * 0.5 = 0.5 → floor 0 → 但 mult>0 → 保留 1
        self.assertEqual(new_lots, 1)

    def test_missing_cluster_no_scale(self) -> None:
        sizer = BucketScalingSizer(self.tracker, min_trades=5)
        ctx = _ctx(cluster="")
        new_lots, reason = sizer.scale(ctx, 10)
        self.assertEqual(new_lots, 10)
        self.assertIn("missing_cluster_interval", reason)

    def test_invalid_mult_length_raises(self) -> None:
        with self.assertRaises(ValueError):
            BucketScalingSizer(self.tracker, bp_steps=(-5.0,), mults=(0.5,))  # 长度应 = bp_steps+1=2

    def test_non_ascending_steps_raises(self) -> None:
        with self.assertRaises(ValueError):
            BucketScalingSizer(self.tracker, bp_steps=(-5.0, -10.0), mults=(0.5, 0.7, 0.9))

    def test_floor_mult_caps_low(self) -> None:
        # 极端负 PnL，mult 默认走 0.5 但 floor=0.6 时不能低于 0.6
        sizer = BucketScalingSizer(
            self.tracker, min_trades=5, floor_mult=0.6,
        )
        _stuff_bucket(self.tracker, count=30, per_trade_pnl=-100.0)  # -30 bp
        new_lots, reason = sizer.scale(_ctx(), 10)
        # -30 < -10 → mult 默认 0.5 → 但 floor 0.6 → 10*0.6=6
        self.assertEqual(new_lots, 6)


if __name__ == "__main__":
    unittest.main()
