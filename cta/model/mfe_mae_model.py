"""MFE/MAE regression model."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import joblib
import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.dummy import DummyRegressor
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.metrics import mean_absolute_error, mean_squared_error, r2_score
from sklearn.multioutput import MultiOutputRegressor
from sklearn.pipeline import Pipeline


@dataclass
class MfeMaeModel:
    """Multi-output regressor for future MFE/MAE ATR."""

    random_state: int = 42
    min_samples: int = 10
    estimator: Pipeline | None = None
    model_kind: str = "uninitialized"

    def fit(
        self,
        df: pd.DataFrame,
        feature_columns: Iterable[str],
        mfe_column: str = "future_mfe_atr",
        mae_column: str = "future_mae_atr",
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
        base = RandomForestRegressor(
            n_estimators=320,
            max_depth=10,
            min_samples_leaf=4,
            random_state=self.random_state,
            n_jobs=-1,
        )
        reg = MultiOutputRegressor(base)
        self.estimator = Pipeline([("pre", pre), ("model", reg)])
        self.estimator.fit(x, y)
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

    return {
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
