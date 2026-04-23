"""volatility.py 单元测试."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.market_regime.volatility import (
    VolState,
    compute_vol_state,
    vol_target_size,
)


def _synth_ohlc(
    n: int = 400,
    seed: int = 7,
    vol_boost_at: int | None = None,
    boost_factor: float = 3.0,
) -> pd.DataFrame:
    """合成一条 close 随机游走序列 + high/low/open/volume，便于测试。"""
    rng = np.random.default_rng(seed)
    ret = rng.normal(0, 0.01, n)
    if vol_boost_at is not None:
        ret[vol_boost_at:] *= boost_factor
    close = 100.0 * np.exp(np.cumsum(ret))
    high = close * (1 + np.abs(rng.normal(0, 0.003, n)))
    low = close * (1 - np.abs(rng.normal(0, 0.003, n)))
    open_ = np.concatenate([[close[0]], close[:-1]])
    volume = rng.integers(1000, 3000, n).astype(float)
    return pd.DataFrame({
        "open": open_, "high": high, "low": low, "close": close,
        "volume": volume,
    })


class TestComputeVolState(unittest.TestCase):
    def test_output_shape_and_columns(self) -> None:
        df = _synth_ohlc(n=500)
        out = compute_vol_state(df)
        self.assertEqual(len(out), len(df))
        for col in ("vol_regime", "vol_atr", "vol_atr_pct",
                    "vol_atr_ratio", "vol_ann"):
            self.assertIn(col, out.columns, col)

    def test_regime_values_subset(self) -> None:
        df = _synth_ohlc(n=500)
        out = compute_vol_state(df)
        vals = set(out["vol_regime"].dropna().unique())
        self.assertTrue(vals.issubset({"compression", "expansion",
                                       "low", "high"}), vals)

    def test_atr_pct_in_unit_interval(self) -> None:
        df = _synth_ohlc(n=500)
        out = compute_vol_state(df)
        pct = out["vol_atr_pct"].dropna()
        self.assertTrue(((pct >= 0.0) & (pct <= 1.0)).all())

    def test_expansion_detects_vol_spike(self) -> None:
        """在波动率突然扩张的初期，atr_fast 应显著高于 atr_slow → expansion。"""
        df = _synth_ohlc(n=600, vol_boost_at=400, boost_factor=5.0)
        out = compute_vol_state(df, atr_n_fast=14, atr_n_slow=60,
                                pct_window=252)
        # spike 刚开始的 60 根（bar 400~460），atr_fast 会远快于 atr_slow，
        # 此时 expansion 应命中（越往后 atr_slow 也跟上，比率下降）
        early_spike = out["vol_regime"].iloc[410:470]
        hits = (early_spike.isin(["expansion", "high"])).sum()
        self.assertGreaterEqual(
            hits, 10,
            f"spike 初期应多次被 expansion/high 覆盖, got {early_spike.tolist()}",
        )

    def test_missing_column_raises(self) -> None:
        df = _synth_ohlc()[["close"]]
        with self.assertRaises(KeyError):
            compute_vol_state(df)

    def test_short_input_ok(self) -> None:
        df = _synth_ohlc(n=30)
        # 不应抛；前面若干 bar NaN 也可以
        out = compute_vol_state(df)
        self.assertEqual(len(out), len(df))


class TestVolState(unittest.TestCase):
    def test_dataclass_fields(self) -> None:
        s = VolState(regime="low", atr=1.2, atr_pct=0.3,
                     atr_ratio_fast_slow=0.8, annualized_vol=0.2)
        self.assertEqual(s.regime, "low")

    def test_bad_regime(self) -> None:
        with self.assertRaises(ValueError):
            VolState(regime="foo", atr=1, atr_pct=0.1,
                     atr_ratio_fast_slow=1, annualized_vol=0.1)

    def test_pct_bounds(self) -> None:
        with self.assertRaises(ValueError):
            VolState(regime="low", atr=1, atr_pct=1.5,
                     atr_ratio_fast_slow=1, annualized_vol=0.1)


class TestVolTargetSize(unittest.TestCase):
    def test_basic(self) -> None:
        size = vol_target_size(
            atr=5.0, contract_multiplier=10.0,
            equity=1_000_000.0, risk_pct=0.003,
        )
        # 3000 风险 / (5 * 10) = 60 手
        self.assertAlmostEqual(size, 60.0, places=6)

    def test_zero_atr_returns_zero(self) -> None:
        self.assertEqual(
            vol_target_size(atr=0.0, contract_multiplier=10.0,
                            equity=1e6, risk_pct=0.003),
            0.0,
        )

    def test_negative_input_raises(self) -> None:
        with self.assertRaises(ValueError):
            vol_target_size(atr=1.0, contract_multiplier=-10.0,
                            equity=1e6, risk_pct=0.003)
        with self.assertRaises(ValueError):
            vol_target_size(atr=1.0, contract_multiplier=10.0,
                            equity=1e6, risk_pct=-0.001)


if __name__ == "__main__":
    unittest.main()
