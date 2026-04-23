"""mean_reversion.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.range_strategies.mean_reversion import (
    MRSignal,
    compute_zscore,
    mr_decision,
)


def _df(n: int = 220, seed: int = 29) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.sin(np.linspace(0, 20, n)) * 2 + rng.normal(0.0, 0.2, n)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close, "range_score": 0.8})


class TestMeanReversion(unittest.TestCase):
    def test_compute_zscore(self) -> None:
        out = compute_zscore(_df())
        for c in ("mr_mean", "mr_sd", "mr_z"):
            self.assertIn(c, out.columns)

    def test_mr_decision(self) -> None:
        df = compute_zscore(_df())
        sig = mr_decision(df, bar_idx=150, z_threshold=1.0, regime_gate=True)
        if sig is not None:
            self.assertIn(sig.side, {"long", "short", "flat"})

    def test_interval_compatibility(self) -> None:
        df = _df()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = compute_zscore(df, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

