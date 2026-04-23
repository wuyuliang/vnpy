"""§09-01 trade filter model."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd


@dataclass
class FilterConfig:
    label_rule: str = "rr"  # 'net_pnl' / 'rr'
    rr_threshold: float = 1.0
    model_type: str = "xgboost"
    walk_window: str = "24M"
    walk_step: str = "3M"


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = np.exp(-x)
        return float(1.0 / (1.0 + z))
    z = np.exp(x)
    return float(z / (1.0 + z))


def build_dataset(
    trade_log: pd.DataFrame,
    feature_panel: pd.DataFrame,
    cfg: FilterConfig,
) -> tuple[pd.DataFrame, pd.Series]:
    """
    Build ML dataset from trade logs and feature snapshots.

    Join key: signal_ts (and symbol/interval when provided in both tables).
    """
    tl = trade_log.copy()
    fp = feature_panel.copy()
    if "signal_ts" not in tl.columns or "signal_ts" not in fp.columns:
        raise KeyError("trade_log 与 feature_panel 都需要 signal_ts")
    tl["signal_ts"] = pd.to_datetime(tl["signal_ts"])
    fp["signal_ts"] = pd.to_datetime(fp["signal_ts"])

    join_cols = ["signal_ts"]
    for c in ("symbol", "interval"):
        if c in tl.columns and c in fp.columns:
            join_cols.append(c)
    merged = tl.merge(fp, on=join_cols, how="inner", suffixes=("_trade", ""))
    if merged.empty:
        return pd.DataFrame(), pd.Series(dtype=int)

    if cfg.label_rule == "net_pnl":
        if "net_pnl" not in merged.columns:
            raise KeyError("label_rule=net_pnl 需要 net_pnl 列")
        y = (merged["net_pnl"].astype(float) > 0).astype(int)
    else:
        if not {"mfe", "risk"}.issubset(set(merged.columns)):
            raise KeyError("label_rule=rr 需要 mfe/risk 列")
        rr = merged["mfe"].astype(float) / merged["risk"].replace(0.0, np.nan).astype(float)
        y = (rr >= float(cfg.rr_threshold)).fillna(False).astype(int)

    drop_cols = {
        "trade_id",
        "signal_ts",
        "net_pnl",
        "mfe",
        "risk",
        "mae",
        "entry",
        "exit",
        "gross_pnl",
    }
    X = merged.drop(columns=[c for c in drop_cols if c in merged.columns], errors="ignore")
    X = X.select_dtypes(include=[np.number]).fillna(0.0)
    return X, y


def train_gate(
    X_train: pd.DataFrame,
    y_train: pd.Series,
    cfg: FilterConfig,
) -> dict[str, Any]:
    """
    Train a lightweight linear-probability surrogate model.

    We keep this dependency-free to ensure deterministic tests.
    """
    _ = cfg
    X = X_train.select_dtypes(include=[np.number]).fillna(0.0)
    y = y_train.astype(int)
    if X.empty:
        return {"intercept": 0.0, "weights": {}}

    pos = X[y == 1]
    neg = X[y == 0]
    mu_pos = pos.mean(axis=0) if len(pos) else pd.Series(0.0, index=X.columns)
    mu_neg = neg.mean(axis=0) if len(neg) else pd.Series(0.0, index=X.columns)
    std = X.std(axis=0, ddof=0).replace(0, 1.0)
    w = ((mu_pos - mu_neg) / std).to_dict()
    w = {str(k): float(v) for k, v in w.items()}
    intercept = -sum(float(X[c].mean()) * float(w.get(c, 0.0)) for c in X.columns)
    return {"intercept": float(intercept), "weights": w}


def apply_gate(
    model: dict[str, Any],
    feat_at_signal: dict,
    threshold: float,
) -> bool:
    """Apply probability threshold gate."""
    intercept = float(model.get("intercept", 0.0))
    weights = {str(k): float(v) for k, v in dict(model.get("weights", {})).items()}
    z = intercept + sum(float(feat_at_signal.get(k, 0.0)) * v for k, v in weights.items())
    p = _sigmoid(z)
    return bool(p >= float(threshold))

