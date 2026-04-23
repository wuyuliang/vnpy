"""channel_state.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.price_action.channel_state import (
    ChannelState,
    channel_based_trailing,
    compute_channel_state,
)


def _df(seed: int = 9, n: int = 180) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.08, 0.2, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.15
    low = np.minimum(open_, close) - 0.15
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
        }
    )


class TestChannelState(unittest.TestCase):
    def test_compute_channel_state(self) -> None:
        df = _df()
        out = compute_channel_state(df)
        for c in ("ch_micro_count", "ch_micro_direction", "ch_trend_top", "ch_trend_bot", "ch_touches_top", "ch_touches_bot"):
            self.assertIn(c, out.columns)

    def test_channel_based_trailing(self) -> None:
        s = ChannelState(
            micro_count=8,
            micro_direction="up",
            trend_top=110.0,
            trend_bot=100.0,
            touches_top=2,
            touches_bot=0,
        )
        stop = channel_based_trailing("long", s, last_high=109.0, last_low=107.0)
        self.assertIsNotNone(stop)
        self.assertLess(float(stop), 109.1)

    def test_interval_compatibility(self) -> None:
        df = _df()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = compute_channel_state(df, interval=interval)
            self.assertEqual(len(out), len(df))


if __name__ == "__main__":
    unittest.main()

