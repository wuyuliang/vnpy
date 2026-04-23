"""donchian_breakout.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.trend_strategies.donchian_breakout import (
    DonchianSignal,
    compute_donchian,
    decide_donchian_trade,
)


def _bars(n: int = 220, seed: int = 101) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.12, 0.5, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.25
    low = np.minimum(open_, close) - 0.25
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


class TestDonchianBreakout(unittest.TestCase):
    def test_compute_donchian(self) -> None:
        df = _bars()
        out = compute_donchian(df, n_entry=55, n_exit=20)
        for c in ("don_upper_entry", "don_lower_entry", "don_upper_exit", "don_lower_exit"):
            self.assertIn(c, out.columns)

    def test_decide_donchian_trade(self) -> None:
        df = compute_donchian(_bars())
        sig = decide_donchian_trade(df, bar_idx=150, current_position=None, filters={}, tick_size=1.0)
        if sig is not None:
            self.assertIn(sig.side, {"long", "short", "flat"})

    def test_interval_compatibility(self) -> None:
        df = _bars()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = compute_donchian(df, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

