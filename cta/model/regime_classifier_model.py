"""Regime Classifier model."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, f1_score
from sklearn.pipeline import Pipeline


@dataclass
class RegimeClassifierModel:
    """Multi-class classifier for market regime labels."""

    random_state: int = 42
    estimator: Pipeline | None = None
    model_kind: str = "uninitialized"

    def fit(
        self,
        df: pd.DataFrame,
        feature_columns: Iterable[str],
        label_column: str = "regime_label",
    ) -> "RegimeClassifierModel":
        feats = list(feature_columns)
        x = df[feats].copy()
        y = df[label_column].astype(str).to_numpy()

        if len(np.unique(y)) < 2:
            # 与 TradeFilterModel 保持一致：单类时使用 prior 策略，predict_proba 可用
            clf = DummyClassifier(strategy="prior")
            self.estimator = Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median")),
                    ("model", clf),
                ]
            )
            self.estimator.fit(x, y)
            self.model_kind = "dummy"
            return self

        pre = ColumnTransformer(
            transformers=[
                (
                    "num",
                    Pipeline([("imputer", SimpleImputer(strategy="median"))]),
                    feats,
                )
            ],
            remainder="drop",
        )
        model = RandomForestClassifier(
            n_estimators=300,
            max_depth=8,
            min_samples_leaf=5,
            random_state=self.random_state,
            n_jobs=-1,
        )
        self.estimator = Pipeline([("pre", pre), ("model", model)])
        self.estimator.fit(x, y)
        self.model_kind = "random_forest"
        return self

    def predict(self, df: pd.DataFrame, feature_columns: Iterable[str]) -> np.ndarray:
        if self.estimator is None:
            raise RuntimeError("RegimeClassifierModel not fitted")
        x = df[list(feature_columns)].copy()
        pred = self.estimator.predict(x)
        return np.asarray(pred, dtype=object)

    def get_top_feature_importance(self, feature_columns: Iterable[str], top_k: int = 10) -> pd.DataFrame:
        """Return top feature importance rows."""
        if self.estimator is None:
            raise RuntimeError("RegimeClassifierModel not fitted")
        feats = list(feature_columns)
        if not feats or int(top_k) <= 0:
            return pd.DataFrame(columns=["feature", "importance"])

        est = self.estimator
        model_step = est.named_steps.get("model") if hasattr(est, "named_steps") else None
        if model_step is not None and hasattr(model_step, "feature_importances_"):
            imp = np.asarray(getattr(model_step, "feature_importances_"), dtype=float)
        else:
            imp = np.zeros(len(feats), dtype=float)

        if imp.shape[0] != len(feats):
            imp = np.resize(imp, len(feats))
        out = pd.DataFrame(
            {
                "feature": feats,
                "importance": np.nan_to_num(imp, nan=0.0, posinf=0.0, neginf=0.0),
            }
        )
        out = out.sort_values(["importance", "feature"], ascending=[False, True]).head(int(top_k)).reset_index(drop=True)
        return out

    def save(self, path: Path) -> None:
        if self.estimator is None:
            raise RuntimeError("RegimeClassifierModel not fitted")
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "random_state": self.random_state,
                "model_kind": self.model_kind,
                "estimator": self.estimator,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path) -> "RegimeClassifierModel":
        obj = joblib.load(path)
        out = cls(random_state=int(obj.get("random_state", 42)))
        out.estimator = obj["estimator"]
        # B7 fix: legacy_no_kind 比 unknown 更明确地告诉读者这是"旧 joblib"。
        out.model_kind = str(obj.get("model_kind", "legacy_no_kind"))
        return out


def evaluate_regime_model(
    model: RegimeClassifierModel,
    df: pd.DataFrame,
    feature_columns: Iterable[str],
    label_column: str = "regime_label",
) -> dict[str, float]:
    y_true = df[label_column].astype(str).to_numpy()
    y_pred = model.predict(df, feature_columns)
    return {
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
    }


__all__ = [
    "RegimeClassifierModel",
    "evaluate_regime_model",
]
