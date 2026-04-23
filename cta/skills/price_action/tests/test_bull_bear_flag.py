"""bull_bear_flag.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.price_action.bull_bear_flag import FlagSetup, detect_flag, flag_breakout_trigger


def _trend_df(n: int = 220, seed: int = 12) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = np.linspace(100, 125, n)
    noise = rng.normal(0.0, 0.25, size=n)
    close = base + noise
    close[120:135] -= np.linspace(0.0, 2.0, 15)  # pullback segment
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.25
    low = np.minimum(open_, close) - 0.25
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(100, 400, size=n),
        }
    )


class TestBullBearFlag(unittest.TestCase):
    def test_detect_flag(self) -> None:
        df = _trend_df()
        out = detect_flag(df)
        for c in ("flag_valid", "flag_kind", "flag_low", "flag_high", "flag_leg_length_atr", "flag_pullback_ratio", "flag_structure_tag"):
            self.assertIn(c, out.columns)

    def test_flag_breakout_trigger(self) -> None:
        setup = FlagSetup(
            valid=True,
            kind="bull",
            flag_low=99.0,
            flag_high=101.0,
            leg_length_atr=3.0,
            pullback_ratio=0.4,
            structure_tag="H1",
        )
        next_bar = pd.Series({"open": 100.8, "high": 101.4, "low": 100.4, "close": 101.2})
        order = flag_breakout_trigger(setup, next_bar, tick_size=1.0)
        self.assertIsNotNone(order)
        self.assertEqual(order["side"], "long")

    def test_interval_compatibility(self) -> None:
        df = _trend_df()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = detect_flag(df, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

