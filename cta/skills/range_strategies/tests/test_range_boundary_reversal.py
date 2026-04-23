"""range_boundary_reversal.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.range_strategies.range_boundary_reversal import (
    BoundaryReversalSetup,
    detect_boundary_reversal,
    place_boundary_order,
)


def _df(n: int = 180, seed: int = 18) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.sin(np.linspace(0, 15, n)) * 2 + rng.normal(0.0, 0.2, n)
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.25
    low = np.minimum(open_, close) - 0.25
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


def _range_df(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["range_upper"] = df["high"].rolling(20, min_periods=5).max()
    out["range_lower"] = df["low"].rolling(20, min_periods=5).min()
    out["range_score"] = 0.7
    out["range_age"] = np.arange(len(df))
    return out


class TestRangeBoundaryReversal(unittest.TestCase):
    def test_detect_boundary_reversal(self) -> None:
        df = _df()
        out = detect_boundary_reversal(df, _range_df(df))
        for c in ("rb_valid", "rb_side", "rb_boundary", "rb_stop", "rb_mid", "rb_far_boundary"):
            self.assertIn(c, out.columns)

    def test_place_boundary_order(self) -> None:
        setup = BoundaryReversalSetup(valid=True, side="short", boundary=101.0, stop=102.0, mid=100.0, far_boundary=99.0)
        order = place_boundary_order(setup, tick_size=1.0)
        self.assertIsNotNone(order)
        self.assertIn("targets", order)

    def test_interval_compatibility(self) -> None:
        df = _df()
        rg = _range_df(df)
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = detect_boundary_reversal(df, rg, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

