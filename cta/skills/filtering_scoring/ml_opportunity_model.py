"""§06-05 ML opportunity model (lightweight inference utilities)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd


@dataclass
class MLGateResult:
    probability: float
    gate_pass: bool
    size_multiplier: float


def _sigmoid(x: float) -> float:
    if x >= 0:
        z = np.exp(-x)
        return float(1.0 / (1.0 + z))
    z = np.exp(x)
    return float(z / (1.0 + z))


def _load_trade_log(path: str) -> pd.DataFrame:
    p = Path(path)
    if not p.exists():
        raise FileNotFoundError(path)
    if p.suffix.lower() == ".parquet":
        try:
            return pd.read_parquet(p)
        except Exception as exc:  # pragma: no cover - fallback path
            raise RuntimeError(f"读取 parquet 失败: {path}") from exc
    return pd.read_csv(p)


def predict_ml_gate(
    features: pd.Series,
    model_path: str,
    thresholds: dict[str, float] | None = None,
) -> MLGateResult:
    """
    Predict gate probability from a lightweight model artifact.

    Supported model json:
    1. {"type":"linear","intercept":...,"weights":{"f1":0.2,...}}
    2. {"type":"constant","probability":0.58}
    """
    p = Path(model_path)
    if not p.exists():
        raise FileNotFoundError(model_path)
    raw = json.loads(p.read_text(encoding="utf-8"))
    model_type = str(raw.get("type", "linear")).lower()
    if model_type == "constant":
        probability = float(raw.get("probability", 0.5))
    else:
        intercept = float(raw.get("intercept", 0.0))
        weights: dict[str, float] = {str(k): float(v) for k, v in dict(raw.get("weights", {})).items()}
        z = intercept + sum(float(features.get(k, 0.0)) * v for k, v in weights.items())
        probability = _sigmoid(z)
    probability = float(min(1.0, max(0.0, probability)))

    th = thresholds or {"skip": 0.4, "boost": 0.6}
    skip = float(th.get("skip", 0.4))
    boost = float(th.get("boost", 0.6))
    gate_pass = probability >= skip
    if probability < skip:
        mult = 0.0
    elif probability >= boost:
        mult = 1.5
    else:
        mult = 1.0
    return MLGateResult(probability=probability, gate_pass=gate_pass, size_multiplier=mult)


def build_training_dataset(
    trade_log_parquet: str,
    feature_loader_fn: Callable[[str, str, pd.Timestamp], Any],
    target_rr: float = 2.0,
    max_bars: int = 20,
) -> pd.DataFrame:
    """
    Build supervised dataset from trade log + as-of feature loader.

    Trade log required fields:
    symbol, interval, signal_ts, entry, stop, mfe, mae
    """
    trade_log = _load_trade_log(trade_log_parquet).copy()
    req = {"symbol", "interval", "signal_ts", "entry", "stop", "mfe", "mae"}
    miss = req - set(trade_log.columns)
    if miss:
        raise KeyError(f"trade log 缺少列: {miss}")

    rows: list[dict[str, Any]] = []
    for _, row in trade_log.iterrows():
        symbol = str(row["symbol"])
        interval = str(row["interval"])
        ts = pd.Timestamp(row["signal_ts"])
        feat_raw = feature_loader_fn(symbol, interval, ts)
        if isinstance(feat_raw, pd.Series):
            feat = feat_raw.to_dict()
        elif isinstance(feat_raw, dict):
            feat = feat_raw
        else:
            feat = {}

        entry = float(row["entry"])
        stop = float(row["stop"])
        mfe = float(row["mfe"])
        mae = float(row["mae"])
        risk = abs(entry - stop)
        if risk <= 0:
            label = 0
            rr_realized = 0.0
        else:
            rr_realized = mfe / risk
            # emulate "reach target_rr before hard stop"
            label = int((mfe >= target_rr * risk) and (mae < risk))

        out = {
            "symbol": symbol,
            "interval": interval,
            "signal_ts": ts,
            "entry": entry,
            "stop": stop,
            "mfe": mfe,
            "mae": mae,
            "risk": risk,
            "rr_realized": rr_realized,
            "target_rr": float(target_rr),
            "max_bars": int(max_bars),
            "label": label,
        }
        out.update({str(k): v for k, v in feat.items()})
        rows.append(out)
    return pd.DataFrame(rows)

