"""risk_reward_score.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.filtering_scoring.risk_reward_score import compute_rr, rr_gate


def _df_for_rr(n: int = 200, seed: int = 99) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    trend = 100 + np.cumsum(rng.normal(0.1, 0.8, size=n))
    close = pd.Series(trend)
    high = close + np.abs(rng.normal(0.8, 0.2, size=n))
    low = close - np.abs(rng.normal(0.8, 0.2, size=n))
    atr_14 = (high - low).rolling(14, min_periods=1).mean()
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "high": high,
            "low": low,
            "close": close,
            "atr_14": atr_14,
        }
    )


class TestRiskReward(unittest.TestCase):
    def test_long_rr(self) -> None:
        df = _df_for_rr()
        out = compute_rr(
            df=df,
            bar_idx=120,
            entry=110.0,
            stop=108.0,
            direction="long",
            lookback=50,
            atr_k=2.0,
        )
        self.assertGreater(out.rr, 0.0)
        self.assertIn(out.target_source, {"swing", "measured_move", "atr", "blended"})

    def test_short_rr(self) -> None:
        df = _df_for_rr()
        out = compute_rr(
            df=df,
            bar_idx=120,
            entry=108.0,
            stop=110.0,
            direction="short",
            lookback=40,
            atr_k=1.8,
        )
        self.assertGreater(out.rr, 0.0)
        self.assertEqual(rr_gate(out.rr, min_rr=0.1), True)

    def test_no_lookahead_at_current_bar(self) -> None:
        """swing 窗口不得含当前 bar 的 high/low：改当前 bar 的 H/L 不应影响 target。"""
        df = _df_for_rr()
        i = 120
        out1 = compute_rr(df, i, entry=110.0, stop=108.0,
                          direction="long", lookback=50, atr_k=2.0)
        # 污染当前 bar 的 high 到天价
        df2 = df.copy()
        df2.at[i, "high"] = 1e6
        out2 = compute_rr(df2, i, entry=110.0, stop=108.0,
                          direction="long", lookback=50, atr_k=2.0)
        # ATR 会被 bar i 的 high 影响（这个 OK，因为 atr_14 本来就只能用到 i），
        # 但 swing 目标不应因此变。这里直接断言 target 未被污染到 1e6 附近：
        self.assertLess(out2.target, 1e5,
                        "当前 bar 的 high 不应直接成为 swing target")
        # 若 ATR 受污染也只是小幅变化；target 差异不该爆炸
        self.assertLess(abs(out2.target - out1.target), 1e4)

    def test_short_no_lookahead_at_current_bar(self) -> None:
        df = _df_for_rr()
        i = 120
        out1 = compute_rr(df, i, entry=108.0, stop=110.0,
                          direction="short", lookback=50, atr_k=2.0)
        df2 = df.copy()
        df2.at[i, "low"] = -1e6
        out2 = compute_rr(df2, i, entry=108.0, stop=110.0,
                          direction="short", lookback=50, atr_k=2.0)
        self.assertGreater(out2.target, -1e5,
                           "当前 bar 的 low 不应直接成为 swing target")

    def test_interval_compatibility(self) -> None:
        df = _df_for_rr()
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            out = compute_rr(
                df=df,
                bar_idx=100,
                entry=109.0,
                stop=107.0,
                direction="long",
                interval=interval,
            )
            self.assertGreater(out.rr, 0.0, interval)


if __name__ == "__main__":
    unittest.main()

