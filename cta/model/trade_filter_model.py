"""Trade Filter model."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def _safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, y_prob))


@dataclass
class TradeFilterModel:
    """Binary classifier for trade opportunity filtering."""

    random_state: int = 42
    estimator: Pipeline | None = None
    model_kind: str = "uninitialized"

    def fit(
        self,
        df: pd.DataFrame,
        feature_columns: Iterable[str],
        label_column: str = "label_class",
    ) -> "TradeFilterModel":
        feats = list(feature_columns)
        x = df[feats].copy()
        y = df[label_column].astype(int).to_numpy()

        if len(np.unique(y)) < 2:
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
                    Pipeline(
                        [
                            ("imputer", SimpleImputer(strategy="median")),
                            ("scaler", StandardScaler()),
                        ]
                    ),
                    feats,
                )
            ],
            remainder="drop",
        )
        model = HistGradientBoostingClassifier(
            learning_rate=0.05,
            max_depth=4,
            max_iter=250,
            random_state=self.random_state,
        )
        self.estimator = Pipeline([("pre", pre), ("model", model)])
        self.estimator.fit(x, y)
        self.model_kind = "hist_gradient_boosting"
        return self

    def predict_proba(self, df: pd.DataFrame, feature_columns: Iterable[str]) -> np.ndarray:
        if self.estimator is None:
            raise RuntimeError("TradeFilterModel not fitted")
        feats = list(feature_columns)
        x = df[feats].copy()
        est = self.estimator
        if hasattr(est, "predict_proba"):
            prob = est.predict_proba(x)
            if prob.ndim == 2 and prob.shape[1] >= 2:
                return prob[:, 1]
            return prob.reshape(-1)
        pred = est.predict(x)
        return np.asarray(pred, dtype=float)

    def predict_label(self, df: pd.DataFrame, feature_columns: Iterable[str], threshold: float = 0.5) -> np.ndarray:
        p = self.predict_proba(df, feature_columns)
        return (p >= float(threshold)).astype(int)

    def get_top_feature_importance(
        self,
        df: pd.DataFrame,
        feature_columns: Iterable[str],
        label_column: str = "label_class",
        top_k: int = 10,
        max_samples: int = 2000,
    ) -> pd.DataFrame:
        """Return top feature importance rows.

        1. 优先读取模型原生 ``feature_importances_``；
        2. 若模型不暴露原生重要性（如 HistGradientBoostingClassifier），
           回退到 permutation importance（基于训练样本）。
        """
        if self.estimator is None:
            raise RuntimeError("TradeFilterModel not fitted")
        feats = list(feature_columns)
        if not feats or int(top_k) <= 0:
            return pd.DataFrame(columns=["feature", "importance"])

        est = self.estimator
        model_step = est.named_steps.get("model") if hasattr(est, "named_steps") else None
        imp: np.ndarray
        if model_step is not None and hasattr(model_step, "feature_importances_"):
            imp = np.asarray(getattr(model_step, "feature_importances_"), dtype=float)
        else:
            x = df[feats].copy()
            y = pd.to_numeric(df[label_column], errors="coerce").fillna(0).astype(int).to_numpy()
            if len(x) > int(max_samples):
                rng = np.random.default_rng(self.random_state)
                idx = np.sort(rng.choice(len(x), size=int(max_samples), replace=False))
                x = x.iloc[idx].copy()
                y = y[idx]
            scoring = "roc_auc" if len(np.unique(y)) >= 2 else "accuracy"
            try:
                p = permutation_importance(
                    est,
                    x,
                    y,
                    n_repeats=3,
                    random_state=self.random_state,
                    scoring=scoring,
                    n_jobs=1,
                )
                imp = np.asarray(p.importances_mean, dtype=float)
            except Exception:
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
            raise RuntimeError("TradeFilterModel not fitted")
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
    def load(cls, path: Path) -> "TradeFilterModel":
        obj = joblib.load(path)
        out = cls(random_state=int(obj.get("random_state", 42)))
        out.estimator = obj["estimator"]
        # B7 fix: 老 joblib 没有 model_kind 字段时显式标记为 legacy_no_kind，
        # 便于运维直接从 metrics 列辨认出"加载了旧模型"。
        out.model_kind = str(obj.get("model_kind", "legacy_no_kind"))
        return out


def evaluate_trade_filter_model(
    model: TradeFilterModel,
    df: pd.DataFrame,
    feature_columns: Iterable[str],
    label_column: str = "label_class",
    threshold: float = 0.5,
) -> dict[str, float]:
    y_true = df[label_column].astype(int).to_numpy()
    y_prob = model.predict_proba(df, feature_columns)
    y_pred = (y_prob >= float(threshold)).astype(int)
    return {
        "auc": _safe_auc(y_true, y_prob),
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "precision": float(precision_score(y_true, y_pred, zero_division=0)),
        "recall": float(recall_score(y_true, y_pred, zero_division=0)),
        "f1": float(f1_score(y_true, y_pred, zero_division=0)),
    }


__all__ = [
    "TradeFilterModel",
    "evaluate_trade_filter_model",
]
