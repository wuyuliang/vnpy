"""Prediction-frame assembly for ``run_model_pipeline``."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from cta.config.baseline_skill_suite_config import LABEL_MAE_PENALTY
from cta.model.dataset.pipeline_meta_features import _build_final_decision_features
from cta.model.orchestration.pipeline_run_final_models import predict_dual_side_final_scores

_PREDICTION_BASE_COLUMNS: tuple[str, ...] = (
    "symbol",
    "exchange",
    "interval",
    "datetime",
    "signal_datetime",
    "exit_datetime",
    "signal_type",
    "side",
    "order_type",
    "candidate_status",
    "is_executed",
    "entry_price",
    "exit_price_ref",
    "trigger",
    "stop_price",
    "label_class",
    "regime_label",
    "generic_ma_alignment",
    "ma_alignment",
    "future_mfe_atr",
    "future_mae_atr",
    "future_pnl_atr",
)


def _ensure_ma_alignment_pair(pred_df: pd.DataFrame, pred_base: pd.DataFrame) -> None:
    if "generic_ma_alignment" not in pred_df.columns:
        if "generic_ma_alignment" in pred_base.columns:
            pred_df["generic_ma_alignment"] = pred_base["generic_ma_alignment"].to_numpy()
        elif "ma_alignment" in pred_base.columns:
            pred_df["generic_ma_alignment"] = pred_base["ma_alignment"].to_numpy()
        else:
            pred_df["generic_ma_alignment"] = np.nan
    if "ma_alignment" not in pred_df.columns:
        if "ma_alignment" in pred_base.columns:
            pred_df["ma_alignment"] = pred_base["ma_alignment"].to_numpy()
        elif "generic_ma_alignment" in pred_base.columns:
            pred_df["ma_alignment"] = pred_base["generic_ma_alignment"].to_numpy()
        else:
            pred_df["ma_alignment"] = np.nan


def build_test_prediction_frame(
    *,
    pred_base: pd.DataFrame,
    signal_type_key: str,
    window_id: int,
    trade_model: Any,
    trade_feature_columns: list[str],
    trade_calibrator: Any,
    regime_model: Any,
    regime_feature_columns: list[str],
    mfe_mae_model: Any,
    mfe_feature_columns: list[str],
    final_feature_columns: list[str],
    final_model: Any,
    final_model_long: Any,
    final_model_short: Any,
    bull_strength_model: Any,
    trend_persistence_model: Any,
    pyramid_model: Any,
) -> pd.DataFrame:
    """Build the OOT prediction frame consumed by reporting and OOT execution."""
    pred_df = pred_base[[c for c in _PREDICTION_BASE_COLUMNS if c in pred_base.columns]].copy()
    _ensure_ma_alignment_pair(pred_df, pred_base)
    pred_df["model_signal_type"] = signal_type_key
    pred_df["window_id"] = window_id
    pred_df["pred_split"] = "test"
    pred_df["trade_filter_prob"] = trade_model.predict_proba(
        pred_base, feature_columns=trade_feature_columns
    )
    pred_df = trade_calibrator.transform(pred_df)
    pred_df["pred_regime_label"] = regime_model.predict(
        pred_base, feature_columns=regime_feature_columns
    )
    if mfe_mae_model is None:
        pred_df["pred_mfe_atr"] = np.nan
        pred_df["pred_mae_atr"] = np.nan
    else:
        mfe_pred = mfe_mae_model.predict(pred_base, feature_columns=mfe_feature_columns)
        pred_df["pred_mfe_atr"] = mfe_pred["pred_mfe_atr"].to_numpy()
        pred_df["pred_mae_atr"] = mfe_pred["pred_mae_atr"].to_numpy()
    pred_meta = _build_final_decision_features(
        pred_base,
        trade_prob=np.asarray(pred_df["trade_filter_prob"], dtype=float),
        regime_label=pred_df["pred_regime_label"].astype(str).tolist(),
        pred_mfe=np.asarray(pd.to_numeric(pred_df["pred_mfe_atr"], errors="coerce").fillna(0.0), dtype=float),
        pred_mae=np.asarray(pd.to_numeric(pred_df["pred_mae_atr"], errors="coerce").fillna(0.0), dtype=float),
        mae_penalty=LABEL_MAE_PENALTY,
    )
    for col in final_feature_columns:
        pred_df[col] = pred_meta[col].to_numpy()
    final_scores, final_scores_long, final_scores_short, final_kind = predict_dual_side_final_scores(
        pred_df,
        feature_columns=final_feature_columns,
        global_model=final_model,
        long_model=final_model_long,
        short_model=final_model_short,
    )
    pred_df["final_decision_score"] = final_scores
    pred_df["final_decision_score_long"] = final_scores_long
    pred_df["final_decision_score_short"] = final_scores_short
    pred_df["final_decision_model_kind"] = final_kind
    bull_pred = bull_strength_model.predict(pred_base, feature_columns=trade_feature_columns)
    pred_df["bull_strength_score"] = pd.to_numeric(
        bull_pred["bull_strength_score"], errors="coerce"
    ).fillna(0.0).to_numpy()
    pred_df["bull_mode"] = bull_pred["bull_mode"].astype(str).to_numpy()
    hold_pred = trend_persistence_model.predict(pred_base, feature_columns=mfe_feature_columns)
    pred_df["hold_extend_score"] = pd.to_numeric(
        hold_pred["hold_extend_score"], errors="coerce"
    ).fillna(0.0).to_numpy()
    pred_df["recommended_horizon_extension_bars"] = pd.to_numeric(
        hold_pred["recommended_horizon_extension_bars"], errors="coerce"
    ).fillna(0).astype(int).to_numpy()
    pyramid_pred = pyramid_model.predict(pred_base, feature_columns=mfe_feature_columns)
    pred_df["pyramid_add_score"] = pd.to_numeric(
        pyramid_pred["pyramid_add_score"], errors="coerce"
    ).fillna(0.0).to_numpy()
    pred_df["pyramid_size_mult"] = pd.to_numeric(
        pyramid_pred["pyramid_size_mult"], errors="coerce"
    ).fillna(0.0).to_numpy()
    return pred_df


__all__ = ["build_test_prediction_frame"]
