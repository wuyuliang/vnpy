"""ExecutionQualityTracker + scaler tests (W10)."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.sizing.config import ExecutionQualityConfig
from cta.risk.sizing.execution_quality_scaler import ExecutionQualityScaler
from cta.risk.state.execution_quality_tracker import ExecutionQualityTracker


def _ctx(symbol: str = "RB0", dt: str = "2026-01-05 10:00:00") -> SignalContext:
    return SignalContext(
        candidate={"symbol": symbol, "cluster": "black", "interval": "day"},
        portfolio={"equity": 1_000_000.0},
        bar_dt=pd.Timestamp(dt),
    )


class TestExecutionQualityScaler(unittest.TestCase):
    def setUp(self) -> None:
        self.cfg = ExecutionQualityConfig(
            slippage_ratio_threshold=3.0,
            reject_rate_threshold=0.05,
            avg_price_deviation_threshold=0.005,
            rolling_window_days=7,
            mults=(0.5, 0.7, 0.3),
            min_trades_for_assessment=10,
        )
        self.tracker = ExecutionQualityTracker(self.cfg)
        self.scaler = ExecutionQualityScaler(self.cfg, tracker=self.tracker)

    def test_insufficient_samples_no_scale(self) -> None:
        for i in range(5):
            self.tracker.on_execution(
                {
                    "dt": pd.Timestamp("2026-01-05 09:00:00") + pd.Timedelta(minutes=i),
                    "symbol": "RB0",
                    "expected_slippage_bp": 1.0,
                    "actual_slippage_bp": 1.1,
                    "is_rejected": False,
                    "price_deviation_pct": 0.001,
                }
            )
        new_lots, reason = self.scaler.scale(_ctx("RB0"), 10)
        self.assertEqual(new_lots, 10)
        self.assertIn("sample_insufficient", reason)

    def test_high_slippage_scales_down(self) -> None:
        for i in range(12):
            self.tracker.on_execution(
                {
                    "dt": pd.Timestamp("2026-01-05 09:00:00") + pd.Timedelta(minutes=i),
                    "symbol": "RB0",
                    "expected_slippage_bp": 1.0,
                    "actual_slippage_bp": 4.0,  # ratio=4.0 > 3.0
                    "is_rejected": False,
                    "price_deviation_pct": 0.001,
                }
            )
        new_lots, reason = self.scaler.scale(_ctx("RB0"), 10)
        self.assertEqual(new_lots, 5)
        self.assertIn("slippage", reason)

    def test_multiple_triggers_take_most_conservative_mult(self) -> None:
        for i in range(12):
            self.tracker.on_execution(
                {
                    "dt": pd.Timestamp("2026-01-05 09:00:00") + pd.Timedelta(minutes=i),
                    "symbol": "RB0",
                    "expected_slippage_bp": 1.0,
                    "actual_slippage_bp": 4.0,  # trigger mult 0.5
                    "is_rejected": i % 3 == 0,  # reject rate 33% -> trigger mult 0.7
                    "price_deviation_pct": 0.008,  # trigger mult 0.3
                }
            )
        new_lots, reason = self.scaler.scale(_ctx("RB0"), 10)
        self.assertEqual(new_lots, 3)
        self.assertIn("deviation", reason)


class TestExecutionQualityConfig(unittest.TestCase):
    def test_invalid_multipliers_raises(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionQualityConfig(mults=(0.5, 0.7))

    def test_invalid_window_raises(self) -> None:
        with self.assertRaises(ValueError):
            ExecutionQualityConfig(rolling_window_days=0)


if __name__ == "__main__":
    unittest.main()
