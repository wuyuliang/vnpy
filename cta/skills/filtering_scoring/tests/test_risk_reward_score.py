"""risk_reward_score.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.filtering_scoring.risk_reward_score import compute_rr, rr_gate


def _df_for_rr(n: int = 200, seed: int = 99) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    trend = 100 + np.cumsum(rng.normal(0.1, 0.8, size=n))
    close = pd.Series(trend)
    high = close + np.abs(rng.normal(0.8, 0.2, size=n))
    low = close - np.abs(rng.normal(0.8, 0.2, size=n))
    atr_14 = (high - low).rolling(14, min_periods=1).mean()
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "high": high,
            "low": low,
            "close": close,
            "atr_14": atr_14,
        }
    )


class TestRiskReward(unittest.TestCase):
    def test_long_rr(self) -> None:
        df = _df_for_rr()
        out = compute_rr(
            df=df,
            bar_idx=120,
            entry=110.0,
            stop=108.0,
            direction="long",
            lookback=50,
            atr_k=2.0,
        )
        self.assertGreater(out.rr, 0.0)
        self.assertIn(out.target_source, {"swing", "measured_move", "atr", "blended"})

    def test_short_rr(self) -> None:
        df = _df_for_rr()
        out = compute_rr(
            df=df,
            bar_idx=120,
            entry=108.0,
            stop=110.0,
            direction="short",
            lookback=40,
            atr_k=1.8,
        )
        self.assertGreater(out.rr, 0.0)
        self.assertEqual(rr_gate(out.rr, min_rr=0.1), True)

    def test_interval_compatibility(self) -> None:
        df = _df_for_rr()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = compute_rr(
                df=df,
                bar_idx=100,
                entry=109.0,
                stop=107.0,
                direction="long",
                interval=interval,
            )
            self.assertGreater(out.rr, 0.0, interval)


if __name__ == "__main__":
    unittest.main()

