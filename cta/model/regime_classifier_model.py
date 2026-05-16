"""Regime Classifier model."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import RandomForestClassifier
from sklearn.impute import SimpleImputer
from sklearn.metrics import accuracy_score, f1_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import label_binarize
from sklearn.metrics import roc_auc_score

logger = logging.getLogger(__name__)
_ALLOWED_REGIME_LABELS: frozenset[str] = frozenset({"trend_up", "trend_down", "range"})


@dataclass
class RegimeClassifierModel:
    """Multi-class classifier for market regime labels."""

    random_state: int = 42
    model_params: dict[str, Any] | None = None
    estimator: Pipeline | None = None
    model_kind: str = "uninitialized"

    def fit(
        self,
        df: pd.DataFrame,
        feature_columns: Iterable[str],
        label_column: str = "regime_label",
        sample_weight: np.ndarray | pd.Series | None = None,
    ) -> "RegimeClassifierModel":
        feats = list(feature_columns)
        x = df[feats].copy()
        y = df[label_column].astype(str).to_numpy()
        uniq = {str(v) for v in np.unique(y)}
        unknown = sorted(uniq - _ALLOWED_REGIME_LABELS)
        if unknown:
            logger.warning(
                "regime label set contains unexpected values: %s (expected subset of %s)",
                unknown,
                sorted(_ALLOWED_REGIME_LABELS),
            )

        if len(np.unique(y)) < 2:
            # 与 TradeFilterModel 保持一致：单类时使用 prior 策略，predict_proba 可用
            clf = DummyClassifier(strategy="prior")
            self.estimator = Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median")),
                    ("model", clf),
                ]
            )
            if sample_weight is None:
                self.estimator.fit(x, y)
            else:
                sw = np.asarray(sample_weight, dtype=float).reshape(-1)
                if sw.shape[0] != len(y):
                    raise ValueError(f"sample_weight length mismatch: {sw.shape[0]} != {len(y)}")
                self.estimator.fit(x, y, model__sample_weight=sw)
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
        params: dict[str, Any] = {
            "n_estimators": 200,
            "max_depth": 5,
            "min_samples_leaf": 20,
            "max_features": "sqrt",
            "class_weight": "balanced_subsample",
            "random_state": self.random_state,
            "n_jobs": -1,
        }
        if self.model_params:
            params.update(dict(self.model_params))
        model = RandomForestClassifier(**params)
        self.estimator = Pipeline([("pre", pre), ("model", model)])
        if sample_weight is None:
            self.estimator.fit(x, y)
        else:
            sw = np.asarray(sample_weight, dtype=float).reshape(-1)
            if sw.shape[0] != len(y):
                raise ValueError(f"sample_weight length mismatch: {sw.shape[0]} != {len(y)}")
            self.estimator.fit(x, y, model__sample_weight=sw)
        self.model_kind = "random_forest"
        return self

    def predict(self, df: pd.DataFrame, feature_columns: Iterable[str]) -> np.ndarray:
        if self.estimator is None:
            raise RuntimeError("RegimeClassifierModel not fitted")
        x = df[list(feature_columns)].copy()
        pred = self.estimator.predict(x)
        return np.asarray(pred, dtype=object)

    def predict_proba(self, df: pd.DataFrame, feature_columns: Iterable[str]) -> np.ndarray:
        if self.estimator is None:
            raise RuntimeError("RegimeClassifierModel not fitted")
        x = df[list(feature_columns)].copy()
        est = self.estimator
        if hasattr(est, "predict_proba"):
            prob = est.predict_proba(x)
            return np.asarray(prob, dtype=float)
        pred = est.predict(x)
        values = np.asarray(pred, dtype=object)
        return np.ones((len(values), 1), dtype=float)

    def classes_(self) -> np.ndarray:
        if self.estimator is None:
            raise RuntimeError("RegimeClassifierModel not fitted")
        est = self.estimator
        model_step = est.named_steps.get("model") if hasattr(est, "named_steps") else est
        classes = getattr(model_step, "classes_", None)
        if classes is None:
            return np.asarray([], dtype=object)
        return np.asarray(classes, dtype=object)

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
    y_prob = model.predict_proba(df, feature_columns)
    classes = model.classes_()

    auc = float("nan")
    unique = np.unique(y_true)
    if len(unique) >= 2:
        try:
            if y_prob.ndim == 2 and y_prob.shape[1] == 2:
                pos_label = classes[1] if classes.shape[0] >= 2 else unique[1]
                y_bin = (y_true == str(pos_label)).astype(int)
                if len(np.unique(y_bin)) >= 2:
                    auc = float(roc_auc_score(y_bin, y_prob[:, 1]))
            elif y_prob.ndim == 2 and y_prob.shape[1] > 2 and classes.shape[0] == y_prob.shape[1]:
                y_true_bin = label_binarize(y_true, classes=classes)
                if y_true_bin.shape[1] == y_prob.shape[1]:
                    auc = float(roc_auc_score(y_true_bin, y_prob, average="macro", multi_class="ovr"))
        except Exception:
            auc = float("nan")
    return {
        "auc": auc,
        "accuracy": float(accuracy_score(y_true, y_pred)),
        "macro_f1": float(f1_score(y_true, y_pred, average="macro", zero_division=0)),
        "weighted_f1": float(f1_score(y_true, y_pred, average="weighted", zero_division=0)),
    }


__all__ = [
    "RegimeClassifierModel",
    "evaluate_regime_model",
]
