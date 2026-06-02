"""HTF gate 按 (cluster, interval) 配置 htf_missing fallback 测试。

历史背景（2026-05-24 OOT 诊断 `cta/docs/review/20260524_profit_aware_postmortem.md` §7）：
  - `htf_missing` 单一 reason 拦截 14,885 / 15,038 行（占候选 21%），是最大单一 block 源；
  - 当前全局 `fallback_when_htf_missing="skip"` 一刀切：任何 (symbol, exchange) 在 HTF
    reference 缺数据都直接拦截；
  - 在 minute60/30 路径上 HTF reference 仅来自当组预测（cross-symbol 覆盖稀），
    系统性拦截了大量本可通过的候选。

修复设计：
  - 新增 `fallback_when_htf_missing_by_cluster_interval: dict[str, str]` 字段
  - 2026-05-24 第三轮：默认翻为「minute60 / minute30 路径上的 6 个非 bond/precious cluster 放宽
    为 "both"」；day 路径继续严格 skip
  - 2026-05-28：bond minute60/minute30 追加放宽为 "both"（优先修复 bond OOT 系统性
    `htf_missing` 导致 0 成交）；precious 仍保持严格 skip 观察
  - 当某 (cluster, interval) 配 "both"，缺 HTF 时按"中性放行"处理（不再 emit htf_missing）
  - 当配 "skip" 或未配置，沿用全局 fallback_when_htf_missing

测试覆盖：
  1) bond|60min 在默认 dict 中 → 缺 HTF 自动 neutral 放行
  2) metal|60min 在默认 dict 中 → 缺 HTF 自动 neutral 放行
  3) precious|60min 不在默认放宽 dict 中 → 走全局 skip → emit htf_missing
  4) 显式传空 dict → 全部行走全局 fallback（旧严格行为）
  5) 当全局 fallback="both" 时缺 HTF 全部 neutral 放行（已有行为）
  6) 按 cluster|interval 配 "both"：仅这些行 neutral 放行
  7) 按 cluster|interval 配 "skip"：和默认一样拦截
  8) interval 别名 normalize_portfolio_interval
  9) post_init 校验：值必须是 "skip" / "both" 之一
  10) post_init 校验：key 必须是 cluster|interval 格式
"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.portfolio_logic.config import IntervalGateConfig
from cta.portfolio_logic.interval_gate import HtfGate


def _row(symbol: str, exchange: str, side: str, interval: str = "60min") -> dict:
    return {
        "symbol": symbol,
        "exchange": exchange,
        "side": side,
        "interval": interval,
    }


class TestHtfMissingFallback(unittest.TestCase):
    def setUp(self) -> None:
        self.now = pd.Timestamp("2024-01-03 10:00:00")

    def test_default_bond_60min_passes_neutral(self) -> None:
        """bond|60min 在默认放宽 dict 中 → 缺 HTF 直接 neutral 放行。"""
        cfg = IntervalGateConfig()
        gate = HtfGate(cfg)
        df = pd.DataFrame([_row("T0", "CFFEX", "long", "60min")])
        out = gate.filter(df, htf_state={}, current_time=self.now)
        self.assertTrue(bool(out["htf_allowed"].iloc[0]))
        self.assertEqual(out["htf_block_reason"].iloc[0], "")

    def test_default_metal_60min_passes_neutral(self) -> None:
        """metal|60min 在默认放宽 dict 中 → 缺 HTF 直接 neutral 放行。"""
        cfg = IntervalGateConfig()
        gate = HtfGate(cfg)
        df = pd.DataFrame([_row("CU0", "SHFE", "long", "60min")])  # metal in default dict
        out = gate.filter(df, htf_state={}, current_time=self.now)
        self.assertTrue(bool(out["htf_allowed"].iloc[0]))
        self.assertEqual(out["htf_block_reason"].iloc[0], "")

    def test_default_precious_60min_still_falls_back_to_global_skip(self) -> None:
        """precious 仍不在默认放宽 dict 中 → 走全局 skip。"""
        cfg = IntervalGateConfig()
        gate = HtfGate(cfg)
        df = pd.DataFrame([_row("AU0", "SHFE", "long", "60min")])
        out = gate.filter(df, htf_state={}, current_time=self.now)
        self.assertFalse(bool(out["htf_allowed"].iloc[0]))
        self.assertEqual(out["htf_block_reason"].iloc[0], "htf_missing")

    def test_explicit_empty_dict_reverts_to_global_skip(self) -> None:
        """显式传空 dict → 所有 cell 都走全局 skip（旧行为）。"""
        cfg = IntervalGateConfig(
            fallback_when_htf_missing="skip",
            fallback_when_htf_missing_by_cluster_interval={},
        )
        gate = HtfGate(cfg)
        df = pd.DataFrame([_row("CU0", "SHFE", "long", "60min")])
        out = gate.filter(df, htf_state={}, current_time=self.now)
        self.assertFalse(bool(out["htf_allowed"].iloc[0]))
        self.assertEqual(out["htf_block_reason"].iloc[0], "htf_missing")

    def test_global_fallback_both_passes_neutral(self) -> None:
        cfg = IntervalGateConfig(fallback_when_htf_missing="both")
        gate = HtfGate(cfg)
        df = pd.DataFrame([_row("T0", "CFFEX", "long")])
        out = gate.filter(df, htf_state={}, current_time=self.now)
        self.assertTrue(bool(out["htf_allowed"].iloc[0]))
        self.assertEqual(out["htf_block_reason"].iloc[0], "")

    def test_per_cell_both_overrides_global_skip(self) -> None:
        """全局 skip，bond|60min 单独 both → bond 行通过、其他簇行仍拦截。"""
        cfg = IntervalGateConfig(
            fallback_when_htf_missing="skip",
            fallback_when_htf_missing_by_cluster_interval={"bond|60min": "both"},
        )
        gate = HtfGate(cfg)
        df = pd.DataFrame([
            _row("T0", "CFFEX", "long", "60min"),    # bond → 放行
            _row("RB0", "SHFE", "long", "60min"),    # black → 仍拦
        ])
        out = gate.filter(df, htf_state={}, current_time=self.now)
        self.assertTrue(bool(out["htf_allowed"].iloc[0]))
        self.assertEqual(out["htf_block_reason"].iloc[0], "")
        self.assertFalse(bool(out["htf_allowed"].iloc[1]))
        self.assertEqual(out["htf_block_reason"].iloc[1], "htf_missing")

    def test_per_cell_skip_overrides_global_both(self) -> None:
        """全局 both，bond|day 单独 skip → bond day 行拦截、其他行继续放行。"""
        cfg = IntervalGateConfig(
            fallback_when_htf_missing="both",
            fallback_when_htf_missing_by_cluster_interval={"bond|day": "skip"},
        )
        gate = HtfGate(cfg)
        df = pd.DataFrame([
            _row("T0", "CFFEX", "long", "day"),     # bond day → 拦
            _row("RB0", "SHFE", "long", "day"),     # black day → 放
        ])
        out = gate.filter(df, htf_state={}, current_time=self.now)
        self.assertFalse(bool(out["htf_allowed"].iloc[0]))
        self.assertEqual(out["htf_block_reason"].iloc[0], "htf_missing")
        self.assertTrue(bool(out["htf_allowed"].iloc[1]))

    def test_interval_alias_normalized(self) -> None:
        """key 中 interval 应同样支持 minute60 / 60min 别名（normalize_portfolio_interval）。"""
        cfg = IntervalGateConfig(
            fallback_when_htf_missing="skip",
            fallback_when_htf_missing_by_cluster_interval={"bond|minute60": "both"},
        )
        gate = HtfGate(cfg)
        df = pd.DataFrame([_row("TF0", "CFFEX", "long", "60min")])
        out = gate.filter(df, htf_state={}, current_time=self.now)
        self.assertTrue(bool(out["htf_allowed"].iloc[0]))

    def test_post_init_rejects_invalid_value(self) -> None:
        with self.assertRaises(ValueError):
            IntervalGateConfig(
                fallback_when_htf_missing_by_cluster_interval={"bond|day": "neutral"},
            )

    def test_post_init_rejects_bad_key_format(self) -> None:
        with self.assertRaises(ValueError):
            IntervalGateConfig(
                fallback_when_htf_missing_by_cluster_interval={"bond_only": "both"},
            )


if __name__ == "__main__":
    unittest.main()
