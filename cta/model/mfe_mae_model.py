"""MFE/MAE regression model."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.metrics import roc_auc_score
from sklearn.multioutput import MultiOutputRegressor
from sklearn.pipeline import Pipeline


@dataclass
class MfeMaeModel:
    """Multi-output regressor for future MFE/MAE ATR."""

    random_state: int = 42
    min_samples: int = 10
    model_params: dict[str, Any] | None = None
    estimator: Pipeline | None = None
    model_kind: str = "uninitialized"

    def fit(
        self,
        df: pd.DataFrame,
        feature_columns: Iterable[str],
        mfe_column: str = "future_mfe_atr",
        mae_column: str = "future_mae_atr",
        sample_weight: np.ndarray | pd.Series | None = None,
    ) -> "MfeMaeModel":
        feats = list(feature_columns)
        x = df[feats].copy()
        y = df[[mfe_column, mae_column]].astype(float).to_numpy()

        if len(df) < int(self.min_samples):
            self.estimator = Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median")),
                    ("model", DummyRegressor(strategy="mean")),
                ]
            )
            if sample_weight is None:
                self.estimator.fit(x, y)
            else:
                sw = np.asarray(sample_weight, dtype=float).reshape(-1)
                if sw.shape[0] != len(df):
                    raise ValueError(f"sample_weight length mismatch: {sw.shape[0]} != {len(df)}")
                self.estimator.fit(x, y, model__sample_weight=sw)
            self.model_kind = "dummy"
            return self

        # 过拟合缓解：对目标做轻度 winsorize，降低极端尾部样本对树模型的噪声放大。
        y_clean = np.asarray(y, dtype=float)
        y_clean = np.nan_to_num(y_clean, nan=0.0, posinf=0.0, neginf=0.0)
        if len(y_clean) >= 50:
            q_low = np.nanquantile(y_clean, 0.01, axis=0)
            q_high = np.nanquantile(y_clean, 0.99, axis=0)
            y_clean = np.clip(y_clean, q_low, q_high)

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
            "min_samples_split": 80,
            "max_features": 0.35,
            "random_state": self.random_state,
            "n_jobs": -1,
        }
        if self.model_params:
            params.update(dict(self.model_params))
        base = RandomForestRegressor(**params)
        reg = MultiOutputRegressor(base)
        self.estimator = Pipeline([("pre", pre), ("model", reg)])
        if sample_weight is None:
            self.estimator.fit(x, y_clean)
        else:
            sw = np.asarray(sample_weight, dtype=float).reshape(-1)
            if sw.shape[0] != len(df):
                raise ValueError(f"sample_weight length mismatch: {sw.shape[0]} != {len(df)}")
            self.estimator.fit(x, y_clean, model__sample_weight=sw)
        self.model_kind = "random_forest"
        return self

    def predict(self, df: pd.DataFrame, feature_columns: Iterable[str]) -> pd.DataFrame:
        if self.estimator is None:
            raise RuntimeError("MfeMaeModel not fitted")
        x = df[list(feature_columns)].copy()
        pred = np.asarray(self.estimator.predict(x), dtype=float)
        if pred.ndim == 1:
            pred = pred.reshape(-1, 2)
        return pd.DataFrame(
            {
                "pred_mfe_atr": pred[:, 0],
                "pred_mae_atr": pred[:, 1],
            },
            index=df.index,
        )

    def get_top_feature_importance(self, feature_columns: Iterable[str], top_k: int = 10) -> pd.DataFrame:
        """Return top feature importance rows.

        对 multi-output 模型，使用各输出子模型的重要性均值。
        """
        if self.estimator is None:
            raise RuntimeError("MfeMaeModel not fitted")
        feats = list(feature_columns)
        if not feats or int(top_k) <= 0:
            return pd.DataFrame(columns=["feature", "importance"])

        est = self.estimator
        model_step = est.named_steps.get("model") if hasattr(est, "named_steps") else None
        imp: np.ndarray
        if model_step is not None and hasattr(model_step, "estimators_"):
            parts: list[np.ndarray] = []
            for sub in getattr(model_step, "estimators_", []):
                if hasattr(sub, "feature_importances_"):
                    parts.append(np.asarray(getattr(sub, "feature_importances_"), dtype=float))
            if parts:
                imp = np.mean(np.vstack(parts), axis=0)
            else:
                imp = np.zeros(len(feats), dtype=float)
        elif model_step is not None and hasattr(model_step, "feature_importances_"):
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
            raise RuntimeError("MfeMaeModel not fitted")
        path.parent.mkdir(parents=True, exist_ok=True)
        joblib.dump(
            {
                "random_state": self.random_state,
                "min_samples": self.min_samples,
                "model_kind": self.model_kind,
                "estimator": self.estimator,
            },
            path,
        )

    @classmethod
    def load(cls, path: Path) -> "MfeMaeModel":
        obj = joblib.load(path)
        out = cls(
            random_state=int(obj.get("random_state", 42)),
            min_samples=int(obj.get("min_samples", 10)),
        )
        out.estimator = obj["estimator"]
        # B7 fix: legacy_no_kind 让 metrics 一眼就能识别旧 joblib 来源。
        out.model_kind = str(obj.get("model_kind", "legacy_no_kind"))
        return out


def evaluate_mfe_mae_model(
    model: MfeMaeModel,
    df: pd.DataFrame,
    feature_columns: Iterable[str],
    mfe_column: str = "future_mfe_atr",
    mae_column: str = "future_mae_atr",
    mae_penalty: float = 1.0,
    direction_threshold: float = 0.0,
) -> dict[str, float]:
    y_true = df[[mfe_column, mae_column]].astype(float).to_numpy()
    pred_df = model.predict(df, feature_columns)
    y_pred = pred_df[["pred_mfe_atr", "pred_mae_atr"]].to_numpy()

    mfe_mae = float(mean_absolute_error(y_true[:, 0], y_pred[:, 0]))
    mae_mae = float(mean_absolute_error(y_true[:, 1], y_pred[:, 1]))
    mfe_rmse = float(np.sqrt(mean_squared_error(y_true[:, 0], y_pred[:, 0])))
    mae_rmse = float(np.sqrt(mean_squared_error(y_true[:, 1], y_pred[:, 1])))
    mfe_r2 = float(r2_score(y_true[:, 0], y_pred[:, 0]))
    mae_r2 = float(r2_score(y_true[:, 1], y_pred[:, 1]))

    true_edge = y_true[:, 0] - float(mae_penalty) * y_true[:, 1]
    pred_edge = y_pred[:, 0] - float(mae_penalty) * y_pred[:, 1]
    true_direction = (true_edge > float(direction_threshold)).astype(int)
    if len(np.unique(true_direction)) >= 2:
        direction_auc = float(roc_auc_score(true_direction, pred_edge))
    else:
        direction_auc = float("nan")

    return {
        "direction_auc": direction_auc,
        "mfe_mae_mae": mfe_mae,
        "mae_mae": mae_mae,
        "mfe_rmse": mfe_rmse,
        "mae_rmse": mae_rmse,
        "mfe_r2": mfe_r2,
        "mae_r2": mae_r2,
    }


__all__ = [
    "MfeMaeModel",
    "evaluate_mfe_mae_model",
]
