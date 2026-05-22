"""Trend persistence / holding extension model."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd
from sklearn.pipeline import Pipeline

from cta.model.training.realized_edge_classifier_base import (
    derive_edge_and_executed,
    fit_edge_binary_estimator,
)

logger = logging.getLogger(__name__)


def _derive_hold_extend_label(df: pd.DataFrame, *, edge_threshold: float = 0.8) -> np.ndarray:
    edge, executed = derive_edge_and_executed(df, mae_penalty=0.7)
    return ((executed) & (edge > float(edge_threshold))).astype(int).to_numpy()


@dataclass
class TrendPersistenceModel:
    random_state: int = 42
    edge_threshold: float = 0.8
    max_extension_bars: int = 30
    max_iter: int = 140
    min_samples_leaf: int = 20
    learning_rate: float = 0.05
    max_depth: int = 3
    estimator: Pipeline | None = None

    def fit(self, df: pd.DataFrame, feature_columns: Iterable[str], label_column: str = "label_hold_extend") -> "TrendPersistenceModel":
        feats = list(feature_columns)
        x = df[feats].copy()
        if label_column in df.columns:
            y = pd.to_numeric(df[label_column], errors="coerce").fillna(0).astype(int).to_numpy()
        else:
            y = _derive_hold_extend_label(df, edge_threshold=float(self.edge_threshold))
        self.estimator = fit_edge_binary_estimator(
            x=x,
            y=y,
            random_state=self.random_state,
            max_iter=self.max_iter,
            min_samples_leaf=self.min_samples_leaf,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            on_dummy_fallback=lambda pos, neg: logger.warning(
                "trend_persistence: fallback to DummyClassifier due to single-class labels (pos=%d neg=%d)",
                pos,
                neg,
            ),
        )
        return self

    def predict(self, df: pd.DataFrame, feature_columns: Iterable[str]) -> pd.DataFrame:
        if self.estimator is None:
            raise RuntimeError("TrendPersistenceModel not fitted")
        feats = list(feature_columns)
        x = df[feats].copy()
        prob = self.estimator.predict_proba(x)
        score = prob[:, 1] if prob.ndim == 2 and prob.shape[1] >= 2 else prob.reshape(-1)
        ext = np.rint(np.clip(score, 0.0, 1.0) * float(self.max_extension_bars)).astype(int)
        return pd.DataFrame(
            {
                "hold_extend_score": score.astype(float),
                "recommended_horizon_extension_bars": ext,
            },
            index=df.index,
        )


__all__ = ["TrendPersistenceModel", "_derive_hold_extend_label"]
