"""ma_trend_following.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.trend_strategies.ma_trend_following import (
    MASignal,
    compute_ma_features,
    ma_decision,
)


def _bars(n: int = 240, seed: int = 16) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.09, 0.4, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


class TestMATrendFollowing(unittest.TestCase):
    def test_compute_ma_features(self) -> None:
        out = compute_ma_features(_bars(), n_fast=20, n_slow=60)
        for c in ("ma_fast", "ma_slow", "ma_slope_fast", "ma_slope_slow"):
            self.assertIn(c, out.columns)

    def test_ma_decision(self) -> None:
        df = compute_ma_features(_bars())
        sig = ma_decision(df, bar_idx=180, require_slope=True)
        if sig is not None:
            self.assertIn(sig.side, {"long", "short", "flat"})

    def test_interval_compatibility(self) -> None:
        df = _bars()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = compute_ma_features(df, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

