"""Smoke tests for feature modules with synthetic OHLCV data."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.feature.calendar_feat import compute_calendar_features
from cta.feature.composite import compute_composite_features
from cta.feature.cross_section import compute_cross_section_features
from cta.feature.entry_stop import compute_entry_stop_features
from cta.feature.minute_tod import compute_minute_tod_features
from cta.feature.momentum import compute_momentum_features
from cta.feature.multi_timeframe import compute_multi_timeframe_features
from cta.feature.pattern import compute_pattern_features
from cta.feature.price_action import compute_price_action_features
from cta.feature.price_action_advanced import compute_price_action_advanced_features
from cta.feature.price_action_context import compute_price_action_context_features
from cta.feature.price_action_supplement import compute_price_action_supplement_features
from cta.feature.regime import compute_regime_features
from cta.feature.stats_feat import compute_stats_features
from cta.feature.trend import compute_trend_features
from cta.feature.volatility import compute_volatility_features
from cta.feature.volatility_regime import compute_volatility_regime_features
from cta.feature.volume import compute_volume_features


def _daily_ohlcv(n: int = 650, seed: int = 42) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2022-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(rng.normal(0.03, 1.1, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.2, 1.8, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.2, 1.8, size=n)
    volume = rng.integers(300, 5000, size=n).astype(float)
    oi = rng.integers(8000, 35000, size=n).astype(float)
    return pd.DataFrame(
        {
            "datetime": dt,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "open_interest": oi,
        }
    )


def _minute_ohlcv(n: int = 3000, seed: int = 43) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2024-01-01 09:00:00", periods=n, freq="1min")
    close = 100 + np.cumsum(rng.normal(0.0, 0.12, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.02, 0.25, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.02, 0.25, size=n)
    volume = rng.integers(10, 600, size=n).astype(float)
    oi = rng.integers(2000, 9000, size=n).astype(float)
    return pd.DataFrame(
        {
            "datetime": dt,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "open_interest": oi,
        }
    )


class TestFeatureModuleSmoke(unittest.TestCase):
    def setUp(self) -> None:
        self.df = _daily_ohlcv()

    def test_trend_momentum_volatility_volume_pattern_calendar(self) -> None:
        trend = compute_trend_features(self.df)
        momentum = compute_momentum_features(self.df)
        vol = compute_volatility_features(self.df)
        volume = compute_volume_features(self.df)
        pattern = compute_pattern_features(self.df)
        cal = compute_calendar_features(self.df)

        self.assertIn("macd_dif", trend.columns)
        self.assertIn("rsi_14", momentum.columns)
        self.assertIn("atr_14", vol.columns)
        self.assertIn("obv", volume.columns)
        self.assertIn("inside_bar", pattern.columns)
        self.assertIn("dow", cal.columns)
        self.assertEqual(len(trend), len(self.df))

    def test_price_action_family_stats_regime_composite(self) -> None:
        pa = compute_price_action_features(self.df)
        pa_ctx = compute_price_action_context_features(self.df)
        pa_adv = compute_price_action_advanced_features(self.df)
        pa_sup = compute_price_action_supplement_features(self.df)
        stats = compute_stats_features(self.df)
        vol_reg = compute_volatility_regime_features(self.df)
        mtf = compute_multi_timeframe_features(self.df, interval="day")
        entry_stop = compute_entry_stop_features(self.df)

        joined = pd.concat(
            [self.df, pa, pa_ctx, pa_adv, pa_sup, stats, vol_reg, mtf, entry_stop],
            axis=1,
        )
        regime = compute_regime_features(joined)
        comp = compute_composite_features(pd.concat([joined, regime], axis=1))

        self.assertIn("pa_bar_range", pa.columns)
        self.assertIn("pa_breakout_fail_20", pa.columns)
        self.assertIn("pa_range_over_atr_20", pa_ctx.columns)
        self.assertIn("pa_htf_align", pa_adv.columns)
        self.assertIn("pa_bull_flag_flag", pa_sup.columns)
        self.assertIn("hurst_20", stats.columns)
        self.assertIn("vol_regime_3", vol_reg.columns)
        self.assertIn("mtf_align_flag", mtf.columns)
        self.assertIn("atr_based_stop_long", entry_stop.columns)
        self.assertIn("regime_label", regime.columns)
        self.assertIn("context_score", comp.columns)

    def test_minute_tod_only_on_1min(self) -> None:
        mdf = _minute_ohlcv()
        tod = compute_minute_tod_features(mdf)
        self.assertGreaterEqual(len(tod.columns), 70)
        self.assertIn("tod_ret_vs_1d_1min", tod.columns)

    def test_cross_section_smoke(self) -> None:
        n = 220
        dt = pd.date_range("2024-01-01", periods=n, freq="D")
        rows = []
        rng = np.random.default_rng(99)
        for sym, drift in (("RB0", 0.06), ("CU0", 0.03), ("M0", -0.01)):
            close = 100 + np.cumsum(rng.normal(drift, 0.8, size=n))
            for i in range(n):
                rows.append(
                    {
                        "datetime": dt[i],
                        "symbol": sym,
                        "close": close[i],
                        "volume": float(rng.integers(200, 5000)),
                        "open_interest": float(rng.integers(10000, 50000)),
                    }
                )
        panel = pd.DataFrame(rows)
        cs = compute_cross_section_features(panel)
        self.assertIn("cs_ret_rank_20", cs.columns)
        self.assertIn("cs_ret_zscore_20", cs.columns)


if __name__ == "__main__":
    unittest.main()
