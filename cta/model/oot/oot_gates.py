"""OOT model-gating helpers.

包含三类 gate：
- ``filter_candidates``：legacy 一体化入口（trade_filter + regime + mfe_mae + stacking）。
  现 ``pipeline_oot_evaluation.py`` 已 inline 多数 gate 逻辑，此函数仅供脚本/测试使用。
- ``apply_ma_cross_gate``：按 ``ma_alignment`` 拦截与趋势相反的 side。
- ``apply_regime_short_filter``：按真实 ``regime_label`` 拦截牛市/扩张段的 short。

后两者是 2026-05-20 新增的治本 gate，默认 off，按 (cluster, interval) 灰度启用。
设计文档见 ``cta/docs/ma_cross_regime_aware_design.md``。
"""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from cta.model.oot.block_reasons import (
    BR_BLOCKED_FINAL_DECISION_GATE,
    BR_BLOCKED_MA_CROSS_TREND,
    BR_BLOCKED_MFE_MAE_GATE,
    BR_BLOCKED_REGIME_GATE,
    BR_BLOCKED_REGIME_SHORT_FILTER,
    BR_BLOCKED_TRADE_FILTER,
)
from cta.model.oot.oot_trade_filter_gate import _cluster_series, _interval_series
from cta.portfolio_logic.config import normalize_portfolio_interval

logger = logging.getLogger(__name__)
_WARNED_MISSING_MA_ALIGNMENT = False
_WARNED_MISSING_REGIME_LABEL = False


def filter_candidates(
    df: pd.DataFrame,
    *,
    use_trade_filter_gate: bool = True,
    trade_filter_threshold: float = 0.5,
    use_regime_gate: bool = False,
    allow_range_in_regime_gate: bool = True,
    use_mfe_mae_gate: bool = False,
    mae_penalty: float = 1.0,
    min_pred_edge_atr: float = 0.0,
    use_stacking_gate: bool = False,
    stacking_score_column: str = "final_decision_score",
    stacking_score_threshold: float = 0.5,
    stacking_gate_overrides_individual_gates: bool = True,
) -> pd.DataFrame:
    """Apply model gates and annotate pass/fail reason per row.

    Output columns:
      - ``model_gate_pass``: bool
      - ``model_gate_reason``: str (empty when pass)
    """
    out = df.copy()
    if out.empty:
        out["model_gate_pass"] = pd.Series(dtype=bool)
        out["model_gate_reason"] = pd.Series(dtype=object)
        return out

    gate_by_legacy = pd.Series(True, index=out.index, dtype=bool)
    reason = pd.Series("", index=out.index, dtype=object)

    if use_trade_filter_gate and "trade_filter_prob" in out.columns:
        prob = pd.to_numeric(out["trade_filter_prob"], errors="coerce").fillna(0.0)
        pass_trade = prob >= float(trade_filter_threshold)
        reason.loc[(~pass_trade) & (reason == "")] = BR_BLOCKED_TRADE_FILTER
        gate_by_legacy = gate_by_legacy & pass_trade

    if use_regime_gate and "pred_regime_label" in out.columns:
        regime = out["pred_regime_label"].astype(str).str.lower()
        side = out.get("side", pd.Series([""] * len(out), index=out.index)).astype(str).str.lower()
        pass_regime = pd.Series(True, index=out.index, dtype=bool)
        pass_regime.loc[side == "long"] = regime.loc[side == "long"] != "trend_down"
        pass_regime.loc[side == "short"] = regime.loc[side == "short"] != "trend_up"
        if not bool(allow_range_in_regime_gate):
            pass_regime = pass_regime & (regime != "range")
        reason.loc[(~pass_regime) & (reason == "")] = BR_BLOCKED_REGIME_GATE
        gate_by_legacy = gate_by_legacy & pass_regime

    if use_mfe_mae_gate and {"pred_mfe_atr", "pred_mae_atr"}.issubset(set(out.columns)):
        pred_mfe = pd.to_numeric(out["pred_mfe_atr"], errors="coerce").fillna(0.0)
        pred_mae = pd.to_numeric(out["pred_mae_atr"], errors="coerce").fillna(0.0)
        pred_edge = pred_mfe - float(mae_penalty) * pred_mae
        pass_mfe = pred_edge >= float(min_pred_edge_atr)
        reason.loc[(~pass_mfe) & (reason == "")] = BR_BLOCKED_MFE_MAE_GATE
        gate_by_legacy = gate_by_legacy & pass_mfe

    gate_by_stacking = pd.Series(True, index=out.index, dtype=bool)
    has_stacking_col = str(stacking_score_column) in out.columns
    if use_stacking_gate and has_stacking_col:
        stack_score = pd.to_numeric(out[str(stacking_score_column)], errors="coerce").fillna(0.0)
        gate_by_stacking = stack_score >= float(stacking_score_threshold)

    if use_stacking_gate and has_stacking_col and stacking_gate_overrides_individual_gates:
        final_gate = gate_by_stacking
        reason.loc[(~gate_by_stacking) & (reason == "")] = BR_BLOCKED_FINAL_DECISION_GATE
    elif use_stacking_gate and has_stacking_col:
        final_gate = gate_by_legacy & gate_by_stacking
        reason.loc[(gate_by_legacy) & (~gate_by_stacking) & (reason == "")] = BR_BLOCKED_FINAL_DECISION_GATE
    else:
        final_gate = gate_by_legacy

    out["model_gate_pass"] = final_gate.astype(bool)
    out["model_gate_reason"] = reason.where(~final_gate, "")
    return out


