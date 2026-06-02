"""VolatilityRegimeScaler 测试。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.sizing.config import VolatilityRegimeScalerConfig
from cta.risk.sizing.volatility_regime_scaler import VolatilityRegimeScaler


def _ctx(vol_pctl=None, vol_col="realized_vol_pctl") -> SignalContext:
    cand = {"symbol": "RB0"}
    if vol_pctl is not None:
        cand[vol_col] = vol_pctl
    return SignalContext(candidate=cand, portfolio={"equity": 1_000_000.0},
                          bar_dt=pd.Timestamp("2026-01-15"))


class TestVolatilityRegimeScaler(unittest.TestCase):

    def setUp(self) -> None:
        self.sc = VolatilityRegimeScaler()

    def test_no_vol_data_no_scale(self) -> None:
        new_lots, reason = self.sc.scale(_ctx(), 10)
        self.assertEqual(new_lots, 10)
        self.assertIn("no_vol_data", reason)

    def test_low_vol_amplifies(self) -> None:
        # vol_pctl=20 < 30 → mult=1.20 → 10*1.20=12
        new_lots, reason = self.sc.scale(_ctx(vol_pctl=20.0), 10)
        self.assertEqual(new_lots, 12)
        self.assertIn("mult=1.20", reason)

    def test_normal_vol_no_change(self) -> None:
        # vol_pctl=50 ∈ [30,70) → mult=1.0
        new_lots, _ = self.sc.scale(_ctx(vol_pctl=50.0), 10)
        self.assertEqual(new_lots, 10)

    def test_high_vol_reduces(self) -> None:
        # vol_pctl=80 ∈ [70,85) → mult=0.8 → 10*0.8=8
        new_lots, _ = self.sc.scale(_ctx(vol_pctl=80.0), 10)
        self.assertEqual(new_lots, 8)

    def test_very_high_vol_reduces_more(self) -> None:
        # vol_pctl=90 ∈ [85,95) → mult=0.6
        new_lots, _ = self.sc.scale(_ctx(vol_pctl=90.0), 10)
        self.assertEqual(new_lots, 6)

    def test_extreme_vol_floor(self) -> None:
        # vol_pctl=98 ≥ 95 → mult=0.4
        new_lots, _ = self.sc.scale(_ctx(vol_pctl=98.0), 10)
        self.assertEqual(new_lots, 4)

    def test_fraction_pctl_normalized(self) -> None:
        # 0.50 ∈ [0,1] → 当 fraction → ×100 = 50 → mult=1.0
        new_lots, _ = self.sc.scale(_ctx(vol_pctl=0.50), 10)
        self.assertEqual(new_lots, 10)

    def test_cap_mult_limits(self) -> None:
        cfg = VolatilityRegimeScalerConfig(
            vol_pctl_edges=(30.0,),
            mults=(1.50, 1.0),    # 低 vol 给 1.5
            cap_mult=1.30,
        )
        sc = VolatilityRegimeScaler(cfg)
        # 10 × 1.5 cap 1.3 = 1.3 → 13
        new_lots, _ = sc.scale(_ctx(vol_pctl=20.0), 10)
        self.assertEqual(new_lots, 13)

    def test_alternative_column_name(self) -> None:
        # atr_pctl 也应被识别
        new_lots, _ = self.sc.scale(_ctx(vol_pctl=80.0, vol_col="atr_pctl"), 10)
        self.assertEqual(new_lots, 8)

    def test_compute_mult_continuous(self) -> None:
        self.assertAlmostEqual(self.sc.compute_mult(50.0), 1.0, places=4)
        self.assertAlmostEqual(self.sc.compute_mult(80.0), 0.8, places=4)
        self.assertAlmostEqual(self.sc.compute_mult(20.0), 1.20, places=4)

    def test_negative_pctl_clipped(self) -> None:
        # 负数 → clip 到 0
        new_lots, _ = self.sc.scale(_ctx(vol_pctl=-10.0), 10)
        self.assertEqual(new_lots, 12)   # < 30 → 1.2

    def test_lots_zero_input(self) -> None:
        new_lots, reason = self.sc.scale(_ctx(vol_pctl=50.0), 0)
        self.assertEqual(new_lots, 0)
        self.assertIn("lots_already_zero", reason)


class TestVolatilityRegimeScalerConfig(unittest.TestCase):

    def test_mismatch_lengths_raises(self) -> None:
        with self.assertRaises(ValueError):
            VolatilityRegimeScalerConfig(
                vol_pctl_edges=(30.0,), mults=(1.0,),    # 长度应 = edges+1=2
            )

    def test_non_ascending_edges_raises(self) -> None:
        with self.assertRaises(ValueError):
            VolatilityRegimeScalerConfig(
                vol_pctl_edges=(70.0, 30.0),
                mults=(1.0, 1.0, 1.0),
            )

    def test_edge_out_of_range_raises(self) -> None:
        with self.assertRaises(ValueError):
            VolatilityRegimeScalerConfig(
                vol_pctl_edges=(30.0, 150.0),
                mults=(1.0, 1.0, 1.0),
            )


if __name__ == "__main__":
    unittest.main()
