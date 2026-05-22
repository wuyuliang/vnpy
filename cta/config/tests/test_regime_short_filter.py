"""Regime-aware short filter 单测。

历史背景（2026-05-19 OOT 诊断）：
  - 现有 regime_gate 用 pred_regime_label（模型预测），2024 牛市训练样本不足时
    模型把牛市判为 range，allow_range_in_regime_gate=True 默认放行 → short 全过
  - 治本：用真实 regime_label（candidate 行已有）做硬过滤，不依赖模型预测

本测试覆盖：
  1) 默认 off：use_regime_short_filter=False 全 pass
  2) regime_label=trend_up + side=short → blocked_regime_short_filter
  3) regime_label=trend_up + side=long → pass（不拦 long）
  4) block_labels 可扩展为多个 regime
  5) 仅启用 (cluster, interval) 受影响
  6) regime_label 列缺失时降级到 pass-all
"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.oot_gates import apply_regime_short_filter


def _empty_state(n: int) -> tuple[pd.Series, pd.Series]:
    gate = pd.Series(True, index=range(n), dtype=bool)
    reason = pd.Series("", index=range(n), dtype=object)
    return gate, reason


class TestRegimeShortFilter(unittest.TestCase):
    def test_default_disabled_passes_all(self) -> None:
        cfg = OotEvaluationConfig()
        df = pd.DataFrame({
            "symbol": ["IF0", "IF0"],
            "interval": ["day", "day"],
            "side": ["short", "long"],
            "regime_label": ["trend_up", "trend_up"],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(gate2.all())
        self.assertTrue((reason2 == "").all())

    def test_blocks_short_in_trend_up(self) -> None:
        """regime_label=trend_up + side=short → blocked_regime_short_filter。"""
        cfg = OotEvaluationConfig(
            use_regime_short_filter=True,
            regime_short_filter_enabled_by_cluster_interval={"index|day": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "IH0", "IC0"],
            "interval": ["day", "day", "day"],
            "side": ["short", "short", "short"],
            "regime_label": ["trend_up", "trend_up", "trend_up"],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertFalse(gate2.any())
        self.assertTrue((reason2 == "blocked_regime_short_filter").all())

    def test_allows_long_in_trend_up(self) -> None:
        """side=long 不被拦截，无论 regime_label 是什么。"""
        cfg = OotEvaluationConfig(
            use_regime_short_filter=True,
            regime_short_filter_enabled_by_cluster_interval={"index|day": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "IH0", "IC0"],
            "interval": ["day", "day", "day"],
            "side": ["long", "long", "long"],
            "regime_label": ["trend_up", "trend_up", "trend_up"],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(gate2.all())

    def test_allows_short_in_non_blocking_regime(self) -> None:
        """regime_label ∉ block_labels（如 range, trend_down）→ short 不拦。"""
        cfg = OotEvaluationConfig(
            use_regime_short_filter=True,
            regime_short_filter_enabled_by_cluster_interval={"index|day": True},
            regime_short_block_labels=("trend_up",),
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "IF0", "IF0", "IF0"],
            "interval": ["day", "day", "day", "day"],
            "side": ["short", "short", "short", "short"],
            "regime_label": ["range", "trend_down", "transition", "compression"],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(gate2.all())

    def test_block_labels_can_be_extended(self) -> None:
        """block_labels=('trend_up', 'expansion') 时 expansion 也拦 short。"""
        cfg = OotEvaluationConfig(
            use_regime_short_filter=True,
            regime_short_filter_enabled_by_cluster_interval={"index|day": True},
            regime_short_block_labels=("trend_up", "expansion"),
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "IF0", "IF0"],
            "interval": ["day", "day", "day"],
            "side": ["short", "short", "short"],
            "regime_label": ["trend_up", "expansion", "range"],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertFalse(bool(gate2.iloc[0]))
        self.assertFalse(bool(gate2.iloc[1]))
        self.assertTrue(bool(gate2.iloc[2]))  # range 不拦

    def test_only_applies_to_enabled_cluster_interval(self) -> None:
        cfg = OotEvaluationConfig(
            use_regime_short_filter=True,
            regime_short_filter_enabled_by_cluster_interval={"index|day": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "RB0", "AU0"],
            "interval": ["day", "day", "day"],
            "side": ["short", "short", "short"],
            "regime_label": ["trend_up", "trend_up", "trend_up"],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertFalse(bool(gate2.iloc[0]))
        self.assertEqual(reason2.iloc[0], "blocked_regime_short_filter")
        # 不在 enabled_keys 的 (cluster, interval) 全 pass
        self.assertTrue(bool(gate2.iloc[1]))
        self.assertTrue(bool(gate2.iloc[2]))

    def test_missing_label_column_passes_all(self) -> None:
        cfg = OotEvaluationConfig(
            use_regime_short_filter=True,
            regime_short_filter_enabled_by_cluster_interval={"index|day": True},
        )
        df = pd.DataFrame({
            "symbol": ["IF0", "IF0"],
            "interval": ["day", "day"],
            "side": ["short", "long"],
            # 无 regime_label 列
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(gate2.all())

    def test_empty_enabled_dict_is_noop(self) -> None:
        cfg = OotEvaluationConfig(
            use_regime_short_filter=True,
            regime_short_filter_enabled_by_cluster_interval={},
        )
        df = pd.DataFrame({
            "symbol": ["IF0"], "interval": ["day"], "side": ["short"], "regime_label": ["trend_up"],
        })
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(gate2.all())

    def test_enabled_key_accepts_day_alias(self) -> None:
        cfg = OotEvaluationConfig(
            use_regime_short_filter=True,
            regime_short_filter_enabled_by_cluster_interval={"index|daily": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        df = pd.DataFrame(
            {
                "symbol": ["IF0"],
                "interval": ["day"],
                "side": ["short"],
                "regime_label": ["trend_up"],
            }
        )
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertFalse(bool(gate2.iloc[0]))
        self.assertEqual(reason2.iloc[0], "blocked_regime_short_filter")

    def test_skips_enabled_minute_interval_even_with_stop_override(self) -> None:
        cfg = OotEvaluationConfig(
            use_regime_short_filter=True,
            regime_short_filter_enabled_by_cluster_interval={"index|minute60": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|60min": 0.03},
        )
        df = pd.DataFrame(
            {
                "symbol": ["IF0"],
                "interval": ["60min"],
                "side": ["short"],
                "regime_label": ["trend_up"],
            }
        )
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(bool(gate2.iloc[0]))
        self.assertEqual(reason2.iloc[0], "")

    def test_skips_day_when_stop_loss_not_widened(self) -> None:
        cfg = OotEvaluationConfig(
            use_regime_short_filter=True,
            regime_short_filter_enabled_by_cluster_interval={"index|day": True},
        )
        df = pd.DataFrame(
            {
                "symbol": ["IF0"],
                "interval": ["day"],
                "side": ["short"],
                "regime_label": ["trend_up"],
            }
        )
        gate, reason = _empty_state(len(df))
        _, gate2, reason2 = apply_regime_short_filter(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=reason)
        self.assertTrue(bool(gate2.iloc[0]))
        self.assertEqual(reason2.iloc[0], "")

    def test_post_init_rejects_bad_label(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(regime_short_block_labels=("not_a_regime",))

    def test_post_init_rejects_empty_label_tuple(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(regime_short_block_labels=())

    def test_post_init_rejects_bad_enable_key(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(regime_short_filter_enabled_by_cluster_interval={"foo_bar": True})
        with self.assertRaises(ValueError):
            OotEvaluationConfig(regime_short_filter_enabled_by_cluster_interval={"foo|day|extra": True})


if __name__ == "__main__":
    unittest.main()
