"""tight_range_breakout.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.price_action.tight_range_breakout import (
    TightRangeSetup,
    detect_tight_range,
    resolve_breakout_trigger,
)


def _mk_df(n: int = 180, seed: int = 11) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.0, 0.4, size=n))
    # inject a compressed window then breakout
    close[80:90] = close[79] + rng.normal(0.0, 0.05, size=10)
    close[91] = close[90] + 1.0
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
            "volume": rng.integers(100, 300, size=n),
        }
    )


class TestTightRangeBreakout(unittest.TestCase):
    def test_detect_tight_range(self) -> None:
        df = _mk_df()
        out = detect_tight_range(df, lookback=10, alpha=1.5, min_count=5)
        for c in ("tr_valid", "tr_upper", "tr_lower", "tr_range_atr", "tr_count", "tr_direction_bias"):
            self.assertIn(c, out.columns)
        self.assertGreater(int(out["tr_valid"].sum()), 0)

    def test_resolve_breakout_trigger(self) -> None:
        setup = TightRangeSetup(valid=True, upper=101.0, lower=99.0, range_atr=1.2, count=7, direction_bias=0)
        bar = pd.Series({"open": 100.0, "high": 101.5, "low": 99.5, "close": 101.2})
        order = resolve_breakout_trigger(setup, bar, tick_size=1.0)
        self.assertIsNotNone(order)
        self.assertIn(order["side"], {"long", "short"})

    def test_interval_compatibility(self) -> None:
        df = _mk_df()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = detect_tight_range(df, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

