"""OOT model-gating helpers.

- ``filter_candidates``：legacy 一体化入口（trade_filter + regime + mfe_mae + stacking）。
  现 ``pipeline_oot_evaluation.py`` 已 inline 多数 gate 逻辑，此函数仅供脚本/测试使用。
"""
from __future__ import annotations

import logging

import pandas as pd

from cta.model.oot.block_reasons import (
    BR_BLOCKED_FINAL_DECISION_GATE,
    BR_BLOCKED_MFE_MAE_GATE,
    BR_BLOCKED_REGIME_GATE,
    BR_BLOCKED_TRADE_FILTER,
)

logger = logging.getLogger(__name__)


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


__all__ = [
    "filter_candidates",
]
