"""Tests for oscillation range-boundary taper logic."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.portfolio_logic.config import OscillationTaperConfig, TrailingExitConfig
from cta.portfolio_logic.oscillation_taper import OscillationUpperBandTaper
from cta.portfolio_logic.trailing_exit import simulate_trailing_exit


def _bar(close: float = 109.0) -> dict[str, float | str]:
    return {
        "close": close,
        "oscillation_upper_boundary": 110.0,
        "oscillation_lower_boundary": 90.0,
    }


def _cfg(*, curve: str = "linear") -> OscillationTaperConfig:
    return OscillationTaperConfig(
        use_oscillation_upper_band_taper=True,
        taper_curve=curve,
        require_profit_to_taper=True,
        min_profit_pct_to_taper=0.005,
        enabled_by_cluster_interval={"index|day": True},
    )


class TestOscillationUpperBandTaper(unittest.TestCase):
    def test_default_disabled(self) -> None:
        got = OscillationUpperBandTaper(OscillationTaperConfig()).evaluate(
            side="long",
            entry_price=100.0,
            bar=_bar(),
            regime_label="range",
            cluster="index",
            interval="day",
        )
        self.assertFalse(got.should_taper)
        self.assertEqual(float(got.target_ratio), 1.0)

    def test_long_short_taper_and_exit_reason(self) -> None:
        taper = OscillationUpperBandTaper(_cfg())
        long = taper.evaluate(
            side="long",
            entry_price=100.0,
            bar=_bar(109.0),
            regime_label="range",
            cluster="index",
            interval="day",
        )
        short = taper.evaluate(
            side="short",
            entry_price=100.0,
            bar=_bar(91.0),
            regime_label="compression",
            cluster="index",
            interval="day",
        )
        self.assertTrue(long.should_taper)
        self.assertTrue(short.should_taper)
        self.assertLess(float(long.target_ratio), 1.0)
        self.assertEqual(long.exit_reason, "oscillation_upper_band_taper")

    def test_taper_blocks_trend_loss_and_disabled_cluster(self) -> None:
        taper = OscillationUpperBandTaper(_cfg())
        cases = (
            dict(regime_label="trend_up", cluster="index", entry_price=100.0, close=109.0),
            dict(regime_label="range", cluster="index", entry_price=112.0, close=109.0),
            dict(regime_label="range", cluster="black", entry_price=100.0, close=109.0),
        )
        for case in cases:
            with self.subTest(case=case):
                got = taper.evaluate(
                    side="long",
                    entry_price=case["entry_price"],
                    bar=_bar(case["close"]),
                    regime_label=case["regime_label"],
                    cluster=case["cluster"],
                    interval="day",
                )
                self.assertFalse(got.should_taper)

    def test_linear_and_stepwise_curves(self) -> None:
        linear = OscillationUpperBandTaper(_cfg(curve="linear")).evaluate(
            side="long",
            entry_price=100.0,
            bar=_bar(106.0),
            regime_label="range",
            cluster="index",
            interval="day",
        )
        stepwise = OscillationUpperBandTaper(_cfg(curve="stepwise")).evaluate(
            side="long",
            entry_price=100.0,
            bar=_bar(106.0),
            regime_label="range",
            cluster="index",
            interval="day",
        )
        self.assertAlmostEqual(float(linear.target_ratio), 0.6, places=6)
        self.assertEqual(float(stepwise.target_ratio), 0.5)

    def test_batch_exit_simulation_records_tapered_return(self) -> None:
        bars = pd.DataFrame(
            {
                "datetime": pd.date_range("2024-01-02", periods=24, freq="D"),
                "open": np.r_[100.0, np.linspace(101.0, 109.0, 23)],
                "high": np.linspace(101.0, 110.0, 24),
                "low": np.linspace(99.0, 105.0, 24),
                "close": np.r_[100.0, np.linspace(101.0, 109.5, 23)],
                "regime_label": ["range"] * 24,
            }
        )
        sim = simulate_trailing_exit(
            side="long",
            entry_ts=pd.Timestamp("2024-01-02"),
            planned_exit_ts=pd.Timestamp("2024-01-25"),
            entry_price_hint=100.0,
            stop_loss_pct=0.10,
            bars=bars,
            interval="day",
            atr_pct_at_entry=None,
            regime_label="range",
            cfg=TrailingExitConfig(enabled=False),
            taper_cfg=_cfg(),
            cluster="index",
        )
        self.assertGreater(int(sim["position_taper_count"]), 0)
        self.assertLess(float(sim["position_taper_target_ratio"]), 1.0)
        self.assertIn(
            str(sim["exit_reason"]),
            {"horizon_exit", "oscillation_upper_band_taper"},
        )


if __name__ == "__main__":
    unittest.main()
