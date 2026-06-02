"""ScoreDistributionDriftMonitor + Guard tests (W9)."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.live.risk import RiskContext
from cta.risk.guards.config import ScoreDistributionDriftConfig
from cta.risk.guards.score_distribution_drift_guard import (
    ScoreDistributionDriftGuard,
)
from cta.risk.monitors.score_distribution_drift import ScoreDistributionDriftMonitor


def _ctx(now: str = "2026-01-05 10:00:00") -> RiskContext:
    return RiskContext(
        pos={},
        daily_pnl=0.0,
        capital=1_000_000.0,
        now=pd.Timestamp(now),
    )


def _order(offset: str = "open") -> dict:
    return {"symbol": "RB0", "direction": "long", "offset": offset, "price": 3200.0}


class TestScoreDistributionDriftMonitor(unittest.TestCase):
    def test_normal_distribution_is_not_drifted(self) -> None:
        cfg = ScoreDistributionDriftConfig(
            drift_metric="ks",
            warning_threshold=0.20,
            critical_threshold=0.40,
            emergency_threshold=0.80,
            min_samples_for_assessment=20,
            rolling_window_hours=4,
        )
        monitor = ScoreDistributionDriftMonitor(cfg, reference_scores=[0.8] * 200)
        base_dt = pd.Timestamp("2026-01-05 09:00:00")
        for i in range(30):
            monitor.observe(0.8, base_dt + pd.Timedelta(minutes=i))
        assessment = monitor.assess(base_dt + pd.Timedelta(hours=1))
        self.assertEqual(assessment.severity, "normal")
        self.assertGreaterEqual(assessment.sample_count, 20)

    def test_extreme_shift_is_emergency(self) -> None:
        cfg = ScoreDistributionDriftConfig(
            drift_metric="ks",
            warning_threshold=0.20,
            critical_threshold=0.40,
            emergency_threshold=0.80,
            min_samples_for_assessment=20,
            rolling_window_hours=4,
        )
        monitor = ScoreDistributionDriftMonitor(cfg, reference_scores=[0.8] * 200)
        base_dt = pd.Timestamp("2026-01-05 09:00:00")
        for i in range(30):
            monitor.observe(0.2, base_dt + pd.Timedelta(minutes=i))
        assessment = monitor.assess(base_dt + pd.Timedelta(hours=1))
        self.assertEqual(assessment.severity, "emergency")
        self.assertGreaterEqual(assessment.value, cfg.emergency_threshold)


class TestScoreDistributionDriftGuard(unittest.TestCase):
    def test_guard_blocks_open_on_critical_or_higher(self) -> None:
        cfg = ScoreDistributionDriftConfig(
            drift_metric="ks",
            warning_threshold=0.20,
            critical_threshold=0.40,
            emergency_threshold=0.80,
            min_samples_for_assessment=20,
            rolling_window_hours=4,
        )
        monitor = ScoreDistributionDriftMonitor(cfg, reference_scores=[0.8] * 200)
        base_dt = pd.Timestamp("2026-01-05 09:00:00")
        for i in range(30):
            monitor.observe(0.2, base_dt + pd.Timedelta(minutes=i))
        guard = ScoreDistributionDriftGuard(cfg, monitor=monitor)
        d_open = guard.check(_order(offset="open"), _ctx("2026-01-05 10:00:00"))
        d_close = guard.check(_order(offset="close"), _ctx("2026-01-05 10:00:00"))
        self.assertFalse(d_open.allowed)
        self.assertIn("emergency", d_open.reason)
        self.assertTrue(d_close.allowed)


class TestScoreDistributionDriftConfig(unittest.TestCase):
    def test_invalid_threshold_order_raises(self) -> None:
        with self.assertRaises(ValueError):
            ScoreDistributionDriftConfig(
                warning_threshold=0.5,
                critical_threshold=0.4,
                emergency_threshold=0.8,
            )

    def test_invalid_metric_raises(self) -> None:
        with self.assertRaises(ValueError):
            ScoreDistributionDriftConfig(drift_metric="invalid")


if __name__ == "__main__":
    unittest.main()
