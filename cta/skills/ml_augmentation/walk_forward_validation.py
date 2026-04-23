"""§09-05 walk-forward validation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Iterator

import numpy as np
import pandas as pd


@dataclass
class WFConfig:
    mode: str = "expanding"  # 'expanding' / 'rolling'
    train_months: int = 24
    test_months: int = 3
    purge_bars: int = 5


def walk_forward_splits(
    timestamps: pd.DatetimeIndex,
    cfg: WFConfig,
) -> Iterator[tuple[pd.Index, pd.Index]]:
    """Yield (train_idx, test_idx) positional index pairs."""
    if len(timestamps) == 0:
        return
    ts = pd.DatetimeIndex(pd.to_datetime(timestamps)).sort_values()
    start = ts.min()
    train_end = start + pd.DateOffset(months=int(cfg.train_months))
    final_ts = ts.max()
    fold = 0

    while True:
        test_end = train_end + pd.DateOffset(months=int(cfg.test_months))
        if test_end > final_ts:
            break

        if cfg.mode == "rolling":
            train_start = train_end - pd.DateOffset(months=int(cfg.train_months))
            train_mask = (ts >= train_start) & (ts < train_end)
        else:
            train_mask = ts < train_end
        test_mask = (ts >= train_end) & (ts < test_end)

        train_idx = np.where(train_mask)[0]
        test_idx = np.where(test_mask)[0]
        if len(train_idx) > 0 and len(test_idx) > int(cfg.purge_bars):
            test_idx = test_idx[int(cfg.purge_bars) :]
            if len(test_idx) > 0:
                yield pd.Index(train_idx), pd.Index(test_idx)
                fold += 1

        train_end = train_end + pd.DateOffset(months=int(cfg.test_months))
        if fold > 1000:
            break


def _predict_prob(model, X_test: pd.DataFrame) -> np.ndarray:
    if hasattr(model, "predict_proba"):
        p = model.predict_proba(X_test)
        p = np.asarray(p)
        if p.ndim == 2 and p.shape[1] >= 2:
            return p[:, 1]
        return p.reshape(-1)
    if hasattr(model, "predict"):
        pred = np.asarray(model.predict(X_test)).reshape(-1)
        return pred.astype(float)
    if callable(model):
        pred = np.asarray(model(X_test)).reshape(-1)
        return pred.astype(float)
    raise TypeError("model 不支持 predict_proba/predict/callable")


def run_walk_forward(
    X: pd.DataFrame,
    y: pd.Series,
    model_factory,
    cfg: WFConfig,
) -> pd.DataFrame:
    """Run fold-by-fold fit/predict and return OOS prediction table."""
    if not isinstance(X.index, pd.DatetimeIndex):
        raise TypeError("X.index 需要 DatetimeIndex")
    if len(X) != len(y):
        raise ValueError("X 与 y 长度不一致")

    rows: list[dict] = []
    for fold, (tr_idx, te_idx) in enumerate(walk_forward_splits(X.index, cfg)):
        X_tr = X.iloc[tr_idx]
        y_tr = y.iloc[tr_idx]
        X_te = X.iloc[te_idx]
        y_te = y.iloc[te_idx]

        model = model_factory() if callable(model_factory) else model_factory
        if hasattr(model, "fit"):
            model.fit(X_tr, y_tr)
        prob = _predict_prob(model, X_te)
        for i in range(len(X_te)):
            p = float(np.clip(prob[i], 0.0, 1.0))
            rows.append(
                {
                    "fold": fold,
                    "ts": X_te.index[i],
                    "y_true": int(y_te.iloc[i]),
                    "pred_prob": p,
                    "pred_label": int(p >= 0.5),
                }
            )
    return pd.DataFrame(rows)

