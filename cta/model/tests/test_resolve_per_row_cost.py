"""按 (cluster, interval) 解析 commission + slippage 的 resolver 测试。

历史背景：2026-05-24 OOT 诊断发现 cost_pct 硬编码为 3 bps（commission_pct_per_trade
+ slippage_pct_per_trade）。为支持按品种分层费率，新增 commission_pct_by_cluster_interval
+ slippage_pct_by_cluster_interval 两个 dict 字段，配套 resolver 按行决定 cost。

覆盖：
  1) 默认（dict 空）所有行 fallback 到全局 commission + slippage（向后兼容）
  2) override dict 命中时按 cluster|interval 用 dict 值
  3) override dict 未命中时回退到全局
  4) 分钟 vs day 不同 cluster 都正确路由
  5) 与 cost_manifest.build_cluster_interval_cost_dict 联动
"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.config.cost_manifest import build_cluster_interval_cost_dict
from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.pipeline_oot_evaluation_inputs import resolve_per_row_cost_pct


class TestResolvePerRowCost(unittest.TestCase):
    def test_explicit_empty_dict_falls_back_to_global_constants(self) -> None:
        """2026-05-24（第三轮）：cfg 默认已翻为真实成本表；显式传空 dict 才回退到全局。"""
        cfg = OotEvaluationConfig(
            commission_pct_by_cluster_interval={},
            slippage_pct_by_cluster_interval={},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "RB0", "T0", "AU0"],
            "interval": ["day", "day", "day", "day"],
        })
        arr = resolve_per_row_cost_pct(df, cfg)
        expected = float(cfg.commission_pct_per_trade) + float(cfg.slippage_pct_per_trade)
        self.assertEqual(len(arr), 4)
        for v in arr:
            self.assertAlmostEqual(v, expected, places=6)

    def test_default_cfg_uses_real_cost_manifest(self) -> None:
        """默认 cfg 应当走真实成本表：bond 单笔 cost 显著低于 black。"""
        cfg = OotEvaluationConfig()
        df = pd.DataFrame({
            "symbol": ["T0", "RB0"],
            "interval": ["day", "day"],
        })
        arr = resolve_per_row_cost_pct(df, cfg)
        # bond < black（cost_manifest 设计原则）
        self.assertLess(arr[0], arr[1])
        # 都比全局 3bp 小（bond 0.8bp、black 4.5bp 等都低于全局错误高估）
        global_pct = float(cfg.commission_pct_per_trade) + float(cfg.slippage_pct_per_trade)
        self.assertLess(arr[0], global_pct)

    def test_override_dict_routes_per_cluster_interval(self) -> None:
        cfg = OotEvaluationConfig(
            commission_pct_by_cluster_interval={
                "bond|day": 0.00003,
                "black|day": 0.00015,
            },
            slippage_pct_by_cluster_interval={
                "bond|day": 0.00005,
                "black|day": 0.00030,
            },
        )
        df = pd.DataFrame({
            "symbol": ["T0", "RB0"],
            "interval": ["day", "day"],
        })
        arr = resolve_per_row_cost_pct(df, cfg)
        self.assertAlmostEqual(arr[0], 0.00003 + 0.00005, places=8)
        self.assertAlmostEqual(arr[1], 0.00015 + 0.00030, places=8)

    def test_override_misses_fall_back_to_global(self) -> None:
        cfg = OotEvaluationConfig(
            commission_pct_by_cluster_interval={"bond|day": 0.00003},
            slippage_pct_by_cluster_interval={"bond|day": 0.00005},
        )
        df = pd.DataFrame({
            "symbol": ["IF0"],  # cluster=index，没在 override 里
            "interval": ["day"],
        })
        global_pct = float(cfg.commission_pct_per_trade) + float(cfg.slippage_pct_per_trade)
        arr = resolve_per_row_cost_pct(df, cfg)
        self.assertAlmostEqual(arr[0], global_pct, places=8)

    def test_minute_and_day_routed_independently(self) -> None:
        cfg = OotEvaluationConfig(
            commission_pct_by_cluster_interval={
                "black|day": 0.00010,
                "black|60min": 0.00010,
            },
            slippage_pct_by_cluster_interval={
                "black|day": 0.00020,
                "black|60min": 0.00040,   # 分钟更高
            },
        )
        df = pd.DataFrame({
            "symbol": ["RB0", "RB0"],
            "interval": ["day", "60min"],
        })
        arr = resolve_per_row_cost_pct(df, cfg)
        self.assertAlmostEqual(arr[0], 0.00010 + 0.00020, places=8)
        self.assertAlmostEqual(arr[1], 0.00010 + 0.00040, places=8)

    def test_built_manifest_dicts_are_accepted(self) -> None:
        """build_cluster_interval_cost_dict 生成的 dict 可直接灌进 cfg 并被 resolver 解析。"""
        cfg = OotEvaluationConfig(
            commission_pct_by_cluster_interval=build_cluster_interval_cost_dict(kind="commission"),
            slippage_pct_by_cluster_interval=build_cluster_interval_cost_dict(kind="slippage"),
        )
        df = pd.DataFrame({
            "symbol": ["T0", "IF0", "AU0", "RB0", "CU0"],
            "interval": ["day"] * 5,
        })
        arr = resolve_per_row_cost_pct(df, cfg)
        self.assertEqual(len(arr), 5)
        # bond 最便宜，black 最贵
        self.assertLess(arr[0], arr[3])

    def test_empty_df_returns_empty_array(self) -> None:
        cfg = OotEvaluationConfig()
        df = pd.DataFrame({"symbol": [], "interval": []})
        arr = resolve_per_row_cost_pct(df, cfg)
        self.assertEqual(len(arr), 0)


if __name__ == "__main__":
    unittest.main()
