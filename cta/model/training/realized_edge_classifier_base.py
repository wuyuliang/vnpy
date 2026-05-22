"""Shared helpers for realized-edge binary classifiers."""
from __future__ import annotations

from typing import Callable

import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.pipeline import Pipeline


def derive_edge_and_executed(
    df: pd.DataFrame,
    *,
    mae_penalty: float = 0.7,
) -> tuple[pd.Series, pd.Series]:
    mfe = pd.to_numeric(df.get("future_mfe_atr", 0.0), errors="coerce").fillna(0.0)
    mae = pd.to_numeric(df.get("future_mae_atr", 0.0), errors="coerce").fillna(0.0)
    edge = mfe - float(mae_penalty) * mae
    executed = pd.to_numeric(df.get("is_executed", 0), errors="coerce").fillna(0).astype(int) == 1
    return edge, executed


def fit_edge_binary_estimator(
    *,
    x: pd.DataFrame,
    y: np.ndarray,
    random_state: int,
    max_iter: int,
    min_samples_leaf: int,
    learning_rate: float,
    max_depth: int,
    on_dummy_fallback: Callable[[int, int], None] | None = None,
) -> Pipeline:
    if len(np.unique(y)) < 2:
        if on_dummy_fallback is not None:
            on_dummy_fallback(int((y == 1).sum()), int((y == 0).sum()))
        model: Pipeline = Pipeline(
            [
                ("imputer", SimpleImputer(strategy="median")),
                ("model", DummyClassifier(strategy="prior")),
            ]
        )
        model.fit(x, y)
        return model
    model = Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            (
                "model",
                HistGradientBoostingClassifier(
                    max_depth=int(max_depth),
                    max_iter=int(max_iter),
                    min_samples_leaf=int(min_samples_leaf),
                    learning_rate=float(learning_rate),
                    random_state=int(random_state),
                ),
            ),
        ]
    )
    model.fit(x, y)
    return model


__all__ = ["derive_edge_and_executed", "fit_edge_binary_estimator"]