# ---------------------------------------------------------------------------
# 新增 gate：MA-cross 趋势过滤 + Regime-aware short filter
# 设计文档：cta/docs/ma_cross_regime_aware_design.md
# 调用风格仿 ``apply_trade_filter_gate``：
#     (df, gate_by_legacy, model_block_reason) -> (df, gate_by_legacy, model_block_reason)
# 由 ``pipeline_oot_evaluation.py`` 串行调用；不修改 df schema，只更新
# gate_by_legacy 和 model_block_reason 两个 Series。
# ---------------------------------------------------------------------------


def _enabled_keys_from_dict(mapping: dict[str, bool] | None) -> set[str]:
    """从 ``{"cluster|interval": bool}`` 提取 value=True 的 key 集合（去重 + lowercase）。"""
    out: set[str] = set()
    if not mapping:
        return out
    for key, value in mapping.items():
        if not bool(value):
            continue
        parts = str(key).strip().lower().split("|")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            continue
        out.add(f"{parts[0]}|{normalize_portfolio_interval(parts[1])}")
    return out


def _effective_trend_filter_enabled_keys(cfg: Any, enabled_keys: set[str]) -> set[str]:
    """Apply the shared trend-filter guard: day only, with widened stop loss."""
    if not enabled_keys:
        return set()
    stop_overrides = dict(getattr(cfg, "intrabar_stop_loss_pct_by_cluster_interval", {}) or {})
    global_stop = float(getattr(cfg, "intrabar_stop_loss_pct", 0.01))
    effective: set[str] = set()
    for key in enabled_keys:
        cluster, interval = key.split("|", 1)
        if normalize_portfolio_interval(interval) != "day":
            continue
        stop_pct = float(stop_overrides.get(f"{cluster}|day", global_stop))
        if stop_pct > 0.01 + 1e-9:
            effective.add(f"{cluster}|day")
    return effective


