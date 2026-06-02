"""SignalTypePositionScaler 单测：按 signal_type 缩放 lots（sim/live 三层一致）。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.sizing.config import SignalTypeSizeScalerConfig
from cta.risk.sizing.signal_type_size_scaler import SignalTypePositionScaler


def _ctx(signal_type: str) -> SignalContext:
    return SignalContext(
        candidate={"signal_type": signal_type, "symbol": "RB0", "interval": "60min"},
        portfolio={"equity": 1_000_000.0},
        bar_dt=pd.Timestamp("2024-01-02 09:00:00"),
    )


class TestSignalTypeSizeScalerConfig(unittest.TestCase):
    def test_defaults_normalized_and_frozen(self) -> None:
        cfg = SignalTypeSizeScalerConfig()
        self.assertAlmostEqual(cfg.multiplier_by_signal_type["bull_pullback_continuation"], 4.0)
        self.assertAlmostEqual(cfg.multiplier_by_signal_type["breakout_pullback_continuation"], 3.0)
        self.assertAlmostEqual(cfg.multiplier_by_signal_type["cross_sectional_momentum"], 0.8)
        self.assertAlmostEqual(cfg.multiplier_by_signal_type["trend_acceleration_breakout"], 0.5)
        self.assertAlmostEqual(cfg.multiplier_by_signal_type["atr_breakout"], 0.2)
        self.assertAlmostEqual(cfg.multiplier_by_signal_type["donchian_breakout"], 0.25)
        self.assertAlmostEqual(cfg.multiplier_by_signal_type["tight_range_breakout"], 0.15)
        # 键归一化：大写/空格
        cfg2 = SignalTypeSizeScalerConfig(multiplier_by_signal_type={"  ATR_Breakout ": 0.5})
        self.assertIn("atr_breakout", cfg2.multiplier_by_signal_type)

    def test_rejects_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            SignalTypeSizeScalerConfig(multiplier_by_signal_type={"atr_breakout": 0.0})
        with self.assertRaises(ValueError):
            SignalTypeSizeScalerConfig(multiplier_by_signal_type={"atr_breakout": 5.1})


class TestSignalTypePositionScaler(unittest.TestCase):
    def setUp(self) -> None:
        self.scaler = SignalTypePositionScaler()

    def test_pullback_amplified_atr_shrunk(self) -> None:
        bull_lots, _ = self.scaler.scale(_ctx("bull_pullback_continuation"), 10)
        atr_lots, _ = self.scaler.scale(_ctx("atr_breakout"), 10)
        self.assertEqual(bull_lots, 40)   # ×4.0
        self.assertEqual(atr_lots, 2)     # ×0.2
        self.assertGreater(bull_lots, atr_lots)

    def test_unknown_signal_type_unchanged(self) -> None:
        lots, reason = self.scaler.scale(_ctx("mystery_signal"), 10)
        self.assertEqual(lots, 10)
        self.assertIn("mult=1.00", reason)

    def test_missing_signal_type_fail_open(self) -> None:
        lots, reason = self.scaler.scale(_ctx(""), 10)
        self.assertEqual(lots, 10)
        self.assertIn("no_signal_type", reason)

    def test_zero_lots_passthrough(self) -> None:
        lots, _ = self.scaler.scale(_ctx("bull_pullback_continuation"), 0)
        self.assertEqual(lots, 0)

    def test_shrink_keeps_at_least_one_lot(self) -> None:
        # 1 手 ×0.2 = floor 0 → 保留 1 手（mult>0）
        lots, _ = self.scaler.scale(_ctx("atr_breakout"), 1)
        self.assertEqual(lots, 1)


if __name__ == "__main__":
    unittest.main()
