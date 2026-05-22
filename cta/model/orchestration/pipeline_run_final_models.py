"""Final-decision side-specific model helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.model.training.final_decision_model import FinalDecisionModel


def fit_side_final_model(
    df: pd.DataFrame,
    *,
    feature_columns: list[str],
    random_state: int,
    sample_weight: np.ndarray | None,
    side: str,
) -> FinalDecisionModel | None:
    """Fit a side-specific final model when that side has enough label diversity."""
    side_s = df.get("side", pd.Series([""] * len(df), index=df.index)).astype(str).str.lower()
    mask = side_s == str(side).lower()
    if not bool(mask.any()):
        return None
    subset = df.loc[mask].copy()
    y = pd.to_numeric(subset.get("label_class", 0), errors="coerce").fillna(0).astype(int).to_numpy()
    if len(np.unique(y)) < 2:
        return None
    if sample_weight is None:
        sub_weight = None
    else:
        full_weight = np.asarray(sample_weight, dtype=float).reshape(-1)
        sub_weight = full_weight[np.asarray(mask, dtype=bool)]
    return FinalDecisionModel(random_state=random_state).fit(
        subset,
        feature_columns=feature_columns,
        label_column="label_class",
        sample_weight=sub_weight,
        ensemble=True,
    )


def predict_dual_side_final_scores(
    df: pd.DataFrame,
    *,
    feature_columns: list[str],
    global_model: FinalDecisionModel,
    long_model: FinalDecisionModel | None,
    short_model: FinalDecisionModel | None,
) -> tuple[np.ndarray, np.ndarray, np.ndarray, np.ndarray]:
    """Return selected, long, short final scores plus model-kind tags."""
    long_scores = (
        long_model.predict_proba(df, feature_columns=feature_columns)
        if long_model is not None
        else global_model.predict_proba(df, feature_columns=feature_columns)
    )
    short_scores = (
        short_model.predict_proba(df, feature_columns=feature_columns)
        if short_model is not None
        else global_model.predict_proba(df, feature_columns=feature_columns)
    )
    side = df.get("side", pd.Series([""] * len(df), index=df.index)).astype(str).str.lower().to_numpy()
    final_scores = np.where(side == "short", short_scores, long_scores).astype(float)
    long_kind = f"{(long_model.model_kind if long_model is not None else global_model.model_kind)}__long"
    short_kind = f"{(short_model.model_kind if short_model is not None else global_model.model_kind)}__short"
    kinds = np.where(side == "short", short_kind, long_kind).astype(object)
    return final_scores, long_scores.astype(float), short_scores.astype(float), kinds


__all__ = ["fit_side_final_model", "predict_dual_side_final_scores"]
