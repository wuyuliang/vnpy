"""LinearDdScaler 测试。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.sizing.linear_dd_scaler import LinearDdScaler


def _ctx(dd_pct: float, key: str = "effective_dd_pct") -> SignalContext:
    return SignalContext(
        candidate={"symbol": "RB0"},
        portfolio={key: dd_pct},
        bar_dt=pd.Timestamp("2026-01-02"),
    )


class TestLinearDdScaler(unittest.TestCase):

    def setUp(self) -> None:
        self.sc = LinearDdScaler(
            trigger_pct=0.01, step_pct=0.01,
            step_mult=0.10, floor_mult=0.10,
        )

    def test_dd_below_trigger_no_scale(self) -> None:
        new_lots, _ = self.sc.scale(_ctx(0.005), 10)
        self.assertEqual(new_lots, 10)

    def test_dd_at_trigger_no_scale(self) -> None:
        new_lots, _ = self.sc.scale(_ctx(0.01), 10)
        self.assertEqual(new_lots, 10)

    def test_dd_1pct_above_trigger_mult_0_9(self) -> None:
        # dd=2% → 1 step → 1 - 0.1 = 0.9 → 10*0.9=9
        new_lots, reason = self.sc.scale(_ctx(0.02), 10)
        self.assertEqual(new_lots, 9)
        self.assertIn("mult=0.90", reason)

    def test_dd_2pct_above_trigger_mult_0_8(self) -> None:
        new_lots, _ = self.sc.scale(_ctx(0.03), 10)
        self.assertEqual(new_lots, 8)

    def test_dd_9pct_above_trigger_floor(self) -> None:
        # dd=10% → 9 steps → mult=1-0.9=0.1 → 等于 floor
        new_lots, _ = self.sc.scale(_ctx(0.10), 10)
        self.assertEqual(new_lots, 1)

    def test_dd_huge_clamped_to_floor(self) -> None:
        # dd=50% → 49 steps → mult would be -3.9, floor=0.1
        new_lots, _ = self.sc.scale(_ctx(0.50), 10)
        self.assertEqual(new_lots, 1)

    def test_round_down_preserves_min_lot(self) -> None:
        # 1 * 0.5 = 0.5 → 1
        new_lots, _ = self.sc.scale(_ctx(0.06), 1)
        self.assertEqual(new_lots, 1)

    def test_backward_compat_drawdown_pct(self) -> None:
        new_lots, _ = self.sc.scale(_ctx(0.02, key="drawdown_pct"), 10)
        self.assertEqual(new_lots, 9)

    def test_compute_mult_continuous(self) -> None:
        # dd=1.5% → 0.5 step → 1 - 0.05 = 0.95
        self.assertAlmostEqual(self.sc.compute_mult(0.015), 0.95, places=6)

    def test_lots_zero_input_returns_zero(self) -> None:
        new_lots, reason = self.sc.scale(_ctx(0.05), 0)
        self.assertEqual(new_lots, 0)
        self.assertIn("lots_already_zero", reason)

    def test_invalid_step_pct_raises(self) -> None:
        with self.assertRaises(ValueError):
            LinearDdScaler(step_pct=0.0)

    def test_hysteresis_keeps_reduction_until_release_band(self) -> None:
        sc = LinearDdScaler(
            trigger_pct=0.01,
            step_pct=0.01,
            step_mult=0.10,
            floor_mult=0.10,
            hysteresis_pct=0.005,
        )
        # 先触发较深回撤，进入 0.8 档
        lots1, _ = sc.scale(_ctx(0.03), 10)
        self.assertEqual(lots1, 8)
        # 回撤回升到 trigger 上方，仍保持原档（不立即放松）
        lots2, _ = sc.scale(_ctx(0.012), 10)
        self.assertEqual(lots2, 8)
        # 落到 trigger 与 release 之间，仍保持原档
        lots3, _ = sc.scale(_ctx(0.008), 10)
        self.assertEqual(lots3, 8)
        # 低于 release=trigger-hysteresis 后才解除
        lots4, _ = sc.scale(_ctx(0.004), 10)
        self.assertEqual(lots4, 10)


if __name__ == "__main__":
    unittest.main()
