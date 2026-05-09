"""Integration tests for compute.py pipeline behavior."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.feature.compute import compute_single_symbol_features


def _daily(n: int = 700, seed: int = 7) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2021-01-01", periods=n, freq="D")
    close = 100 + np.cumsum(rng.normal(0.04, 0.9, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.1, 1.2, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 1.2, size=n)
    volume = rng.integers(100, 10000, size=n).astype(float)
    return pd.DataFrame(
        {
            "datetime": dt,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "open_interest": rng.integers(1000, 30000, size=n).astype(float),
        }
    )


def _minute(n: int = 4000, seed: int = 8) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2024-01-01 09:00:00", periods=n, freq="1min")
    close = 100 + np.cumsum(rng.normal(0.0, 0.15, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.02, 0.2, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.02, 0.2, size=n)
    volume = rng.integers(10, 800, size=n).astype(float)
    return pd.DataFrame(
        {
            "datetime": dt,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "open_interest": rng.integers(2000, 12000, size=n).astype(float),
        }
    )


class TestComputePipeline(unittest.TestCase):
    def test_compute_single_symbol_day(self) -> None:
        out = compute_single_symbol_features(_daily(), interval="day")
        self.assertIn("trend_score", out.columns)
        self.assertIn("regime_label", out.columns)
        self.assertIn("context_score", out.columns)
        self.assertIn("rr_score", out.columns)
        self.assertGreater(len(out.columns), 350)

    def test_compute_single_symbol_minute_has_tod(self) -> None:
        out = compute_single_symbol_features(_minute(), interval="minute")
        tod_cols = [c for c in out.columns if c.startswith("tod_")]
        self.assertGreaterEqual(len(tod_cols), 70)
        self.assertIn("tod_ret_vs_1d_1min", out.columns)

    def test_compute_single_symbol_minute5_no_tod(self) -> None:
        out = compute_single_symbol_features(_minute(), interval="minute5")
        tod_cols = [c for c in out.columns if c.startswith("tod_")]
        self.assertEqual(len(tod_cols), 0)
        self.assertIn("regime_label", out.columns)

    def test_open_interest_auto_fill(self) -> None:
        df = _daily().drop(columns=["open_interest"])
        out = compute_single_symbol_features(df, interval="day")
        self.assertIn("open_interest", out.columns)
        self.assertEqual(float(out["open_interest"].iloc[0]), 0.0)


if __name__ == "__main__":
    unittest.main()

