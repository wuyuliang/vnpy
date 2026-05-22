"""Pyramid eligibility model."""
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


def _derive_pyramid_label(df: pd.DataFrame, *, edge_threshold: float = 1.2) -> np.ndarray:
    edge, executed = derive_edge_and_executed(df, mae_penalty=0.7)
    return ((executed) & (edge > float(edge_threshold))).astype(int).to_numpy()


@dataclass
class PyramidEligibilityModel:
    random_state: int = 42
    edge_threshold: float = 1.2
    add_score_high_threshold: float = 0.8
    add_score_low_threshold: float = 0.6
    size_mult_high: float = 0.5
    size_mult_low: float = 0.25
    max_iter: int = 140
    min_samples_leaf: int = 20
    learning_rate: float = 0.05
    max_depth: int = 3
    estimator: Pipeline | None = None

    def fit(self, df: pd.DataFrame, feature_columns: Iterable[str], label_column: str = "label_pyramid_add") -> "PyramidEligibilityModel":
        feats = list(feature_columns)
        x = df[feats].copy()
        if label_column in df.columns:
            y = pd.to_numeric(df[label_column], errors="coerce").fillna(0).astype(int).to_numpy()
        else:
            y = _derive_pyramid_label(df, edge_threshold=float(self.edge_threshold))
        self.estimator = fit_edge_binary_estimator(
            x=x,
            y=y,
            random_state=self.random_state,
            max_iter=self.max_iter,
            min_samples_leaf=self.min_samples_leaf,
            learning_rate=self.learning_rate,
            max_depth=self.max_depth,
            on_dummy_fallback=lambda pos, neg: logger.warning(
                "pyramid_eligibility: fallback to DummyClassifier due to single-class labels (pos=%d neg=%d)",
                pos,
                neg,
            ),
        )
        return self

    def predict(self, df: pd.DataFrame, feature_columns: Iterable[str]) -> pd.DataFrame:
        if self.estimator is None:
            raise RuntimeError("PyramidEligibilityModel not fitted")
        feats = list(feature_columns)
        x = df[feats].copy()
        prob = self.estimator.predict_proba(x)
        score = prob[:, 1] if prob.ndim == 2 and prob.shape[1] >= 2 else prob.reshape(-1)
        size_mult = np.where(
            score >= float(self.add_score_high_threshold),
            float(self.size_mult_high),
            np.where(
                score >= float(self.add_score_low_threshold),
                float(self.size_mult_low),
                0.0,
            ),
        )
        return pd.DataFrame(
            {
                "pyramid_add_score": score.astype(float),
                "pyramid_size_mult": size_mult.astype(float),
            },
            index=df.index,
        )


__all__ = ["PyramidEligibilityModel", "_derive_pyramid_label"]
