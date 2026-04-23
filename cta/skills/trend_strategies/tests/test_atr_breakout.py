"""atr_breakout.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.trend_strategies.atr_breakout import (
    ATRChannelSignal,
    compute_atr_channel,
    decide_atr_channel_trade,
)


def _bars(n: int = 200, seed: int = 51) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.1, 0.5, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


class TestATRBreakout(unittest.TestCase):
    def test_compute_atr_channel(self) -> None:
        out = compute_atr_channel(_bars())
        for c in ("atr_ma", "atr_value", "atr_upper", "atr_lower"):
            self.assertIn(c, out.columns)

    def test_decide_trade(self) -> None:
        df = compute_atr_channel(_bars())
        sig = decide_atr_channel_trade(df, bar_idx=120, current_position=None, filters={})
        if sig is not None:
            self.assertIn(sig.side, {"long", "short", "flat"})

    def test_interval_compatibility(self) -> None:
        df = _bars()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = compute_atr_channel(df, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

