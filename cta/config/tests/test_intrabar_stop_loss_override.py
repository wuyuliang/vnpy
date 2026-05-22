"""按 (cluster, interval) override `intrabar_stop_loss_pct` 的测试。

历史背景（2026-05-19 OOT 诊断）：
  - INDEX day 用全局 0.01 (1%) 止损 → 13 笔 short 全在 1% 被 9.24 单日暴涨打穿
  - day 级别 A 股 (high-low)/close 中位数 1.5%, P90 3.25% → 1% 必然被日内噪音打穿
  - 因此引入按 (cluster, interval) 的 override，用 2024 前数据计算 P90 作为推荐值

本测试覆盖：
  1) config 默认含 7 个 day cluster override 且数值合理
  2) `_resolve_per_row_intrabar_stop_pct` 按 symbol + interval 正确路由
  3) 未匹配 key 时回退到全局 cfg.intrabar_stop_loss_pct
  4) post_init 校验：key 格式 + value 区间
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.pipeline_oot_evaluation import _resolve_per_row_intrabar_stop_pct


class TestIntrabarStopLossOverride(unittest.TestCase):
    def test_default_config_has_empty_cluster_interval_override(self) -> None:
        """2026-05-20：默认 override 字典临时清空（回退到全局 1%），机制保留但不主动生效。

        历史值（已 archived 在 model_oot_eval_config.py 注释里）可在策略 short bias 治本后
        重新启用，或通过 cta/model/tools/compute_stop_loss_manifest.py 重新计算。
        """
        cfg = OotEvaluationConfig()
        d = dict(cfg.intrabar_stop_loss_pct_by_cluster_interval)
        self.assertEqual(d, {},
                         f"default override should be empty (reverted to global 1%), got {d}")

    def test_resolver_uses_override_for_matching_cluster_interval(self) -> None:
        """显式传 override 时 resolver 走 cluster|interval 路径。"""
        cfg = OotEvaluationConfig(intrabar_stop_loss_pct_by_cluster_interval={
            "index|day": 0.0325,
        })
        df = pd.DataFrame({
            "symbol": ["IF0", "IH0", "IC0", "IM0"],
            "interval": ["day"] * 4,
        })
        arr = _resolve_per_row_intrabar_stop_pct(df, cfg)
        for v in arr:
            self.assertAlmostEqual(v, 0.0325, places=6)

    def test_resolver_falls_back_to_default_when_no_override(self) -> None:
        cfg = OotEvaluationConfig()
        # 30min interval 没有 override，应当走 global default
        df = pd.DataFrame({
            "symbol": ["IF0", "RB0", "CU0"],
            "interval": ["30min", "30min", "30min"],
        })
        arr = _resolve_per_row_intrabar_stop_pct(df, cfg)
        expected = float(cfg.intrabar_stop_loss_pct)
        for v in arr:
            self.assertAlmostEqual(v, expected, places=6)

    def test_resolver_mixed_intervals_routes_correctly(self) -> None:
        """不同行的 (cluster, interval) 不同 → 每行独立路由（用显式 override 验证机制）。"""
        cfg = OotEvaluationConfig(intrabar_stop_loss_pct_by_cluster_interval={
            "index|day": 0.0325,
            "black|day": 0.0451,
            "precious|day": 0.0232,
        })
        df = pd.DataFrame({
            "symbol": ["IF0", "IF0", "RB0", "AU0"],
            "interval": ["day", "30min", "day", "day"],
        })
        arr = _resolve_per_row_intrabar_stop_pct(df, cfg)
        # row 0: index|day → override 0.0325
        self.assertAlmostEqual(arr[0], 0.0325, places=6)
        # row 1: index|30min 不在 override 中 → global default
        self.assertAlmostEqual(arr[1], cfg.intrabar_stop_loss_pct, places=6)
        # row 2: black|day (RB) → override 0.0451
        self.assertAlmostEqual(arr[2], 0.0451, places=6)
        # row 3: precious|day (AU0) → override 0.0232
        self.assertAlmostEqual(arr[3], 0.0232, places=6)

    def test_resolver_handles_empty_df(self) -> None:
        cfg = OotEvaluationConfig()
        arr = _resolve_per_row_intrabar_stop_pct(pd.DataFrame(columns=["symbol", "interval"]), cfg)
        self.assertEqual(arr.shape, (0,))

    def test_resolver_handles_missing_columns_gracefully(self) -> None:
        cfg = OotEvaluationConfig()
        # 没有 symbol/interval 列 → 全部回退到 global default
        df = pd.DataFrame({"foo": [1, 2, 3]})
        arr = _resolve_per_row_intrabar_stop_pct(df, cfg)
        self.assertEqual(len(arr), 3)
        for v in arr:
            self.assertAlmostEqual(v, cfg.intrabar_stop_loss_pct, places=6)

    def test_resolver_with_empty_override_dict_uses_global_default(self) -> None:
        """传入空 override dict → 全部走 global default。"""
        cfg = OotEvaluationConfig(intrabar_stop_loss_pct_by_cluster_interval={})
        df = pd.DataFrame({
            "symbol": ["IF0", "RB0", "AU0"],
            "interval": ["day", "day", "day"],
        })
        arr = _resolve_per_row_intrabar_stop_pct(df, cfg)
        for v in arr:
            self.assertAlmostEqual(v, cfg.intrabar_stop_loss_pct, places=6)

    def test_post_init_rejects_bad_key_format(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(intrabar_stop_loss_pct_by_cluster_interval={"index_day": 0.03})  # 缺 '|'
        with self.assertRaises(ValueError):
            OotEvaluationConfig(intrabar_stop_loss_pct_by_cluster_interval={"index|day|extra": 0.03})

    def test_post_init_rejects_out_of_range_value(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.0001})  # 太小
        with self.assertRaises(ValueError):
            OotEvaluationConfig(intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.15})  # 太大

    def test_resolver_with_custom_overrides(self) -> None:
        """允许用户传入自定义 override 覆盖默认值（注意 interval key 用 normalize 后的形式）。"""
        cfg = OotEvaluationConfig(intrabar_stop_loss_pct_by_cluster_interval={
            "index|day": 0.04,
            # key 必须用 normalize 后的形式（"minute30" 是数据存储别名，会被 normalize 到 "30min"）
            "bond|30min": 0.008,
        })
        df = pd.DataFrame({
            "symbol": ["IF0", "T0"],
            "interval": ["day", "30min"],
        })
        arr = _resolve_per_row_intrabar_stop_pct(df, cfg)
        self.assertAlmostEqual(arr[0], 0.04, places=6)
        self.assertAlmostEqual(arr[1], 0.008, places=6)

    def test_resolver_normalizes_override_alias_keys(self) -> None:
        """override key 支持 minute60 别名，匹配 interval=60min。"""
        cfg = OotEvaluationConfig(
            intrabar_stop_loss_pct_by_cluster_interval={
                "index|minute60": 0.02,
            }
        )
        df = pd.DataFrame(
            {
                "symbol": ["IF0"],
                "interval": ["60min"],
            }
        )
        arr = _resolve_per_row_intrabar_stop_pct(df, cfg)
        self.assertAlmostEqual(float(arr[0]), 0.02, places=6)


if __name__ == "__main__":
    unittest.main()
