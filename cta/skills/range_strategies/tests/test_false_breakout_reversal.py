"""false_breakout_reversal.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.range_strategies.false_breakout_reversal import (
    FBRTrade,
    build_fbr_trade,
    manage_fbr_exit,
)


def _mk_df(n: int = 180, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.0, 0.4, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.2
    low = np.minimum(open_, close) - 0.2
    return pd.DataFrame({"open": open_, "high": high, "low": low, "close": close})


def _range_df(df: pd.DataFrame) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out["range_upper"] = df["high"].rolling(20, min_periods=5).max()
    out["range_lower"] = df["low"].rolling(20, min_periods=5).min()
    out["range_score"] = 0.7
    return out


class TestFalseBreakoutReversal(unittest.TestCase):
    def test_build_fbr_trade(self) -> None:
        df = _mk_df()
        trade = build_fbr_trade(df, _range_df(df), bar_idx=100, tick_size=1.0, htf_range_ok=True)
        if trade is not None:
            self.assertIn(trade.side, {"long", "short"})

    def test_manage_fbr_exit(self) -> None:
        t = FBRTrade(side="short", entry=100.0, stop=102.0, targets=[99.0, 98.0])
        status = manage_fbr_exit(t, bars_since_entry=3, current_price=99.0)
        self.assertIn(status, {"hold", "partial", "full_exit", "stop_out"})


if __name__ == "__main__":
    unittest.main()

