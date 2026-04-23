"""breakout_pullback.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.price_action.breakout_pullback import (
    PullbackSetup,
    detect_breakout_pullback,
    pullback_entry_trigger,
)


def _mk_df(seed: int = 5, n: int = 180) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.1, 0.3, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(200, 600, size=n),
        }
    )


def _breakout_anchor(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["breakout_level"] = np.nan
    out["breakout_direction"] = ""
    out.loc[80, "breakout_level"] = float(df["high"].iloc[75:80].max())
    out.loc[80, "breakout_direction"] = "long"
    return out


class TestBreakoutPullback(unittest.TestCase):
    def test_detect_breakout_pullback(self) -> None:
        df = _mk_df()
        breakout_df = _breakout_anchor(df)
        out = detect_breakout_pullback(df, breakout_df)
        for c in ("bp_valid", "bp_direction", "bp_breakout_level", "bp_pullback_low", "bp_bars_since_breakout", "bp_confirmed"):
            self.assertIn(c, out.columns)

    def test_pullback_entry_trigger(self) -> None:
        setup = PullbackSetup(
            valid=True,
            direction="long",
            breakout_level=101.0,
            pullback_low=100.4,
            bars_since_breakout=5,
            confirmed=True,
        )
        next_bar = pd.Series({"open": 101.1, "high": 101.8, "low": 100.9, "close": 101.6})
        order = pullback_entry_trigger(setup, next_bar, tick_size=1.0)
        self.assertIsNotNone(order)
        self.assertEqual(order["side"], "long")

    def test_interval_compatibility(self) -> None:
        df = _mk_df()
        breakout_df = _breakout_anchor(df)
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = detect_breakout_pullback(df, breakout_df, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

