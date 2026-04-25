"""context_score.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.filtering_scoring.context_score import (
    combine_final_score,
    compute_context_score,
)


def _mk_tf(n: int, slope: float, seed: int) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = 100 + slope * np.arange(n)
    close = base + rng.normal(0, 0.4, size=n)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=n, freq="D"),
            "close": close,
            "regime": ["trend"] * n,
            "leg_position": ["start"] * n,
            "session": ["open"] * n,
        }
    )


class TestContextScore(unittest.TestCase):
    def test_compute_context_score(self) -> None:
        ltf = _mk_tf(120, slope=0.02, seed=1)
        mtf = _mk_tf(120, slope=0.03, seed=2)
        htf = _mk_tf(120, slope=0.05, seed=3)
        cs = compute_context_score(
            df_ltf=ltf,
            df_mtf=mtf,
            df_htf=htf,
            bar_idx_ltf=80,
            setup_type="tight_range",
            setup_dir="long",
        )
        self.assertTrue(0.0 <= cs.score <= 1.0)
        self.assertIn("s_htf", cs.components)
        self.assertIn("s_regime", cs.components)

    def test_combine_final_score_geometric(self) -> None:
        s = combine_final_score(0.8, 0.6, 0.7)
        self.assertTrue(0.0 <= s <= 1.0)
        self.assertLess(s, 0.8)

    def test_htf_mtf_timestamp_asof(self) -> None:
        """LTF 与 HTF 频率不同：HTF 应按 timestamp asof 匹配，不是按 i_ltf 位置截断。"""
        # LTF 15min × 2 天 = 64 bar；LTF 时间放在 2024-07-01 起
        # 让 asof 落到 HTF 末段的下跌中（索引 91，lb=20 窗口全部在下跌段）
        n_ltf = 64
        ltf = pd.DataFrame(
            {
                "datetime": pd.date_range("2024-07-01 09:00", periods=n_ltf, freq="15min"),
                "close": 100.0 + np.arange(n_ltf) * 0.02,  # LTF 温和上行
                "regime": ["trend"] * n_ltf,
                "leg_position": ["start"] * n_ltf,
                "session": ["open"] * n_ltf,
            }
        )
        mtf = ltf.iloc[::4].reset_index(drop=True)  # 60min
        # HTF 100 天日线：前 60 天强烈上行，最近 40 天强烈下跌
        # LTF bar 索引 (0..63) 若按位置对齐到 HTF，会落入前 60 天的「上行段」
        # 而 LTF 的 datetime 都在 2024-06-01~02，asof 查 HTF 应落到 HTF 最后一根
        # （假设 HTF 覆盖从 2024-04-01 到 2024-07-09，asof(2024-06-01) 应落在下跌段）
        htf_dates = pd.date_range("2024-04-01", periods=100, freq="D")
        htf_close = np.concatenate([
            np.linspace(100.0, 160.0, 60),   # 上行 60 天
            np.linspace(160.0, 110.0, 40),   # 下跌 40 天
        ])
        htf = pd.DataFrame(
            {
                "datetime": htf_dates,
                "close": htf_close,
                "regime": ["trend"] * 100,
                "leg_position": ["start"] * 100,
                "session": ["open"] * 100,
            }
        )
        # LTF bar 30 (datetime ≈ 2024-07-01 16:30) → asof 落在 HTF 索引 91
        # 下跌段中 close[91] < close[71] (lb=20 窗口全在下跌段) → htf_dir="short"
        # setup_dir="long" → s_htf 应 = 0.0
        # 位置对齐旧逻辑：i_htf = min(30, 99) = 30，HTF[30] 在上行段，slope 正 → long
        # → s_htf = 1.0
        # 两种结果差一个数量级
        cs = compute_context_score(
            ltf, mtf, htf, bar_idx_ltf=30,
            setup_type="tight_range", setup_dir="long",
        )
        self.assertEqual(
            cs.components["s_htf"], 0.0,
            "HTF 应按 timestamp asof 对齐 (2024-06-01 在下跌段)，"
            "而不是按 LTF 位置索引落到 HTF[30] 的上行段",
        )

    def test_htf_before_first_bar_returns_flat(self) -> None:
        """LTF bar 的时间戳早于 HTF 首根 bar：HTF 方向应为 flat，不抛异常。"""
        ltf = pd.DataFrame(
            {
                "datetime": pd.date_range("2024-01-01 09:00", periods=30, freq="15min"),
                "close": 100.0 + np.arange(30) * 0.01,
                "regime": ["trend"] * 30,
                "leg_position": ["start"] * 30,
                "session": ["open"] * 30,
            }
        )
        htf = pd.DataFrame(
            {
                "datetime": pd.date_range("2024-02-01", periods=5, freq="D"),
                "close": [100.0, 101.0, 102.0, 103.0, 104.0],
                "regime": ["trend"] * 5,
                "leg_position": ["start"] * 5,
                "session": ["open"] * 5,
            }
        )
        cs = compute_context_score(
            ltf, htf, htf, bar_idx_ltf=10,
            setup_type="tight_range", setup_dir="long",
        )
        # LTF 时间早于 HTF 起点：HTF 方向无从判断 → flat → s_htf = 0
        self.assertEqual(cs.components["s_htf"], 0.0)

    def test_interval_compatibility(self) -> None:
        ltf = _mk_tf(120, slope=0.02, seed=8)
        mtf = _mk_tf(120, slope=0.01, seed=9)
        htf = _mk_tf(120, slope=0.03, seed=10)
        for interval in ("day", "minute60", "minute30", "minute15", "minute5", "minute"):
            cs = compute_context_score(
                df_ltf=ltf,
                df_mtf=mtf,
                df_htf=htf,
                bar_idx_ltf=90,
                setup_type="bp",
                setup_dir="long",
                interval=interval,
            )
            self.assertTrue(0.0 <= cs.score <= 1.0, interval)

    def test_regime_label_precedence_over_regime_default(self) -> None:
        """
        回归测试：当仅提供 regime_label 时，必须被 context_score 正确读取。
        """
        ltf = _mk_tf(80, slope=0.02, seed=101).drop(columns=["regime"])
        ltf["regime_label"] = "range"
        mtf = _mk_tf(80, slope=0.01, seed=102)
        htf = _mk_tf(80, slope=0.03, seed=103)

        cs = compute_context_score(
            df_ltf=ltf,
            df_mtf=mtf,
            df_htf=htf,
            bar_idx_ltf=60,
            setup_type="failed_break",   # range 组
            setup_dir="long",
        )
        self.assertEqual(cs.components["s_regime"], 1.0)


    def test_no_datetime_falls_back_to_positional(self) -> None:
        """LTF/HTF 无 datetime 列时退化为位置对齐（legacy 行为）。"""
        ltf = _mk_tf(120, slope=0.02, seed=21).drop(columns=["datetime"])
        mtf = _mk_tf(120, slope=0.01, seed=22).drop(columns=["datetime"])
        htf = _mk_tf(120, slope=0.03, seed=23).drop(columns=["datetime"])
        cs = compute_context_score(
            ltf, mtf, htf, bar_idx_ltf=80,
            setup_type="bp", setup_dir="long",
        )
        self.assertTrue(0.0 <= cs.score <= 1.0)

    def test_mtf_asof_independent_of_htf(self) -> None:
        """MTF 与 HTF 的 asof 各自独立查找，不串扰。"""
        # LTF 15min 100 bar，覆盖 ~25h。MTF 60min 50 bar；HTF day 30 bar。
        ltf = pd.DataFrame({
            "datetime": pd.date_range("2024-05-01 09:00", periods=100, freq="15min"),
            "close": 100.0 + np.arange(100) * 0.01,
            "regime": ["trend"] * 100,
        })
        mtf = pd.DataFrame({
            "datetime": pd.date_range("2024-05-01 09:00", periods=50, freq="60min"),
            "close": np.linspace(100, 90, 50),  # MTF 下行
        })
        htf = pd.DataFrame({
            "datetime": pd.date_range("2024-04-01", periods=30, freq="D"),
            "close": np.linspace(80, 130, 30),  # HTF 上行（截止到 2024-04-30）
        })
        # LTF 第 80 根 = 2024-05-01 29:00 ≈ next day. asof MTF 索引 80*15/60=20，asof HTF=最后一根（4-30）
        cs = compute_context_score(
            ltf, mtf, htf, bar_idx_ltf=80,
            setup_type="bp", setup_dir="long",
        )
        # MTF 下行 → mtf_dir=short ≠ long → s_mtf=0.3
        # HTF 上行（asof 落在 4-30，位于上行段后期）→ htf_dir=long → s_htf=1.0
        self.assertEqual(cs.components["s_htf"], 1.0,
                         "HTF asof 应落到上行段尾部，与 long setup 同向")
        self.assertAlmostEqual(cs.components["s_mtf"], 0.3, places=6,
                               msg="MTF asof 应识别下行 → 与 long 反向 → 0.3")


if __name__ == "__main__":
    unittest.main()