def apply_ma_cross_gate(
    df: pd.DataFrame,
    *,
    cfg: Any,
    gate_by_legacy: pd.Series,
    model_block_reason: pd.Series,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """按 ma_alignment 拦截与趋势相反的 side。

    规则（仅在 (cluster, interval) ∈ enabled_keys 时启用）：
      - ma_alignment >= 1 AND side=short  → block (blocked_ma_cross_trend)
      - ma_alignment <= -1 AND side=long  → block (blocked_ma_cross_trend)
      - 其他情况 / NaN / 缺列              → pass

    输入 ``df`` 不修改 schema；只更新 ``gate_by_legacy`` 和 ``model_block_reason``。
    """
    if not bool(getattr(cfg, "use_ma_cross_gate", False)):
        return df, gate_by_legacy, model_block_reason
    enabled_keys = _enabled_keys_from_dict(
        dict(getattr(cfg, "ma_cross_enabled_by_cluster_interval", {}) or {})
    )
    if not enabled_keys:
        return df, gate_by_legacy, model_block_reason
    enabled_keys = _effective_trend_filter_enabled_keys(cfg, enabled_keys)
    if not enabled_keys:
        return df, gate_by_legacy, model_block_reason

    alignment_col = str(getattr(cfg, "ma_cross_alignment_column", "generic_ma_alignment"))
    if alignment_col in df.columns:
        alignment_series = df[alignment_col]
    elif alignment_col != "ma_alignment" and "ma_alignment" in df.columns:
        alignment_series = df["ma_alignment"]
    else:
        global _WARNED_MISSING_MA_ALIGNMENT
        if not _WARNED_MISSING_MA_ALIGNMENT:
            logger.warning(
                "apply_ma_cross_gate: column %r missing; gate degrades to pass-all", alignment_col
            )
            _WARNED_MISSING_MA_ALIGNMENT = True
        return df, gate_by_legacy, model_block_reason

    alignment = pd.to_numeric(alignment_series, errors="coerce")
    clusters = _cluster_series(df).astype(str)
    intervals = _interval_series(df).astype(str)
    keys = clusters + "|" + intervals
    enabled_row = keys.isin(enabled_keys)

    side = (
        df.get("side", pd.Series([""] * len(df), index=df.index))
        .astype(str)
        .str.strip()
        .str.lower()
    )

    bullish = alignment >= 1
    bearish = alignment <= -1
    # 拦截：多头排列禁 short；空头排列禁 long
    block_short = enabled_row & bullish & (side == "short")
    block_long = enabled_row & bearish & (side == "long")
    blocked = block_short | block_long

    if not blocked.any():
        return df, gate_by_legacy, model_block_reason

    pass_mask = ~blocked
    new_block_mask = blocked & (model_block_reason.astype(str) == "")
    model_block_reason = model_block_reason.copy()
    model_block_reason.loc[new_block_mask] = BR_BLOCKED_MA_CROSS_TREND
    gate_by_legacy = gate_by_legacy & pass_mask
    return df, gate_by_legacy, model_block_reason


def apply_regime_short_filter(
    df: pd.DataFrame,
    *,
    cfg: Any,
    gate_by_legacy: pd.Series,
    model_block_reason: pd.Series,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """按真实 regime_label 在 trend_up 等标签下拦截 short。

    规则（仅在 (cluster, interval) ∈ enabled_keys 时启用）：
      - side=short AND regime_label ∈ block_labels  → block (blocked_regime_short_filter)
      - 其他情况 / NaN / 缺列                       → pass
    """
    if not bool(getattr(cfg, "use_regime_short_filter", False)):
        return df, gate_by_legacy, model_block_reason
    enabled_keys = _enabled_keys_from_dict(
        dict(getattr(cfg, "regime_short_filter_enabled_by_cluster_interval", {}) or {})
    )
    if not enabled_keys:
        return df, gate_by_legacy, model_block_reason
    enabled_keys = _effective_trend_filter_enabled_keys(cfg, enabled_keys)
    if not enabled_keys:
        return df, gate_by_legacy, model_block_reason

    label_col = str(getattr(cfg, "regime_short_filter_label_column", "regime_label"))
    if label_col not in df.columns:
        global _WARNED_MISSING_REGIME_LABEL
        if not _WARNED_MISSING_REGIME_LABEL:
            logger.warning(
                "apply_regime_short_filter: column %r missing; gate degrades to pass-all",
                label_col,
            )
            _WARNED_MISSING_REGIME_LABEL = True
        return df, gate_by_legacy, model_block_reason

    block_labels = tuple(
        str(x).strip().lower() for x in getattr(cfg, "regime_short_block_labels", ("trend_up",))
    )
    if not block_labels:
        return df, gate_by_legacy, model_block_reason

    labels = df[label_col].astype(str).str.strip().str.lower()
    clusters = _cluster_series(df).astype(str)
    intervals = _interval_series(df).astype(str)
    keys = clusters + "|" + intervals
    enabled_row = keys.isin(enabled_keys)

    side = (
        df.get("side", pd.Series([""] * len(df), index=df.index))
        .astype(str)
        .str.strip()
        .str.lower()
    )

    blocked = enabled_row & (side == "short") & labels.isin(block_labels)

    if not blocked.any():
        return df, gate_by_legacy, model_block_reason

    pass_mask = ~blocked
    new_block_mask = blocked & (model_block_reason.astype(str) == "")
    model_block_reason = model_block_reason.copy()
    model_block_reason.loc[new_block_mask] = BR_BLOCKED_REGIME_SHORT_FILTER
    gate_by_legacy = gate_by_legacy & pass_mask
    return df, gate_by_legacy, model_block_reason


__all__ = [
    "apply_ma_cross_gate",
    "apply_regime_short_filter",
    "filter_candidates",
]
