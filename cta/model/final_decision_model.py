"""Final stacking decision model (meta learner).

P1.3 / P1.4:
- linear / elastic-net Logistic Regression with 3-trial l1_ratio grid search
- ensemble mode: equal-weight average of ElasticNet logit + HistGradientBoosting probs
- explicit feature presence check; explicit dummy kind name on degenerate labels.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Sequence

import joblib
import numpy as np
import pandas as pd
from sklearn.dummy import DummyClassifier
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.model_selection import KFold
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler


def _safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, y_prob))


def _ensure_features_present(df: pd.DataFrame, feature_columns: Sequence[str]) -> None:
    missing = [c for c in feature_columns if c not in df.columns]
    if missing:
        raise ValueError(f"FinalDecisionModel: missing feature columns: {missing}")


def _build_linear_pipeline(l1_ratio: float, random_state: int) -> Pipeline:
    clf = LogisticRegression(
        penalty="elasticnet",
        solver="saga",
        l1_ratio=float(l1_ratio),
        C=1.0,
        max_iter=1500,
        random_state=int(random_state),
    )
    return Pipeline(
        [
            ("imputer", SimpleImputer(strategy="median")),
            ("scaler", StandardScaler()),
            ("model", clf),
        ]
    )


def _build_gbt_pipeline(random_state: int) -> Pipeline:
    # HistGBT 自带 NaN 处理，不需要 imputer。给一组保守超参，深度浅、学习率低，
    # 防止 stacking 阶段的少量样本 (~thousands) 上过拟合。
    gbt = HistGradientBoostingClassifier(
        learning_rate=0.05,
        max_depth=3,
        max_iter=200,
        min_samples_leaf=30,
        l2_regularization=1.0,
        random_state=int(random_state),
    )
    return Pipeline([("model", gbt)])


def _quick_cv_auc(estimator: Pipeline, x: pd.DataFrame, y: np.ndarray, n_splits: int = 3) -> float:
    """3-fold CV AUC, time-aware via shuffled KFold for small meta-sample size."""
    if len(np.unique(y)) < 2:
        return float("nan")
    aucs: list[float] = []
    kf = KFold(n_splits=int(n_splits), shuffle=True, random_state=42)
    for tr_idx, va_idx in kf.split(x):
        try:
            estimator.fit(x.iloc[tr_idx], y[tr_idx])
            prob = estimator.predict_proba(x.iloc[va_idx])
            p1 = prob[:, 1] if prob.ndim == 2 and prob.shape[1] >= 2 else prob.reshape(-1)
            auc = _safe_auc(y[va_idx], p1)
        except Exception:
            auc = float("nan")
        if np.isfinite(auc):
            aucs.append(float(auc))
    if not aucs:
        return float("nan")
    return float(np.mean(aucs))


@dataclass
class FinalDecisionModel:
    """Meta-model over [trade_prob, regime_code, pred_mfe, pred_mae].

    model_kind values:
      - "stack_logit_elasticnet"  : single ElasticNet logistic regression (P1.3)
      - "stack_ensemble"          : equal-weight ElasticNet + HistGBT (P1.4)
      - "dummy_no_label_diversity": label all-zero/all-one, returns prior
    """

    random_state: int = 42
    estimator: Pipeline | None = None
    estimator_b: Pipeline | None = None  # for ensemble mode
    model_kind: str = "uninitialized"
    selected_l1_ratio: float = 0.5
    selected_valid_auc: float = float("nan")

    def fit(
        self,
        df: pd.DataFrame,
        feature_columns: Iterable[str],
        label_column: str = "label_class",
        sample_weight: np.ndarray | pd.Series | None = None,
        *,
        ensemble: bool = False,
        l1_ratio_grid: Sequence[float] = (0.2, 0.5, 0.8),
    ) -> "FinalDecisionModel":
        feats = list(feature_columns)
        _ensure_features_present(df, feats)
        x = df[feats].copy()
        y = pd.to_numeric(df[label_column], errors="coerce").fillna(0).astype(int).to_numpy()

        if len(np.unique(y)) < 2:
            # P1.3 review fix：dummy kind 用明确名字便于在指标表里识别
            dummy = DummyClassifier(strategy="prior")
            self.estimator = Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median")),
                    ("model", dummy),
                ]
            )
            self.estimator.fit(x, y)
            self.model_kind = "dummy_no_label_diversity"
            return self

        # P1.3 hyperparam search: 3-trial l1_ratio grid，按 3-fold CV AUC 选最优
        best_auc = -np.inf
        best_l1 = float(l1_ratio_grid[0])
        for l1 in l1_ratio_grid:
            trial = _build_linear_pipeline(l1_ratio=float(l1), random_state=self.random_state)
            auc = _quick_cv_auc(trial, x, y, n_splits=3)
            if np.isfinite(auc) and auc > best_auc:
                best_auc = float(auc)
                best_l1 = float(l1)
        self.selected_l1_ratio = float(best_l1)
        self.selected_valid_auc = float(best_auc) if np.isfinite(best_auc) else float("nan")

        # 在全量训练集上 refit 最优配置
        linear_pipe = _build_linear_pipeline(l1_ratio=best_l1, random_state=self.random_state)
        if sample_weight is None:
            linear_pipe.fit(x, y)
        else:
            sw = np.asarray(sample_weight, dtype=float).reshape(-1)
            if sw.shape[0] != len(df):
                raise ValueError(f"sample_weight length mismatch: {sw.shape[0]} != {len(df)}")
            linear_pipe.fit(x, y, model__sample_weight=sw)
        self.estimator = linear_pipe

        if ensemble:
            # P1.4：HistGBT 与 ElasticNet 等权平均概率，捕捉 meta-feature 间非线性交互
            gbt_pipe = _build_gbt_pipeline(random_state=self.random_state)
            if sample_weight is None:
                gbt_pipe.fit(x, y)
            else:
                gbt_pipe.fit(x, y, model__sample_weight=np.asarray(sample_weight, dtype=float))
            self.estimator_b = gbt_pipe
            self.model_kind = "stack_ensemble"
        else:
            self.estimator_b = None
            self.model_kind = "stack_logit_elasticnet"
        return self

    def predict_proba(self, df: pd.DataFrame, feature_columns: Iterable[str]) -> np.ndarray:
        if self.estimator is None:
            raise RuntimeError("FinalDecisionModel not fitted")
        feats = list(feature_columns)
        _ensure_features_present(df, feats)
        x = df[feats].copy()
        proba_a = self.estimator.predict_proba(x)
        p_a = proba_a[:, 1] if proba_a.ndim == 2 and proba_a.shape[1] >= 2 else proba_a.reshape(-1)
        if self.estimator_b is None:
            return np.asarray(p_a, dtype=float)
        proba_b = self.estimator_b.predict_proba(x)
        p_b = proba_b[:, 1] if proba_b.ndim == 2 and proba_b.shape[1] >= 2 else proba_b.reshape(-1)
        return np.asarray(0.5 * (p_a + p_b), dtype=float)

    def save(self, path: Path) -> None:
        if self.estimator is None:
            raise RuntimeError("FinalDecisionModel not fitted")
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "random_state": self.random_state,
                "model_kind": self.model_kind,
                "estimator": self.estimator,
                "estimator_b": self.estimator_b,
                "selected_l1_ratio": self.selected_l1_ratio,
                "selected_valid_auc": self.selected_valid_auc,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path) -> "FinalDecisionModel":
        obj = joblib.load(path)
        out = cls(random_state=int(obj.get("random_state", 42)))
        out.estimator = obj["estimator"]
        out.estimator_b = obj.get("estimator_b")
        out.model_kind = str(obj.get("model_kind", "legacy_no_kind"))
        out.selected_l1_ratio = float(obj.get("selected_l1_ratio", 0.5))
        out.selected_valid_auc = float(obj.get("selected_valid_auc", float("nan")))
        return out


def evaluate_final_decision_model(
    model: FinalDecisionModel,
    df: pd.DataFrame,
    feature_columns: Iterable[str],
    label_column: str = "label_class",
    threshold: float = 0.5,
) -> dict[str, float]:
    y_true = pd.to_numeric(df[label_column], errors="coerce").fillna(0).astype(int).to_numpy()
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
    "FinalDecisionModel",
    "evaluate_final_decision_model",
]
