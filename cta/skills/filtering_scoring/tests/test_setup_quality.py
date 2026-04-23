"""setup_quality.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.filtering_scoring.setup_quality import (
    score_setup,
    setup_quality_gate,
)


def _bars(n: int = 120, seed: int = 13) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0002, 0.01, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    open_ = np.concatenate([[close[0]], close[:-1]])
    spread = np.maximum(close * 0.003, 0.1)
    high = np.maximum(open_, close) + spread
    low = np.minimum(open_, close) - spread
    volume = rng.integers(100, 500, size=n).astype(float)
    atr_14 = pd.Series(np.abs(close - open_)).rolling(14, min_periods=1).mean() + 0.5
    df = pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "atr_14": atr_14,
            "pa_tight_range_count": rng.integers(0, 3, size=n),
            "atr_pct": rng.uniform(0.1, 0.8, size=n),
            "recent_fakes": rng.integers(0, 4, size=n),
        }
    )
    return df


class TestSetupQuality(unittest.TestCase):
    def test_score_in_range(self) -> None:
        df = _bars()
        sq = score_setup(df, bar_idx=60, setup_type="tight_range")
        self.assertTrue(0.0 <= sq.score <= 1.0)
        self.assertEqual(sq.setup_type, "tight_range")
        for k in ("s_body", "s_shadow", "s_prior", "s_atr", "s_clean"):
            self.assertIn(k, sq.components)
            self.assertTrue(0.0 <= sq.components[k] <= 1.0, k)

    def test_gate(self) -> None:
        df = _bars()
        sq = score_setup(df, bar_idx=70, setup_type="bp")
        self.assertEqual(setup_quality_gate(sq, min_score=0.0), True)
        self.assertEqual(setup_quality_gate(sq, min_score=1.0), sq.score >= 1.0)

    def test_interval_compatibility(self) -> None:
        df = _bars()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            sq = score_setup(df, bar_idx=50, setup_type="bull_flag", interval=interval)
            self.assertTrue(0.0 <= sq.score <= 1.0, interval)


if __name__ == "__main__":
    unittest.main()

