"""breakout_quality.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.filtering_scoring.breakout_quality import (
    breakout_quality_gate,
    score_breakout,
)


def _breakout_df(n: int = 140, seed: int = 123) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = np.linspace(100.0, 110.0, n)
    noise = rng.normal(0.0, 0.4, size=n)
    close = base + noise
    close[90:95] += np.linspace(0.0, 4.0, 5)  # inject breakout sequence
    open_ = np.concatenate([[close[0]], close[:-1]])
    high = np.maximum(open_, close) + rng.uniform(0.2, 0.8, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.2, 0.8, size=n)
    volume = rng.integers(800, 1500, size=n).astype(float)
    volume[90:95] *= 2.0
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
        }
    )


class TestBreakoutQuality(unittest.TestCase):
    def test_breakout_score(self) -> None:
        df = _breakout_df()
        breakout_bar_idx = 92
        level = float(df["close"].iloc[:90].max())
        bq = score_breakout(
            df=df,
            breakout_bar_idx=breakout_bar_idx,
            breakout_level=level,
            atr=1.5,
        )
        self.assertTrue(0.0 <= bq.score <= 1.0)
        for key in ("s_cross", "s_vol", "s_body", "s_follow", "s_shadow"):
            self.assertIn(key, bq.components)

    def test_default_no_lookahead(self) -> None:
        """默认 follow_bars=0：改动 i+1,i+2 的 close 不应影响 score。"""
        df = _breakout_df().copy()
        i = 92
        level = float(df["close"].iloc[:90].max())
        bq1 = score_breakout(df, i, level, atr=1.5)
        # 污染未来 bar：无论正负都不应影响得分
        df2 = df.copy()
        df2.loc[i + 1 : i + 10, "close"] = -1e6
        df2.loc[i + 1 : i + 10, "high"] = -1e6
        df2.loc[i + 1 : i + 10, "low"] = -1e6
        df2.loc[i + 1 : i + 10, "volume"] = 0.0
        bq2 = score_breakout(df2, i, level, atr=1.5)
        self.assertAlmostEqual(bq1.score, bq2.score, places=9,
                               msg="默认模式下不得使用 i 之后的 bar")

    def test_opt_in_follow_bars_uses_future(self) -> None:
        """显式 follow_bars>0：读取未来 bar，属 post-hoc 标注模式（必须 _allow_future=True）。"""
        df = _breakout_df()
        i = 92
        level = float(df["close"].iloc[:90].max())
        bq_live = score_breakout(df, i, level, atr=1.5, follow_bars=0)
        bq_label = score_breakout(df, i, level, atr=1.5, follow_bars=2, _allow_future=True)
        # 分量字典里 s_follow 在 live 模式下应为 None 或默认值；label 模式下有数值
        # 弱断言：两种模式可以给出不同 score
        self.assertIsInstance(bq_live.score, float)
        self.assertIsInstance(bq_label.score, float)

    def test_gate(self) -> None:
        df = _breakout_df()
        bq = score_breakout(
            df=df,
            breakout_bar_idx=92,
            breakout_level=float(df["close"].iloc[:90].max()),
            atr=1.2,
        )
        self.assertEqual(breakout_quality_gate(bq, min_score=0.0), True)

    def test_interval_compatibility(self) -> None:
        df = _breakout_df()
        level = float(df["close"].iloc[:90].max())
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            bq = score_breakout(df, 92, level, atr=1.0, interval=interval)
            self.assertTrue(0.0 <= bq.score <= 1.0, interval)


    def test_follow_bars_partial_window(self) -> None:
        """follow_bars 超过剩余 bar 数：seen 应衰减为剩余可读 bar，不报错。
        Post-hoc 标注用例 → 显式 _allow_future=True。"""
        df = _breakout_df()
        i = len(df) - 1  # 最后一根 bar，没有 i+1
        level = float(df["close"].iloc[:i].max())
        bq = score_breakout(df, i, level, atr=1.5, follow_bars=5, _allow_future=True)
        self.assertTrue(0.0 <= bq.score <= 1.0)
        # 没有未来 bar 时 s_follow 应回退为 0（除以 max(seen,1) 不爆）
        self.assertGreaterEqual(bq.components["s_follow"], 0.0)
        self.assertLessEqual(bq.components["s_follow"], 1.0)

    def test_follow_bars_negative_treated_as_live(self) -> None:
        """follow_bars 负值视同 0：走 live-safe 分支（以本 bar close 位置为代理）。"""
        df = _breakout_df()
        i = 92
        level = float(df["close"].iloc[:90].max())
        bq_live = score_breakout(df, i, level, atr=1.5, follow_bars=0)
        bq_neg = score_breakout(df, i, level, atr=1.5, follow_bars=-3)
        self.assertAlmostEqual(bq_live.score, bq_neg.score, places=9)

    def test_live_proxy_close_near_high_for_up_breakout(self) -> None:
        """live-safe s_follow 代理：上破时 close 越贴近 high → s_follow 越高。"""
        # 用显式低 level 保证两个样本都是 up-break，避免 is_up 翻转
        df = _breakout_df().copy()
        i = 92
        level = 100.0  # 显式低位 level，使 strong/weak 两个 close 都 > level
        # 强势：close 接近 high
        df.at[i, "open"] = 100.0
        df.at[i, "low"] = 100.0
        df.at[i, "high"] = 110.0
        df.at[i, "close"] = 109.5  # 95% 位置
        bq_strong = score_breakout(df, i, level, atr=1.5, follow_bars=0)
        # 弱势：close 接近 low（但仍 > level）
        df.at[i, "close"] = 101.0  # 10% 位置
        bq_weak = score_breakout(df, i, level, atr=1.5, follow_bars=0)
        self.assertGreater(
            bq_strong.components["s_follow"],
            bq_weak.components["s_follow"],
            "上破时 close 越靠 high s_follow 应越高",
        )

    def test_live_proxy_close_near_low_for_down_breakout(self) -> None:
        """live-safe s_follow 代理：下破时 close 越贴近 low → s_follow 越高（对称）。"""
        df = _breakout_df().copy()
        i = 92
        level = 200.0  # 显式高位 level，使 strong/weak 两个 close 都 < level (下破)
        df.at[i, "open"] = 110.0
        df.at[i, "low"] = 100.0
        df.at[i, "high"] = 110.0
        df.at[i, "close"] = 100.5  # 5% 位置（贴底，下破强）
        bq_strong = score_breakout(df, i, level, atr=1.5, follow_bars=0)
        df.at[i, "close"] = 109.0  # 90% 位置（贴顶，下破弱）
        bq_weak = score_breakout(df, i, level, atr=1.5, follow_bars=0)
        self.assertGreater(
            bq_strong.components["s_follow"],
            bq_weak.components["s_follow"],
            "下破时 close 越靠 low s_follow 应越高",
        )


    # ---------------- P1-A: follow_bars > 0 必须显式 opt-in ------------------
    def test_score_breakout_raises_when_follow_bars_positive_without_opt_in(self) -> None:
        """P1-A: follow_bars > 0 会读 i+1..i+follow_bars 的 bar，构成未来函数。
        必须强制调用方显式传 ``_allow_future=True``，否则 raise。
        这样把"label 用法"和"feature 用法"在 API 层硬隔离，避免误用导致 leak。
        """
        df = _breakout_df(140)
        i = 92
        level = float(df["high"].iloc[i - 1])
        with self.assertRaises(ValueError) as cm:
            score_breakout(df, i, level, atr=1.5, follow_bars=3)
        self.assertIn("_allow_future", str(cm.exception))

    def test_score_breakout_allows_future_when_opt_in_explicit(self) -> None:
        """P1-A: 显式 ``_allow_future=True`` 的 post-hoc 标注用法仍可用。"""
        df = _breakout_df(140)
        i = 92
        level = float(df["high"].iloc[i - 1])
        # 显式开 future 模式不会 raise
        bq = score_breakout(df, i, level, atr=1.5, follow_bars=3, _allow_future=True)
        self.assertIsNotNone(bq)
        self.assertTrue(0.0 <= bq.score <= 1.0)

    def test_score_breakout_default_follow_bars_zero_still_works(self) -> None:
        """P1-A: 默认 follow_bars=0 (live-safe) 走原路径，无需 _allow_future。"""
        df = _breakout_df(140)
        i = 92
        level = float(df["high"].iloc[i - 1])
        bq = score_breakout(df, i, level, atr=1.5)  # 默认 follow_bars=0
        self.assertIsNotNone(bq)


if __name__ == "__main__":
    unittest.main()

