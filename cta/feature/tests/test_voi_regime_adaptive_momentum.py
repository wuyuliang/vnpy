"""Tests for regime-adaptive VOI momentum features."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.config.voi_momentum_config import VoiMomentumConfig
from cta.feature.compute import compute_single_symbol_features
from cta.feature.run_all_features import _voi_cfg_from_enabled_cells
from cta.feature.voi_momentum import (
    classify_vol_regime,
    compute_adaptive_momentum,
    compute_voi_features,
)


def _bars(n: int = 90) -> pd.DataFrame:
    dt = pd.date_range("2023-01-01", periods=n, freq="D")
    close = pd.Series(np.linspace(100.0, 125.0, n))
    return pd.DataFrame(
        {
            "datetime": dt,
            "symbol": "IF0",
            "cluster": "index",
            "open": close.shift(1).fillna(close.iloc[0]),
            "high": close + 1.0,
            "low": close - 1.0,
            "close": close,
            "volume": np.linspace(1000.0, 3000.0, n),
            "open_interest": 10_000.0,
        }
    )


class TestVoiRegimeAdaptiveMomentum(unittest.TestCase):
    def test_default_disabled(self) -> None:
        out = compute_voi_features(_bars(), cfg=VoiMomentumConfig(), cluster="index", interval="day")
        self.assertEqual(list(out.columns), [])

    def test_classify_vol_regime_high_low_mid(self) -> None:
        close = pd.Series(
            np.r_[
                np.linspace(100.0, 102.0, 20),
                102.0 + np.sin(np.linspace(0.0, 12.0, 20)) * 15.0,
                np.linspace(101.0, 102.0, 20),
            ]
        )
        regime = classify_vol_regime(
            close,
            vol_window=3,
            rank_window=8,
            high_threshold=0.70,
            low_threshold=0.30,
        )
        self.assertIn("high", set(regime.dropna()))
        self.assertIn("low", set(regime.dropna()))
        self.assertIn("mid", set(regime.dropna()))

    def test_adaptive_momentum_uses_regime_window_and_mid_outputs_zero(self) -> None:
        close = pd.Series(np.linspace(100.0, 140.0, 30))
        regime = pd.Series(["mid"] * 30)
        regime.iloc[10] = "high"
        regime.iloc[25] = "low"
        out = compute_adaptive_momentum(close, regime, fast_window=2, slow_window=5)
        self.assertAlmostEqual(float(out.iloc[10]), float(close.pct_change(2).iloc[10]))
        self.assertAlmostEqual(float(out.iloc[25]), float(close.pct_change(5).iloc[25]))
        self.assertEqual(float(out.iloc[20]), 0.0)

    def test_composite_ranges_and_enabled_cluster_interval(self) -> None:
        cfg = VoiMomentumConfig(
            use_voi_regime_adaptive_momentum=True,
            vol_window=3,
            rank_window=12,
            fast_momentum_window=2,
            slow_momentum_window=5,
            volume_rank_window=8,
            enabled_by_cluster_interval={"index|day": True},
        )
        out = compute_voi_features(_bars(), cfg=cfg, cluster="index", interval="day")
        self.assertIn("voi_momentum_signed_score", out.columns)
        self.assertTrue(out["voi_intraday_position_factor"].dropna().between(-1.0, 1.0).all())
        self.assertTrue(out["voi_volume_rank"].dropna().between(0.0, 1.0).all())
        mid = out["voi_vol_regime"].astype(str) == "mid"
        self.assertTrue((out.loc[mid, "voi_momentum_signed_score"].fillna(0.0) == 0.0).all())

        off_group = compute_voi_features(_bars(), cfg=cfg, cluster="black", interval="day")
        self.assertEqual(list(off_group.columns), [])

    def test_compute_pipeline_only_adds_voi_columns_when_enabled(self) -> None:
        bars = _bars(280)
        disabled = compute_single_symbol_features(bars, interval="day")
        self.assertNotIn("voi_momentum_signed_score", disabled.columns)

        enabled = compute_single_symbol_features(
            bars,
            interval="day",
            voi_cfg=VoiMomentumConfig(
                use_voi_regime_adaptive_momentum=True,
                rank_window=20,
                enabled_by_cluster_interval={"index|day": True},
            ),
            cluster="index",
        )
        self.assertIn("voi_momentum_signed_score", enabled.columns)

    def test_batch_entry_builds_opt_in_config_from_cells(self) -> None:
        cfg = _voi_cfg_from_enabled_cells(["index|day,black|60min"])
        self.assertTrue(cfg.is_enabled("index", "day"))
        self.assertTrue(cfg.is_enabled("black", "minute60"))
        self.assertFalse(cfg.is_enabled("metal", "day"))


if __name__ == "__main__":
    unittest.main()
