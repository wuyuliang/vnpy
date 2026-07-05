"""Model-gate application for OOT real-execution evaluation."""
from __future__ import annotations

import pandas as pd

from cta.model.oot.oot_trade_filter_gate import apply_trade_filter_gate
from cta.model.oot.hard_stop_entry_filter import apply_hard_stop_entry_filter
from cta.model.oot.interval_cell_gate import apply_static_interval_cell_gate
from cta.model.oot.pipeline_oot_evaluation_base import (
    BR_BLOCKED_FINAL_DECISION_GATE,
    BR_BLOCKED_MFE_MAE_GATE,
    BR_BLOCKED_REGIME_GATE,
)
from cta.model.oot.pipeline_oot_evaluation_inputs import attach_bull_mode_columns
from cta.model.oot.signal_type_allowlist import apply_signal_type_allowlist


def _apply_preblocked_status(
    df: pd.DataFrame,
    gate: pd.Series,
    reason: pd.Series,
) -> tuple[pd.Series, pd.Series]:
    status = df.get("execution_status", pd.Series([""] * len(df), index=df.index)).astype(str)
    block = df.get("block_reason", pd.Series([""] * len(df), index=df.index)).astype(str)
    blocked = status.str.startswith("blocked_") | block.str.startswith("blocked_")
    if bool(blocked.any()):
        resolved = block.where(block.str.strip() != "", status)
        reason.loc[blocked & (reason == "")] = resolved.loc[blocked]
        gate = gate & (~blocked)
    return gate.astype(bool), reason.astype(str)


def apply_oot_model_gates(df: pd.DataFrame, cfg: object) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Apply trade/regime/trend/MFE/final gates and return pass mask plus reasons."""
    gate_by_legacy = pd.Series(True, index=df.index, dtype=bool)
    hard_gate = pd.Series(True, index=df.index, dtype=bool)
    model_block_reason = pd.Series("", index=df.index, dtype=object)
    df = attach_bull_mode_columns(df, cfg)
    df = apply_signal_type_allowlist(
        df,
        allowlist=tuple(getattr(cfg, "signal_type_allowlist", ())),
        enabled=bool(getattr(cfg, "use_signal_type_allowlist", False)),
    )
    gate_by_legacy, model_block_reason = _apply_preblocked_status(
        df, gate_by_legacy, model_block_reason
    )
    hard_gate = gate_by_legacy.copy()
    df = apply_static_interval_cell_gate(
        df,
        disabled_intervals=tuple(getattr(cfg, "disabled_intervals", ())),
        disabled_cells=tuple(getattr(cfg, "disabled_cluster_signal_interval_cells", ())),
        enabled_cells=tuple(getattr(cfg, "enabled_cluster_signal_interval_cells", ())),
    )
    gate_by_legacy, model_block_reason = _apply_preblocked_status(
        df, gate_by_legacy, model_block_reason
    )
    hard_gate = gate_by_legacy.copy()
    df = apply_hard_stop_entry_filter(df, cfg)
    gate_by_legacy, model_block_reason = _apply_preblocked_status(
        df, gate_by_legacy, model_block_reason
    )
    hard_gate = gate_by_legacy.copy()
    df, gate_by_legacy, model_block_reason = apply_trade_filter_gate(
        df, cfg=cfg, gate_by_legacy=gate_by_legacy, model_block_reason=model_block_reason
    )
    if getattr(cfg, "use_regime_gate", False) and "pred_regime_label" in df.columns:
        regime = df["pred_regime_label"].astype(str).str.lower()
        side = df.get("side", pd.Series([""] * len(df), index=df.index)).astype(str).str.lower()
        pass_regime = pd.Series(True, index=df.index, dtype=bool)
        pass_regime.loc[side == "long"] = regime.loc[side == "long"] != "trend_down"
        pass_regime.loc[side == "short"] = regime.loc[side == "short"] != "trend_up"
        if not bool(getattr(cfg, "allow_range_in_regime_gate", True)):
            pass_regime = pass_regime & (regime != "range")
        model_block_reason.loc[~pass_regime & (model_block_reason == "")] = BR_BLOCKED_REGIME_GATE
        gate_by_legacy = gate_by_legacy & pass_regime
    if getattr(cfg, "use_mfe_mae_gate", False) and {"pred_mfe_atr", "pred_mae_atr"}.issubset(df.columns):
        pred_mfe = pd.to_numeric(df["pred_mfe_atr"], errors="coerce").fillna(0.0)
        pred_mae = pd.to_numeric(df["pred_mae_atr"], errors="coerce").fillna(0.0)
        pred_edge = pred_mfe - float(getattr(cfg, "mae_penalty", 1.0)) * pred_mae
        pass_mfe = pred_edge >= float(getattr(cfg, "min_pred_edge_atr", 0.0))
        model_block_reason.loc[~pass_mfe & (model_block_reason == "")] = BR_BLOCKED_MFE_MAE_GATE
        gate_by_legacy = gate_by_legacy & pass_mfe
    gate_by_stacking = pd.Series(True, index=df.index, dtype=bool)
    stacking_col = str(getattr(cfg, "stacking_score_column", "final_decision_score"))
    has_stacking_col = stacking_col in df.columns
    if bool(getattr(cfg, "use_stacking_gate", False)) and has_stacking_col:
        stack_score = pd.to_numeric(df[stacking_col], errors="coerce").fillna(0.0)
        gate_by_stacking = stack_score >= float(getattr(cfg, "stacking_score_threshold", 0.5))
    if bool(getattr(cfg, "use_stacking_gate", False)) and has_stacking_col and bool(getattr(cfg, "stacking_gate_overrides_individual_gates", True)):
        gate = hard_gate & gate_by_stacking
        model_block_reason.loc[~gate_by_stacking & (model_block_reason == "")] = BR_BLOCKED_FINAL_DECISION_GATE
    elif bool(getattr(cfg, "use_stacking_gate", False)) and has_stacking_col:
        gate = gate_by_legacy & gate_by_stacking
        model_block_reason.loc[gate_by_legacy & ~gate_by_stacking & (model_block_reason == "")] = BR_BLOCKED_FINAL_DECISION_GATE
    else:
        gate = gate_by_legacy
    return df, gate.astype(bool), model_block_reason.astype(str)


__all__ = ["apply_oot_model_gates"]
