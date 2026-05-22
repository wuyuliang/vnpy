"""MA-cross 趋势过滤 gate 单测。

历史背景（2026-05-19 OOT 诊断）：
  - INDEX day 2024 牛市 13 笔 short 全 hard_stop
  - 策略 short bias：2024 候选 69% 为 short，方向错了再小的止损也打穿
  - 治本：OOT gate 层用 ma_alignment（多头/空头排列）拦截与趋势相反的 side

本测试覆盖：
  1) 默认 off：use_ma_cross_gate=False 时全 pass
  2) 多头排列拦 short：alignment=1 + side=short → blocked
  3) 空头排列拦 long：alignment=-1 + side=long → blocked
  4) 混合（alignment=0）放行
  5) 仅启用 (cluster, interval) 受影响
  6) ma_alignment 列缺失时降级到 pass-all
  7) post_init 校验 key 格式 / window / regime 标签
"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.oot_gates import apply_ma_cross_gate


def _empty_state(n: int) -> tuple[pd.Series, pd.Series]:
    gate = pd.Series(True, index=range(n), dtype=bool)
    reason = pd.Series("", index=range(n), dtype=object)
    return gate, reason


class TestMaCrossGate(unittest.TestCase):
    def test_default_disabled_passes_all(self) -> None:
        """默认 cfg.use_ma_cross_gate=False 时所有行全 pass。"""
        cfg = OotEvaluationConfig()
        df = pd.DataFrame({
            "symbol": ["IF0", "IF0", "RB0"],
            "interval": ["day", "day", "day"],
            "side": ["short", "long", "short"],
            "ma_alignment": [1, -1, 1],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(gate2.all())
        self.assertTrue((reason2 == "").all())

    def test_blocks_short_in_uptrend(self) -> None:
        """ma_alignment=1（多头排列）+ side=short → blocked_ma_cross_trend。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={"index|day": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "IH0", "IC0"],
            "interval": ["day", "day", "day"],
            "side": ["short", "short", "short"],
            "ma_alignment": [1, 1, 1],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertFalse(gate2.any())
        self.assertTrue((reason2 == "blocked_ma_cross_trend").all())

    def test_blocks_long_in_downtrend(self) -> None:
        """ma_alignment=-1（空头排列）+ side=long → blocked_ma_cross_trend。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={"index|day": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "IH0"],
            "interval": ["day", "day"],
            "side": ["long", "long"],
            "ma_alignment": [-1, -1],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertFalse(gate2.any())
        self.assertTrue((reason2 == "blocked_ma_cross_trend").all())

    def test_allows_both_when_alignment_mixed(self) -> None:
        """ma_alignment=0（混合）→ 不拦截。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={"index|day": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "IF0"],
            "interval": ["day", "day"],
            "side": ["short", "long"],
            "ma_alignment": [0, 0],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(gate2.all())
        self.assertTrue((reason2 == "").all())

    def test_only_applies_to_enabled_cluster_interval(self) -> None:
        """启用 index|day，其它 (cluster, interval) 不受影响。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={"index|day": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame({
            # index|day 多头拦 short；black|day 不在启用列表，应放行
            "symbol": ["IF0", "RB0", "AU0"],
            "interval": ["day", "day", "day"],
            "side": ["short", "short", "short"],
            "ma_alignment": [1, 1, 1],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        # row 0: index|day + short + bullish → blocked
        self.assertFalse(bool(gate2.iloc[0]))
        self.assertEqual(reason2.iloc[0], "blocked_ma_cross_trend")
        # row 1: black|day (RB0) 不启用 → pass
        self.assertTrue(bool(gate2.iloc[1]))
        self.assertEqual(reason2.iloc[1], "")
        # row 2: precious|day (AU0) 不启用 → pass
        self.assertTrue(bool(gate2.iloc[2]))
        self.assertEqual(reason2.iloc[2], "")

    def test_missing_alignment_column_passes_all(self) -> None:
        """ma_alignment 列缺失 → graceful degrade（全 pass）。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={"index|day": True},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "IF0"],
            "interval": ["day", "day"],
            "side": ["short", "long"],
            # 无 ma_alignment 列
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(gate2.all())
        self.assertTrue((reason2 == "").all())

    def test_fallback_to_plain_ma_alignment_when_generic_missing(self) -> None:
        """默认列改为 generic_ma_alignment 后，仍兼容仅有 ma_alignment 的旧表。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={"index|day": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
            ma_cross_alignment_column="generic_ma_alignment",
        )
        df = pd.DataFrame(
            {
                "symbol": ["IF0"],
                "interval": ["day"],
                "side": ["short"],
                "ma_alignment": [1],
            }
        )
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertFalse(bool(gate2.iloc[0]))
        self.assertEqual(reason2.iloc[0], "blocked_ma_cross_trend")

    def test_empty_enabled_dict_is_noop(self) -> None:
        """启用开关 on 但 enabled dict 为空 → no-op。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={},
        )
        df = pd.DataFrame({
            "symbol": ["IF0"], "interval": ["day"], "side": ["short"], "ma_alignment": [1],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(gate2.all())

    def test_enabled_key_accepts_day_alias(self) -> None:
        """enabled dict 支持 day 别名并命中 day 样本。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={"index|daily": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame(
            {
                "symbol": ["IF0"],
                "interval": ["day"],
                "side": ["short"],
                "ma_alignment": [1],
            }
        )
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertFalse(bool(gate2.iloc[0]))
        self.assertEqual(reason2.iloc[0], "blocked_ma_cross_trend")

    def test_skips_enabled_minute_interval_even_with_stop_override(self) -> None:
        """趋势 gate 只在 day 级别启用；60min 即使配置打开也 no-op。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={"index|minute60": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|60min": 0.03},
        )
        df = pd.DataFrame(
            {
                "symbol": ["IF0"],
                "interval": ["60min"],
                "side": ["short"],
                "ma_alignment": [1],
            }
        )
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(bool(gate2.iloc[0]))
        self.assertEqual(reason2.iloc[0], "")

    def test_skips_day_when_stop_loss_not_widened(self) -> None:
        """day 级别若仍用默认 1% 止损，趋势 gate 不启用。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={"index|day": True},
        )
        df = pd.DataFrame(
            {
                "symbol": ["IF0"],
                "interval": ["day"],
                "side": ["short"],
                "ma_alignment": [1],
            }
        )
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(bool(gate2.iloc[0]))
        self.assertEqual(reason2.iloc[0], "")

    def test_post_init_rejects_bad_key_format(self) -> None:
        """post_init 校验 key 必须形如 'cluster|interval'。"""
        with self.assertRaises(ValueError):
            OotEvaluationConfig(ma_cross_enabled_by_cluster_interval={"foo_bar": True})  # 缺 '|'
        with self.assertRaises(ValueError):
            OotEvaluationConfig(ma_cross_enabled_by_cluster_interval={"foo|day|extra": True})

    def test_post_init_rejects_non_bool_value(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(ma_cross_enabled_by_cluster_interval={"index|day": 1})  # type: ignore[dict-item]

    def test_post_init_rejects_bad_window(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(ma_cross_fast_window=20, ma_cross_slow_window=5)
        with self.assertRaises(ValueError):
            OotEvaluationConfig(ma_cross_fast_window=0, ma_cross_slow_window=10)

    def test_reason_does_not_overwrite_earlier_block(self) -> None:
        """之前的 block_reason 不被本 gate 覆盖（前面 reason 优先原则）。"""
        cfg = OotEvaluationConfig(
            use_ma_cross_gate=True,
            ma_cross_enabled_by_cluster_interval={"index|day": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame({
            "symbol": ["IF0"], "interval": ["day"], "side": ["short"], "ma_alignment": [1],
        })
        gate, reason = _empty_state(len(df))
        # 假装上一个 gate 已经标记 blocked_trade_filter
        reason.iloc[0] = "blocked_trade_filter"
        gate.iloc[0] = False
        _, gate2, reason2 = apply_ma_cross_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertFalse(bool(gate2.iloc[0]))
        # reason 应保留 blocked_trade_filter，不被 ma_cross 覆盖
        self.assertEqual(reason2.iloc[0], "blocked_trade_filter")


if __name__ == "__main__":
    unittest.main()
