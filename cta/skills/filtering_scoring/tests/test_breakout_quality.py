"""breakout_quality.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.filtering_scoring.breakout_quality import (
    breakout_quality_gate,
    score_breakout,
)


def _breakout_df(n: int = 140, seed: int = 123) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = np.linspace(100.0, 110.0, n)
    noise = rng.normal(0.0, 0.4, size=n)
    close = base + noise
    close[90:95] += np.linspace(0.0, 4.0, 5)  # inject breakout sequence
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0.2, 0.8, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.2, 0.8, size=n)
    volume = rng.integers(800, 1500, size=n).astype(float)
    volume[90:95] *= 2.0
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


class TestBreakoutQuality(unittest.TestCase):
    def test_breakout_score(self) -> None:
        df = _breakout_df()
        breakout_bar_idx = 92
        level = float(df["close"].iloc[:90].max())
        bq = score_breakout(
            df=df,
            breakout_bar_idx=breakout_bar_idx,
            breakout_level=level,
            atr=1.5,
        )
        self.assertTrue(0.0 <= bq.score <= 1.0)
        for key in ("s_cross", "s_vol", "s_body", "s_follow", "s_shadow"):
            self.assertIn(key, bq.components)

    def test_gate(self) -> None:
        df = _breakout_df()
        bq = score_breakout(
            df=df,
            breakout_bar_idx=92,
            breakout_level=float(df["close"].iloc[:90].max()),
            atr=1.2,
        )
        self.assertEqual(breakout_quality_gate(bq, min_score=0.0), True)

    def test_interval_compatibility(self) -> None:
        df = _breakout_df()
        level = float(df["close"].iloc[:90].max())
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            bq = score_breakout(df, 92, level, atr=1.0, interval=interval)
            self.assertTrue(0.0 <= bq.score <= 1.0, interval)


if __name__ == "__main__":
    unittest.main()

