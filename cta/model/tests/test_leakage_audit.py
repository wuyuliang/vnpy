"""Unit tests for cta.model.tools.leakage_audit.

验证三个关键性质：
1. 已知穿越列（用 close.shift(-h) 构造）能被检出 |IC| >= 0.30 阈值；
2. 真实因果列（仅基于过去的 ATR、log-return）不会误杀；
3. audit_symbol 返回结构稳定，empty/常数列健壮。
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.model.tools.leakage_audit import audit_symbol


def _synth_bars(n: int = 800, seed: int = 7) -> pd.DataFrame:
    """构造一段有真实波动的合成 OHLC，给 ic 计算提供非常数 close。"""
    rng = np.random.default_rng(seed)
    drift = rng.normal(0.0001, 0.01, size=n)
    close = 100.0 * np.exp(np.cumsum(drift))
    high = close * (1.0 + np.abs(rng.normal(0.0, 0.003, size=n)))
    low = close * (1.0 - np.abs(rng.normal(0.0, 0.003, size=n)))
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2020-01-01", periods=n, freq="D"),
            "open": close,
            "high": high,
            "low": low,
            "close": close,
            "volume": rng.integers(1000, 5000, size=n).astype(float),
        }
    )


class TestLeakageAudit(unittest.TestCase):
    def test_audit_symbol_detects_known_leakage_column(self) -> None:
        bars = _synth_bars()
        # 穿越列：把未来 1 根 close 复制为当前特征
        bars["feature_obvious_leak"] = bars["close"].shift(-1)
        out = audit_symbol(bars, symbol="TEST", horizon=5)
        leak_row = out.loc[out["column"] == "feature_obvious_leak"]
        self.assertFalse(leak_row.empty, "audit should produce a row for the leak column")
        # close.shift(-1) 直接命中 fwd1 (=close.shift(-1)/close - 1)？不一定，
        # 因为 ic_fwd1 是 |corr(feature, close.shift(-1)/close - 1)|。
        # 这里特征是 close.shift(-1)，与未来收益高度相关（其实是同 shift 的"未来 close 绝对值"）。
        # 严格的"穿越列"应该用 fwd_return 本身作为特征：
        bars["feature_pure_leak"] = bars["close"].shift(-1) / bars["close"] - 1.0
        out2 = audit_symbol(bars, symbol="TEST", horizon=5)
        pure = out2.loc[out2["column"] == "feature_pure_leak"]
        self.assertFalse(pure.empty)
        self.assertGreaterEqual(float(pure["ic_max"].iloc[0]), 0.99 - 1e-6)

    def test_audit_symbol_does_not_flag_causal_lag_feature(self) -> None:
        bars = _synth_bars()
        # 合法因果特征：基于过去 5 根的对数收益
        bars["feature_lag5_logret"] = np.log(bars["close"] / bars["close"].shift(5))
        out = audit_symbol(bars, symbol="TEST", horizon=5)
        lag_row = out.loc[out["column"] == "feature_lag5_logret"]
        self.assertFalse(lag_row.empty)
        # 合成数据是几乎纯白噪声，过去 5 根收益与未来 5 根的 |IC| 应该 < 0.30
        self.assertLess(float(lag_row["ic_max"].iloc[0]), 0.30)

    def test_audit_symbol_robust_to_constant_and_empty_columns(self) -> None:
        bars = _synth_bars()
        bars["feature_constant"] = 1.0
        bars["feature_all_nan"] = np.nan
        out = audit_symbol(bars, symbol="TEST", horizon=5)
        # 常数列 ic 应该是 NaN（spearman 不可定义），不会爆。
        const_row = out.loc[out["column"] == "feature_constant"]
        self.assertFalse(const_row.empty)
        self.assertTrue(pd.isna(const_row["ic_max"].iloc[0]))
        # 全 NaN 列应当被过滤掉，不出现在结果。
        nan_row = out.loc[out["column"] == "feature_all_nan"]
        self.assertTrue(nan_row.empty)

    def test_audit_symbol_returns_empty_on_empty_input(self) -> None:
        out = audit_symbol(pd.DataFrame(), symbol="X", horizon=5)
        self.assertTrue(out.empty)


if __name__ == "__main__":
    unittest.main()
