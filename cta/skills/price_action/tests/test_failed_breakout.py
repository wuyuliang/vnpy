"""failed_breakout.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.price_action.failed_breakout import (
    FailedBreakoutSetup,
    detect_failed_breakout,
    failed_breakout_entry,
)


def _mk(seed: int = 22, n: int = 180) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.0, 0.4, n))
    close[90] += 2.0
    close[91] -= 1.8
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
        }
    )


def _range_df(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["range_upper"] = df["high"].rolling(20, min_periods=5).max().shift(1)
    out["range_lower"] = df["low"].rolling(20, min_periods=5).min().shift(1)
    return out


class TestFailedBreakout(unittest.TestCase):
    def test_detect_failed_breakout(self) -> None:
        df = _mk()
        r = _range_df(df)
        out = detect_failed_breakout(df, r, max_confirm_bars=3)
        for c in ("fb_valid", "fb_side", "fb_extreme_level", "fb_range_boundary", "fb_confirm_bar_idx"):
            self.assertIn(c, out.columns)

    def test_failed_breakout_entry(self) -> None:
        s = FailedBreakoutSetup(
            valid=True,
            side="short",
            extreme_level=103.0,
            range_boundary=101.0,
            confirm_bar_idx=20,
        )
        bar = pd.Series({"open": 100.8, "high": 101.0, "low": 99.9, "close": 100.1})
        order = failed_breakout_entry(s, bar, tick_size=1.0)
        self.assertIsNotNone(order)
        self.assertEqual(order["side"], "short")

    def test_interval_compatibility(self) -> None:
        df = _mk()
        r = _range_df(df)
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = detect_failed_breakout(df, r, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

