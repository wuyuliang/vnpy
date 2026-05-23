"""Tests for spread feature helpers."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.feature.spread_features import (
    compute_dynamic_hedge_ratio,
    compute_log_spread,
    compute_spread_zscore,
)


class TestSpreadFeatures(unittest.TestCase):
    def test_log_spread_basic(self) -> None:
        price1 = pd.Series([100.0, 110.0, 121.0])
        price2 = pd.Series([100.0, 100.0, 100.0])
        out = compute_log_spread(price1, price2)
        exp = pd.Series(np.log([1.0, 1.1, 1.21]))
        self.assertTrue(np.allclose(out.to_numpy(), exp.to_numpy(), atol=1e-12, equal_nan=True))

    def test_log_spread_handles_nan(self) -> None:
        price1 = pd.Series([100.0, np.nan, 121.0])
        price2 = pd.Series([100.0, 100.0, np.nan])
        out = compute_log_spread(price1, price2)
        self.assertTrue(np.isfinite(float(out.iloc[0])))
        self.assertTrue(pd.isna(out.iloc[1]))
        self.assertTrue(pd.isna(out.iloc[2]))

    def test_zscore_basic(self) -> None:
        spread = pd.Series([0.0, 1.0, 2.0, 3.0, 4.0])
        out = compute_spread_zscore(spread, rolling_window_days=3)
        # idx=4 uses window [2, 3, 4], mean=3, std=sqrt(2/3)
        exp = (4.0 - 3.0) / np.sqrt(2.0 / 3.0)
        self.assertAlmostEqual(float(out.iloc[-1]), float(exp), places=10)

    def test_zscore_warmup_returns_nan(self) -> None:
        spread = pd.Series([1.0, 2.0, 3.0, 4.0])
        out = compute_spread_zscore(spread, rolling_window_days=3)
        self.assertTrue(pd.isna(out.iloc[0]))
        self.assertTrue(pd.isna(out.iloc[1]))
        self.assertTrue(np.isfinite(float(out.iloc[2])))

    def test_dynamic_hedge_ratio_basic(self) -> None:
        price2 = pd.Series(np.arange(1.0, 21.0))
        price1 = 2.0 * price2 + 1.0
        beta = compute_dynamic_hedge_ratio(price1, price2, window_days=10)
        self.assertAlmostEqual(float(beta.dropna().iloc[-1]), 2.0, places=6)


if __name__ == "__main__":
    unittest.main()

