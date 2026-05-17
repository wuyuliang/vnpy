"""Model parameter grids and tuning helpers."""
from __future__ import annotations

from cta.model.pipeline_orchestrator import (
    MFE_MAE_KIND_SKIPPED_NO_EXEC,
    _auc_gap,
    _default_label_stop_loss_pct,
    _mfe_mae_param_grid,
    _regime_classifier_param_grid,
    _safe_float,
    _select_best_param_trial,
    _trade_filter_param_grid,
    _train_mfe_mae_or_skip,
    _tune_mfe_mae_model,
    _tune_regime_classifier_model,
    _tune_trade_filter_model,
    _validate_stop_loss_pct_consistency,
)

__all__ = [
    "MFE_MAE_KIND_SKIPPED_NO_EXEC",
    "_safe_float",
    "_auc_gap",
    "_select_best_param_trial",
    "_default_label_stop_loss_pct",
    "_validate_stop_loss_pct_consistency",
    "_trade_filter_param_grid",
    "_regime_classifier_param_grid",
    "_mfe_mae_param_grid",
    "_train_mfe_mae_or_skip",
    "_tune_trade_filter_model",
    "_tune_regime_classifier_model",
    "_tune_mfe_mae_model",
]
