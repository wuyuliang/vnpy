"""backtest_principles.py 单元测试."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.overview.backtest_principles import (
    BacktestConfig,
    LookaheadViolation,
    assert_no_lookahead,
    detect_lookahead,
)


class TestBacktestConfig(unittest.TestCase):
    def test_default_ok(self) -> None:
        cfg = BacktestConfig()
        self.assertEqual(cfg.interval, "day")
        self.assertEqual(cfg.fill_model, "next_open")

    def test_interval_normalization(self) -> None:
        cfg = BacktestConfig(interval="5min")
        self.assertEqual(cfg.interval, "minute5")

    def test_bad_interval(self) -> None:
        with self.assertRaises(ValueError):
            BacktestConfig(interval="tick")

    def test_close_fill_forbidden(self) -> None:
        with self.assertRaises(ValueError):
            BacktestConfig(fill_model="close")

    def test_bad_fill_model(self) -> None:
        with self.assertRaises(ValueError):
            BacktestConfig(fill_model="foo")

    def test_neg_slippage(self) -> None:
        with self.assertRaises(ValueError):
            BacktestConfig(slippage_ticks=-1)

    def test_bad_date_order(self) -> None:
        with self.assertRaises(ValueError):
            BacktestConfig(start="2025-01-01", end="2024-01-01")

    def test_signal_lag_min(self) -> None:
        with self.assertRaises(ValueError):
            BacktestConfig(signal_lag_bars=0)

    def test_ranges(self) -> None:
        cfg = BacktestConfig(start="2020-01-01", end="2024-12-31",
                             oos_start="2024-01-01")
        self.assertEqual(cfg.in_sample_range(), ("2020-01-01", "2024-01-01"))
        self.assertEqual(cfg.out_of_sample_range(),
                         ("2024-01-01", "2024-12-31"))


class TestLookahead(unittest.TestCase):
    def _synth(self, n: int = 500, seed: int = 42) -> pd.DataFrame:
        rng = np.random.default_rng(seed)
        ret = rng.normal(0, 0.01, n)
        close = 100.0 * np.exp(np.cumsum(ret))
        return pd.DataFrame({"close": close}).reset_index(drop=True)

    def test_clean_signal_no_violation(self) -> None:
        df = self._synth()
        # 干净信号：使用前 5 根 close 的均值斜率（完全不使用当前/未来 bar）
        df["signal"] = (df["close"].shift(1) > df["close"].shift(5)).astype(int)
        vios = detect_lookahead(df, "signal",
                                divergence_threshold=0.5,
                                corr_threshold=0.3)
        self.assertEqual(vios, [])

    def test_peeking_signal_detected(self) -> None:
        df = self._synth()
        # 偷看：直接把未来 2 根 bar 的涨跌当信号 → 高 corr & 高 sharpe gap
        df["signal_bad"] = (
            df["close"].shift(-2) > df["close"]
        ).astype(int).fillna(0)
        vios = detect_lookahead(
            df, "signal_bad",
            divergence_threshold=0.3,
            corr_threshold=0.15,
            horizons_for_corr=(1, 2, 5),
        )
        self.assertTrue(vios, "应检测到 lookahead，got empty")
        # 至少 corr_with_future 应触发
        self.assertTrue(
            any(v.mode == "corr_with_future" for v in vios),
            [str(v) for v in vios],
        )

    def test_assert_no_lookahead_raises(self) -> None:
        df = self._synth()
        df["signal_bad"] = (
            df["close"].shift(-1) > df["close"]
        ).astype(int).fillna(0)
        with self.assertRaises(AssertionError):
            assert_no_lookahead(df, "signal_bad", corr_threshold=0.1)

    def test_missing_column_raises(self) -> None:
        df = self._synth()
        with self.assertRaises(KeyError):
            detect_lookahead(df, "no_such_col")

    def test_violation_str(self) -> None:
        v = LookaheadViolation(
            mode="shift_divergence", signal="x",
            metric="m", value=0.8, threshold=0.3,
        )
        s = str(v)
        self.assertIn("shift_divergence", s)
        self.assertIn("'x'", s)


if __name__ == "__main__":
    unittest.main()
