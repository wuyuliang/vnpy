"""breakout_score.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.regime_switch.breakout_score import (
    BreakoutScoreResult,
    calibrate_weights_from_history,
    compute_breakout_score,
)


def _synth_intraday_df(n: int = 600, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.0, 0.004, n)
    # tail breakout boost
    ret[-30:] += 0.01
    close = 100 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.002, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = rng.integers(800, 2500, n).astype(float)
    dt = pd.date_range("2024-01-01 09:00:00", periods=n, freq="5min")
    return pd.DataFrame(
        {
            "datetime": dt,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


class TestBreakoutScore(unittest.TestCase):
    def test_compute_breakout_score_output(self) -> None:
        df = _synth_intraday_df()
        res = compute_breakout_score(
            df,
            bar_idx=len(df) - 1,
            htf_same_direction=True,
            interval="minute5",
        )
        self.assertIsInstance(res, BreakoutScoreResult)
        self.assertGreaterEqual(res.score, 0.0)
        self.assertLessEqual(res.score, 1.0)
        self.assertIn("s_tight", res.parts)
        self.assertIn("s_atr", res.parts)

    def test_htf_alignment_changes_score(self) -> None:
        df = _synth_intraday_df()
        idx = len(df) - 1
        yes = compute_breakout_score(
            df, bar_idx=idx, htf_same_direction=True, interval="minute15"
        )
        no = compute_breakout_score(
            df, bar_idx=idx, htf_same_direction=False, interval="minute15"
        )
        self.assertGreaterEqual(yes.score, no.score)

    def test_interval_compatibility(self) -> None:
        df = _synth_intraday_df()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            res = compute_breakout_score(
                df, bar_idx=len(df) - 1, htf_same_direction=True, interval=interval
            )
            self.assertGreaterEqual(res.score, 0.0, interval)
            self.assertLessEqual(res.score, 1.0, interval)


class TestCalibrateWeights(unittest.TestCase):
    def test_calibrate_weights_sum_to_one(self) -> None:
        rng = np.random.default_rng(13)
        n = 300
        x1 = rng.normal(0, 1, n)
        x2 = rng.normal(0, 1, n)
        x3 = rng.normal(0, 1, n)
        y = (x1 + rng.normal(0, 0.4, n) > 0).astype(int)
        df = pd.DataFrame(
            {
                "s_tight": x1,
                "s_atr": x2,
                "s_vol": x3,
            }
        )
        w = calibrate_weights_from_history(df, pd.Series(y))
        self.assertAlmostEqual(sum(w.values()), 1.0, places=6)
        self.assertIn("s_tight", w)
        self.assertGreater(w["s_tight"], 0.2)


if __name__ == "__main__":
    unittest.main()

