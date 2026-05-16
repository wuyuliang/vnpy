"""Trade Filter model."""
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
from sklearn.ensemble import HistGradientBoostingClassifier
from sklearn.inspection import permutation_importance
from sklearn.impute import SimpleImputer
from sklearn.linear_model import LogisticRegression
from sklearn.metrics import accuracy_score, f1_score, precision_score, recall_score, roc_auc_score
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import StandardScaler

logger = logging.getLogger(__name__)


def _safe_auc(y_true: np.ndarray, y_prob: np.ndarray) -> float:
    if len(np.unique(y_true)) < 2:
        return float("nan")
    return float(roc_auc_score(y_true, y_prob))


@dataclass
class TradeFilterModel:
    """Binary classifier for trade opportunity filtering."""

    random_state: int = 42
    model_params: dict[str, Any] | None = None
    estimator: Pipeline | None = None
    estimator_tree: Pipeline | None = None
    estimator_linear: Pipeline | None = None
    model_kind: str = "uninitialized"

    def fit(
        self,
        df: pd.DataFrame,
        feature_columns: Iterable[str],
        label_column: str = "label_class",
        sample_weight: np.ndarray | pd.Series | None = None,
    ) -> "TradeFilterModel":
        feats = list(feature_columns)
        x = df[feats].copy()
        y = df[label_column].astype(int).to_numpy()

        if len(np.unique(y)) < 2:
            clf = DummyClassifier(strategy="prior")
            dummy = Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median")),
                    ("model", clf),
                ]
            )
            dummy.fit(x, y)
            self.estimator = dummy
            self.estimator_tree = None
            self.estimator_linear = None
            self.model_kind = "dummy"
            return self

        pre_tree = ColumnTransformer(
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
        pre_linear = ColumnTransformer(
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
        # 过拟合缓解：
        # 1) 降低树复杂度 + 提高正则；
        # 2) 启用 early_stopping；
        # 3) 使用类别平衡 sample_weight，降低极端标签分布对模型的偏置。
        n = len(y)
        pos = int((y == 1).sum())
        neg = int((y == 0).sum())
        if pos > 0 and neg > 0:
            w_pos = n / (2.0 * pos)
            w_neg = n / (2.0 * neg)
            cls_weight = np.where(y == 1, w_pos, w_neg).astype(float)
        else:
            cls_weight = np.ones(n, dtype=float)
        if sample_weight is None:
            fit_weight = cls_weight
        else:
            ext = np.asarray(sample_weight, dtype=float).reshape(-1)
            if ext.shape[0] != n:
                raise ValueError(f"sample_weight length mismatch: {ext.shape[0]} != {n}")
            ext = np.nan_to_num(ext, nan=1.0, posinf=1.0, neginf=1.0)
            fit_weight = cls_weight * ext

        tree_params: dict[str, Any] = {
            "learning_rate": 0.03,
            "max_depth": 3,
            "max_iter": 180,
            "min_samples_leaf": 20,
            "max_leaf_nodes": 31,
            "l2_regularization": 1.0,
            "validation_fraction": 0.15,
            "n_iter_no_change": 15,
            "early_stopping": True,
            "random_state": self.random_state,
        }
        linear_params: dict[str, Any] = {
            "penalty": "elasticnet",
            "solver": "saga",
            "l1_ratio": 0.5,
            "C": 1.0,
            "max_iter": 1500,
            "random_state": self.random_state,
            "class_weight": None,
        }
        ensemble_mode = "average"
        if self.model_params:
            for k, v in dict(self.model_params).items():
                key = str(k)
                if key == "ensemble_mode":
                    ensemble_mode = str(v)
                elif key.startswith("linear_"):
                    linear_params[key[len("linear_") :]] = v
                elif key.startswith("tree_"):
                    tree_params[key[len("tree_") :]] = v
                else:
                    # 向后兼容：未加前缀参数默认给树模型。
                    tree_params[key] = v

        tree_model = HistGradientBoostingClassifier(**tree_params)
        tree_est = Pipeline([("pre", pre_tree), ("model", tree_model)])
        tree_est.fit(x, y, model__sample_weight=fit_weight)
        self.estimator_tree = tree_est

        linear_est: Pipeline | None = None
        try:
            linear_model = LogisticRegression(**linear_params)
            linear_est = Pipeline([("pre", pre_linear), ("model", linear_model)])
            linear_est.fit(x, y, model__sample_weight=fit_weight)
        except Exception:
            linear_est = None
        self.estimator_linear = linear_est

        self.estimator = self.estimator_tree
        if self.estimator_tree is not None and self.estimator_linear is not None and ensemble_mode != "tree_only":
            self.model_kind = "ensemble_histgb_elasticnet_avg"
        elif self.estimator_tree is not None:
            self.model_kind = "single_histgb"
        elif self.estimator_linear is not None:
            self.model_kind = "single_elasticnet"
            self.estimator = self.estimator_linear
        else:
            raise RuntimeError("failed to fit both tree and linear trade-filter estimators")
        return self

    def predict_proba(self, df: pd.DataFrame, feature_columns: Iterable[str]) -> np.ndarray:
        if self.estimator is None and self.estimator_tree is None and self.estimator_linear is None:
            raise RuntimeError("TradeFilterModel not fitted")
        feats = list(feature_columns)
        x = df[feats].copy()
        probs: list[np.ndarray] = []
        for est in (self.estimator_tree, self.estimator_linear):
            if est is None:
                continue
            if hasattr(est, "predict_proba"):
                prob = est.predict_proba(x)
                if prob.ndim == 2 and prob.shape[1] >= 2:
                    probs.append(np.asarray(prob[:, 1], dtype=float))
                else:
                    probs.append(np.asarray(prob.reshape(-1), dtype=float))
            else:
                probs.append(np.asarray(est.predict(x), dtype=float))
        if not probs:
            est0 = self.estimator
            if est0 is None:
                return np.zeros(len(x), dtype=float)
            if hasattr(est0, "predict_proba"):
                prob = est0.predict_proba(x)
                if prob.ndim == 2 and prob.shape[1] >= 2:
                    return np.asarray(prob[:, 1], dtype=float)
                return np.asarray(prob.reshape(-1), dtype=float)
            return np.asarray(est0.predict(x), dtype=float)
        if len(probs) == 1:
            return probs[0]
        stack = np.vstack(probs)
        return np.asarray(np.mean(stack, axis=0), dtype=float)

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

        # tree importance (permutation fallback)
        tree_imp = np.zeros(len(feats), dtype=float)
        tree_est = self.estimator_tree
        if tree_est is None:
            tree_est = self.estimator
        if tree_est is not None:
            tree_model = tree_est.named_steps.get("model") if hasattr(tree_est, "named_steps") else None
            if tree_model is not None and hasattr(tree_model, "feature_importances_"):
                tree_imp = np.asarray(getattr(tree_model, "feature_importances_"), dtype=float)
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
                        tree_est,
                        x,
                        y,
                        n_repeats=3,
                        random_state=self.random_state,
                        scoring=scoring,
                        n_jobs=1,
                    )
                    tree_imp = np.asarray(p.importances_mean, dtype=float)
                except Exception:
                    tree_imp = np.zeros(len(feats), dtype=float)

        # linear importance (abs coef)
        linear_imp = np.zeros(len(feats), dtype=float)
        if self.estimator_linear is not None:
            linear_model = (
                self.estimator_linear.named_steps.get("model")
                if hasattr(self.estimator_linear, "named_steps")
                else None
            )
            if linear_model is not None and hasattr(linear_model, "coef_"):
                coef = np.asarray(getattr(linear_model, "coef_"), dtype=float)
                if coef.ndim == 2:
                    coef = np.mean(np.abs(coef), axis=0)
                else:
                    coef = np.abs(coef)
                linear_imp = np.asarray(coef, dtype=float).reshape(-1)

        if tree_imp.shape[0] != len(feats):
            tree_imp = np.resize(tree_imp, len(feats))
        if linear_imp.shape[0] != len(feats):
            linear_imp = np.resize(linear_imp, len(feats))

        def _norm(v: np.ndarray) -> np.ndarray:
            arr = np.nan_to_num(v, nan=0.0, posinf=0.0, neginf=0.0)
            s = float(np.sum(np.abs(arr)))
            if s <= 0:
                return np.zeros_like(arr)
            return np.abs(arr) / s

        # ensemble importance：树/线性等权平均（有单模型时退化为单模型）
        imp_parts: list[np.ndarray] = []
        if np.any(np.isfinite(tree_imp)):
            imp_parts.append(_norm(tree_imp))
        if np.any(np.isfinite(linear_imp)):
            imp_parts.append(_norm(linear_imp))
        if not imp_parts:
            imp = np.zeros(len(feats), dtype=float)
        elif len(imp_parts) == 1:
            imp = imp_parts[0]
        else:
            imp = np.mean(np.vstack(imp_parts), axis=0)

        out = pd.DataFrame(
            {
                "feature": feats,
                "importance": np.nan_to_num(imp, nan=0.0, posinf=0.0, neginf=0.0),
            }
        )
        out = out.sort_values(["importance", "feature"], ascending=[False, True]).head(int(top_k)).reset_index(drop=True)
        return out

    def save(self, path: Path) -> None:
        if self.estimator is None and self.estimator_tree is None and self.estimator_linear is None:
            raise RuntimeError("TradeFilterModel not fitted")
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "random_state": self.random_state,
                "model_kind": self.model_kind,
                "estimator": self.estimator,
                "estimator_tree": self.estimator_tree,
                "estimator_linear": self.estimator_linear,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path) -> "TradeFilterModel":
        obj = joblib.load(path)
        out = cls(random_state=int(obj.get("random_state", 42)))
        out.estimator = obj.get("estimator")
        if "estimator_tree" not in obj:
            logger.warning(
                "loaded legacy joblib at %s without estimator_tree; estimator_tree fallback may be inaccurate",
                path,
            )
        out.estimator_tree = obj.get("estimator_tree", out.estimator)
        out.estimator_linear = obj.get("estimator_linear")
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
