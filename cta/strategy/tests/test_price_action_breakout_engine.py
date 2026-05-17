"""Smoke tests for split price action breakout engine."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.strategy.price_action_breakout_engine import backtest_one_symbol


def _mk_daily(n: int = 520, seed: int = 123) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.05, 0.9, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.1, 1.2, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 1.2, size=n)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2014-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1000, 5000, size=n),
            "turnover": rng.uniform(5e6, 2e7, size=n),
            "open_interest": rng.uniform(1e5, 2e5, size=n),
        }
    )


class TestPriceActionBreakoutEngine(unittest.TestCase):
    def test_backtest_one_symbol_smoke(self) -> None:
        df = _mk_daily()
        equity, trades, summary = backtest_one_symbol(df, "RB0", "SHFE")
        self.assertIsInstance(equity, pd.DataFrame)
        self.assertIsInstance(trades, pd.DataFrame)
        self.assertIsInstance(summary, pd.DataFrame)
        self.assertGreater(len(equity), 100)


if __name__ == "__main__":
    unittest.main()

