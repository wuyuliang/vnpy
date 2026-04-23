"""noise_filtering.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.range_strategies.noise_filtering import (
    FilterResult,
    combine_filters,
    make_time_window_filter,
    make_volume_filter,
)


def _df(n: int = 120, seed: int = 17) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2024-01-01 09:00:00", periods=n, freq="5min")
    close = 100 + np.cumsum(rng.normal(0.0, 0.2, n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + 0.1
    low = np.minimum(open_, close) - 0.1
    vol = rng.integers(100, 500, n)
    return pd.DataFrame({"datetime": dt, "open": open_, "high": high, "low": low, "close": close, "volume": vol})


class TestNoiseFiltering(unittest.TestCase):
    def test_volume_filter(self) -> None:
        df = _df()
        f = make_volume_filter(ratio=0.8, ma_n=20)
        res = f(df, 50)
        self.assertIsInstance(res, FilterResult)

    def test_time_window_filter(self) -> None:
        df = _df()
        f = make_time_window_filter(windows=[("09:00", "10:00")])
        res = f(df, 10)
        self.assertIsInstance(res, FilterResult)

    def test_combine_filters(self) -> None:
        df = _df()
        filters = [make_volume_filter(ratio=0.8), make_time_window_filter([("09:00", "10:00")])]
        ok = combine_filters(df, bar_idx=15, filters=filters, min_pass=1)
        self.assertIsInstance(ok, bool)


if __name__ == "__main__":
    unittest.main()

