"""trend.py 单元测试."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.market_regime.trend import (
    TrendState,
    classify_maturity,
    compute_trend_state,
)


def _random_walk(n: int = 400, seed: int = 3) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.01, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close,
                         "volume": rng.integers(1000, 2000, n).astype(float)})


def _strong_uptrend(n: int = 400, seed: int = 11,
                    drift: float = 0.005) -> pd.DataFrame:
    """正漂移 => 强烈上升趋势，close 几乎单调递增。"""
    rng = np.random.default_rng(seed)
    ret = rng.normal(drift, 0.005, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.002, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close,
                         "volume": rng.integers(1000, 2000, n).astype(float)})


def _strong_downtrend(n: int = 400, seed: int = 13) -> pd.DataFrame:
    return _strong_uptrend(n=n, seed=seed, drift=-0.005)


class TestComputeTrendState(unittest.TestCase):
    def test_output_shape(self) -> None:
        df = _random_walk()
        out = compute_trend_state(df)
        self.assertEqual(len(out), len(df))
        for c in ("trend_score", "trend_dir", "trend_strength",
                  "trend_maturity"):
            self.assertIn(c, out.columns, c)

    def test_score_in_unit_interval(self) -> None:
        df = _random_walk()
        out = compute_trend_state(df)
        s = out["trend_score"].dropna()
        self.assertTrue(((s >= -1.0) & (s <= 1.0)).all())

    def test_strength_in_unit_interval(self) -> None:
        df = _random_walk()
        out = compute_trend_state(df)
        s = out["trend_strength"].dropna()
        self.assertTrue(((s >= 0.0) & (s <= 1.0)).all())

    def test_direction_values(self) -> None:
        df = _random_walk()
        out = compute_trend_state(df)
        dirs = set(out["trend_dir"].dropna().unique())
        self.assertTrue(dirs.issubset({-1, 0, 1}), dirs)

    def test_uptrend_detected(self) -> None:
        df = _strong_uptrend(n=400)
        out = compute_trend_state(df)
        tail = out["trend_score"].dropna().iloc[-50:]
        # 强趋势尾部平均分应显著为正
        self.assertGreater(tail.mean(), 0.4,
                           f"强上升趋势 trend_score 平均应 > 0.4, got {tail.mean():.3f}")
        self.assertGreater((out["trend_dir"].iloc[-50:] == 1).sum(), 30)

    def test_downtrend_detected(self) -> None:
        df = _strong_downtrend(n=400)
        out = compute_trend_state(df)
        tail = out["trend_score"].dropna().iloc[-50:]
        self.assertLess(tail.mean(), -0.4,
                        f"强下降趋势 trend_score 平均应 < -0.4, got {tail.mean():.3f}")
        self.assertGreater((out["trend_dir"].iloc[-50:] == -1).sum(), 30)

    def test_missing_cols_raise(self) -> None:
        df = _random_walk()[["close"]]
        with self.assertRaises(KeyError):
            compute_trend_state(df)

    def test_maturity_values_are_labels(self) -> None:
        df = _strong_uptrend(n=300)
        out = compute_trend_state(df)
        mats = set(out["trend_maturity"].dropna().unique())
        self.assertTrue(mats.issubset({"young", "mature", "late", "none"}),
                        mats)


class TestClassifyMaturity(unittest.TestCase):
    def test_young_then_mature(self) -> None:
        # 前 10 bar 趋势刚起来，中段成熟，末段回落
        score = pd.Series(
            [0.0] * 30 + [0.8] * 15 + [0.9] * 80 + [0.3] * 30,
        )
        mat = classify_maturity(score, lookback=60, threshold=0.5)
        self.assertEqual(len(mat), len(score))
        # 趋势起来的初期 (bar 30~40)：young
        self.assertEqual(mat.iloc[35], "young",
                         f"刚进趋势应 young, got {mat.iloc[35]!r}")
        # 趋势稳定的中段 (bar 90 附近)：mature
        self.assertEqual(mat.iloc[90], "mature",
                         f"成熟趋势应 mature, got {mat.iloc[90]!r}")

    def test_none_when_below_threshold(self) -> None:
        score = pd.Series([0.1] * 100)
        mat = classify_maturity(score, threshold=0.5)
        self.assertTrue((mat == "none").all())


class TestTrendState(unittest.TestCase):
    def test_valid(self) -> None:
        s = TrendState(score=0.6, direction=1, strength=0.7, maturity="mature")
        self.assertEqual(s.direction, 1)

    def test_bad_direction(self) -> None:
        with self.assertRaises(ValueError):
            TrendState(score=0.1, direction=2, strength=0.5, maturity="young")

    def test_bad_maturity(self) -> None:
        with self.assertRaises(ValueError):
            TrendState(score=0.1, direction=0, strength=0.5, maturity="xxx")

    def test_score_bounds(self) -> None:
        with self.assertRaises(ValueError):
            TrendState(score=1.5, direction=1, strength=0.5, maturity="young")


if __name__ == "__main__":
    unittest.main()
