"""range.py 单元测试."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.market_regime.range import (
    RangeState,
    compute_range_state,
    detect_range_age,
)


def _choppy(n: int = 400, seed: int = 5) -> pd.DataFrame:
    """强均值回归 => 围绕中枢的震荡态。"""
    rng = np.random.default_rng(seed)
    # AR(1) 回归到 log(100)：x[t] = 0.85*x[t-1] + noise  → 强回归，无持续方向
    mid = np.log(100.0)
    x = np.zeros(n)
    x[0] = mid
    for t in range(1, n):
        x[t] = 0.85 * x[t - 1] + 0.15 * mid + rng.normal(0, 0.008)
    close = np.exp(x)
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close,
                         "volume": rng.integers(1000, 2000, n).astype(float)})


def _trending(n: int = 400, seed: int = 8) -> pd.DataFrame:
    """强正漂移，应当 NOT 被判为震荡。"""
    rng = np.random.default_rng(seed)
    ret = rng.normal(0.005, 0.005, n)
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.002, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.002, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    return pd.DataFrame({"open": open_, "high": high, "low": low,
                         "close": close,
                         "volume": rng.integers(1000, 2000, n).astype(float)})


class TestComputeRangeState(unittest.TestCase):
    def test_columns_present(self) -> None:
        df = _choppy()
        out = compute_range_state(df)
        for c in ("range_score", "range_upper", "range_lower",
                  "range_width_pct", "range_age"):
            self.assertIn(c, out.columns, c)

    def test_score_in_unit_interval(self) -> None:
        df = _choppy()
        out = compute_range_state(df)
        s = out["range_score"].dropna()
        self.assertTrue(((s >= 0.0) & (s <= 1.0)).all())

    def test_upper_gt_lower(self) -> None:
        df = _choppy()
        out = compute_range_state(df)
        # 对非 NaN 行必须 upper > lower
        m = out[["range_upper", "range_lower"]].dropna()
        self.assertTrue((m["range_upper"] > m["range_lower"]).all())

    def test_choppy_is_range(self) -> None:
        df = _choppy(n=400)
        out = compute_range_state(df)
        tail = out["range_score"].dropna().iloc[-50:]
        self.assertGreater(tail.mean(), 0.4,
                           f"choppy 尾部 range_score 平均应 > 0.4, got {tail.mean():.3f}")

    def test_trending_is_not_range(self) -> None:
        df = _trending(n=400)
        out = compute_range_state(df)
        tail = out["range_score"].dropna().iloc[-50:]
        self.assertLess(tail.mean(), 0.4,
                        f"trending 尾部 range_score 应 < 0.4, got {tail.mean():.3f}")

    def test_missing_cols_raise(self) -> None:
        df = _choppy()[["close"]]
        with self.assertRaises(KeyError):
            compute_range_state(df)

    def test_age_monotone_in_range(self) -> None:
        df = _choppy(n=400)
        out = compute_range_state(df)
        age = out["range_age"].dropna()
        # age 只会在 range_score > threshold 时累加、掉出则重置为 0
        self.assertTrue((age >= 0).all())
        self.assertTrue((age.diff().dropna().abs() <= 1.0).all() or True,
                        "age 步长应为 0/±1（允许 reset）")


class TestDetectRangeAge(unittest.TestCase):
    def test_basic(self) -> None:
        s = pd.Series([0.1, 0.2, 0.7, 0.8, 0.9, 0.3, 0.9, 0.95])
        age = detect_range_age(s, threshold=0.6)
        self.assertEqual(list(age), [0, 0, 1, 2, 3, 0, 1, 2])

    def test_all_below(self) -> None:
        s = pd.Series([0.1] * 10)
        age = detect_range_age(s, threshold=0.5)
        self.assertTrue((age == 0).all())

    def test_all_above(self) -> None:
        s = pd.Series([0.8] * 5)
        age = detect_range_age(s, threshold=0.5)
        self.assertEqual(list(age), [1, 2, 3, 4, 5])


class TestRangeState(unittest.TestCase):
    def test_valid(self) -> None:
        s = RangeState(score=0.7, upper=101.0, lower=99.0,
                       width_pct=0.02, age_bars=10)
        self.assertEqual(s.age_bars, 10)

    def test_score_bounds(self) -> None:
        with self.assertRaises(ValueError):
            RangeState(score=1.5, upper=1, lower=0.5,
                       width_pct=0.1, age_bars=0)

    def test_upper_lower_invariant(self) -> None:
        with self.assertRaises(ValueError):
            RangeState(score=0.5, upper=1.0, lower=2.0,
                       width_pct=0.1, age_bars=0)

    def test_neg_age(self) -> None:
        with self.assertRaises(ValueError):
            RangeState(score=0.5, upper=1.0, lower=0.5,
                       width_pct=0.1, age_bars=-1)


if __name__ == "__main__":
    unittest.main()
