"""Unit tests for drawdown-adaptive risk throttle."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.portfolio_logic.config import CapsConfig, PyramidConfig, RiskThrottleConfig
from cta.portfolio_logic.risk_throttle import EquityTracker, RiskThrottle


class TestRiskThrottle(unittest.TestCase):
    def test_equity_tracker_snapshot(self) -> None:
        tracker = EquityTracker()
        tracker.on_bar(100.0, pd.Timestamp("2024-01-01"))
        tracker.on_bar(110.0, pd.Timestamp("2024-01-05"))
        tracker.on_bar(99.0, pd.Timestamp("2024-01-08"))
        snap = tracker.snapshot()

        self.assertAlmostEqual(snap.drawdown_pct, 0.1, places=6)
        self.assertLess(snap.weekly_return_pct, 0.0)

    def test_compute_level_by_drawdown_and_hysteresis(self) -> None:
        throttle = RiskThrottle(RiskThrottleConfig())
        # dd=7% -> reduced
        snap = throttle.make_snapshot(drawdown_pct=0.07, weekly_return_pct=0.0, monthly_return_pct=0.0, equity=100.0)
        reduced = throttle.compute(snap, current_level=None)
        self.assertEqual(reduced.name, "reduced")

        # 想从 reduced 放宽到 normal，但 dd=4% 未低于 3%（5%-2%）=> 仍保持 reduced
        snap_relax = throttle.make_snapshot(drawdown_pct=0.04, weekly_return_pct=0.0, monthly_return_pct=0.0, equity=100.0)
        still_reduced = throttle.compute(snap_relax, current_level=reduced)
        self.assertEqual(still_reduced.name, "reduced")

    def test_weekly_loss_forces_conservative(self) -> None:
        throttle = RiskThrottle(RiskThrottleConfig())
        snap = throttle.make_snapshot(drawdown_pct=0.01, weekly_return_pct=-0.04, monthly_return_pct=-0.01, equity=100.0)
        level = throttle.compute(snap, current_level=None)
        self.assertEqual(level.name, "conservative")

    def test_apply_to_caps_scales_capacity(self) -> None:
        throttle = RiskThrottle(RiskThrottleConfig())
        base = CapsConfig(max_total_positions=10, max_total_per_cluster=4)
        reduced = throttle.level_by_name("reduced")
        scaled = throttle.apply_to_caps(base, reduced)
        self.assertEqual(scaled.max_total_positions, 7)
        self.assertEqual(scaled.max_total_per_cluster, 2)

    def test_bootstrap_from_broker_with_history_file(self) -> None:
        with tempfile.TemporaryDirectory(prefix="eq_bootstrap_") as td:
            path = Path(td) / "eq.csv"
            pd.DataFrame(
                {
                    "datetime": ["2024-01-01", "2024-01-02"],
                    "equity": [100.0, 110.0],
                }
            ).to_csv(path, index=False, encoding="utf-8-sig")
            tracker = EquityTracker.bootstrap_from_broker(95.0, history_path=path)
            snap = tracker.snapshot()
            self.assertAlmostEqual(float(snap.equity), 110.0, places=6)
            self.assertGreaterEqual(float(snap.running_high), 110.0)

    def test_apply_to_pyramid_respects_throttle_level(self) -> None:
        throttle = RiskThrottle(RiskThrottleConfig())
        base = PyramidConfig(max_active_layers=4, max_lifetime_layers=6)
        conservative = throttle.level_by_name("conservative")
        out = throttle.apply_to_pyramid(base, conservative)
        self.assertFalse(out.enabled)
        self.assertEqual(int(out.max_active_layers), 0)


if __name__ == "__main__":
    unittest.main()
