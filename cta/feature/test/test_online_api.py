"""Tests for online feature APIs."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.feature.online import FeatureGenerator, compute_features, compute_latest_features, get_recommended_lookback


def _minute_df(n: int = 2600, seed: int = 66) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    dt = pd.date_range("2024-01-01 09:00:00", periods=n, freq="1min")
    close = 100 + np.cumsum(rng.normal(0.0, 0.12, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.01, 0.15, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.01, 0.15, size=n)
    vol = rng.integers(20, 300, size=n)
    return pd.DataFrame(
        {
            "datetime": dt,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": vol,
            "open_interest": 1000,
        }
    )


class TestOnlineAPI(unittest.TestCase):
    def test_recommended_lookback(self) -> None:
        self.assertGreater(get_recommended_lookback("day"), 100)
        self.assertGreater(get_recommended_lookback("minute"), 100)
        self.assertEqual(get_recommended_lookback("5min"), get_recommended_lookback("minute5"))

    def test_compute_features_and_latest(self) -> None:
        df = _minute_df()
        out = compute_features(df, interval="minute")
        self.assertEqual(len(out), len(df))
        self.assertIn("trend_score", out.columns)
        latest = compute_latest_features(df, interval="minute", n=3)
        self.assertEqual(len(latest), 3)
        self.assertIn("rr_score", latest.columns)

    def test_feature_generator_stream(self) -> None:
        # 流式接口语义测试：控制样本规模，避免该用例变成性能测试。
        df = _minute_df(n=40)
        gen = FeatureGenerator(interval="minute", window=40, symbol="RB0", exchange="SHFE")
        got = None
        for _, row in df.iterrows():
            got = gen.update(row)
        self.assertIsNotNone(got)
        assert got is not None
        self.assertIn("trend_score", got.index)
        self.assertEqual(len(gen), len(df))


if __name__ == "__main__":
    unittest.main()
