"""DD-driven dynamic bump（子系统③ 阈值面）测试。

覆盖：
1. dd <= trigger → bump=0，输出 == base
2. dd 跨各阶 step → bump 累加
3. cap_pp 封顶
4. threshold_cap_pp 封顶最终输出
5. ctx 缺 effective_dd_pct 但有 drawdown_pct → 后向兼容
6. ctx 全缺 → bump=0
7. 非数值 dd → bump=0
"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.threshold.dynamic_bump import DynamicBumpAdjuster


def _ctx(dd_key: str = "effective_dd_pct", dd_val: float = 0.0) -> SignalContext:
    return SignalContext(
        candidate={"symbol": "RB0", "cluster": "black", "interval": "day"},
        portfolio={dd_key: dd_val},
        bar_dt=pd.Timestamp("2026-01-02"),
    )


class TestDynamicBump(unittest.TestCase):

    def setUp(self) -> None:
        self.adj = DynamicBumpAdjuster(
            trigger_pct=0.01, step_pct=0.01,
            pp_per_step=5.0, cap_pp=25.0,
            threshold_cap_pp=95.0,
        )

    def test_below_trigger_no_bump(self) -> None:
        out = self.adj.resolve(_ctx(dd_val=0.005), base_threshold=70.0)
        self.assertEqual(out, 70.0)

    def test_at_trigger_no_bump(self) -> None:
        out = self.adj.resolve(_ctx(dd_val=0.01), base_threshold=70.0)
        self.assertEqual(out, 70.0)

    def test_one_step_above_trigger(self) -> None:
        # dd=2% → excess=1% / step=1% = 1 step → bump=5
        out = self.adj.resolve(_ctx(dd_val=0.02), base_threshold=70.0)
        self.assertAlmostEqual(out, 75.0, places=6)

    def test_two_steps_above_trigger(self) -> None:
        out = self.adj.resolve(_ctx(dd_val=0.03), base_threshold=70.0)
        self.assertAlmostEqual(out, 80.0, places=6)

    def test_cap_pp_limit(self) -> None:
        # dd=10% → 9 steps × 5 = 45pp 但 cap=25
        out = self.adj.resolve(_ctx(dd_val=0.10), base_threshold=70.0)
        self.assertAlmostEqual(out, 95.0, places=6)

    def test_threshold_cap_pp_limit(self) -> None:
        # base=85 + bump 12pp → 97 → cap 95
        out = self.adj.resolve(_ctx(dd_val=0.035), base_threshold=85.0)
        self.assertEqual(out, 95.0)

    def test_backward_compat_drawdown_pct_key(self) -> None:
        out = self.adj.resolve(_ctx(dd_key="drawdown_pct", dd_val=0.02), base_threshold=70.0)
        self.assertAlmostEqual(out, 75.0, places=6)

    def test_no_dd_in_ctx_falls_through(self) -> None:
        ctx = SignalContext(
            candidate={"symbol": "RB0"}, portfolio={}, bar_dt=pd.Timestamp("2026-01-02"),
        )
        out = self.adj.resolve(ctx, base_threshold=70.0)
        self.assertEqual(out, 70.0)

    def test_non_numeric_dd_falls_through(self) -> None:
        ctx = _ctx(dd_val=0.0)
        ctx.portfolio["effective_dd_pct"] = "not_a_number"
        out = self.adj.resolve(ctx, base_threshold=70.0)
        # 进入 fallback dd=0 → bump=0
        self.assertEqual(out, 70.0)

    def test_compute_bump_continuous(self) -> None:
        # 直接调 helper：dd=1.5% → 0.5 step → 2.5pp
        self.assertAlmostEqual(self.adj.compute_bump(0.015), 2.5, places=6)

    def test_zero_step_pct_raises(self) -> None:
        with self.assertRaises(ValueError):
            DynamicBumpAdjuster(step_pct=0.0)


if __name__ == "__main__":
    unittest.main()
