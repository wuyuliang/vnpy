"""§09-02 regime classifier helpers."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class RegimeConfig:
    horizon: int = 20
    vol_pct_range: float = 40.0
    label_scheme: str = "weak_future"
    ema_smooth: int = 5


def build_regime_labels(
    bars: pd.DataFrame,
    cfg: RegimeConfig,
) -> pd.Series:
    """
    Build weak-supervision regime labels from future return/volatility.
    """
    if "close" not in bars.columns:
        raise KeyError("build_regime_labels 需要 close 列")
    close = bars["close"].astype(float)
    h = max(int(cfg.horizon), 1)
    ret = close.pct_change().fillna(0.0)
    fut_ret = close.shift(-h) / close - 1.0
    fut_vol = ret.rolling(h, min_periods=max(5, h // 2)).std().shift(-h).abs()
    vol_cut = float(np.nanpercentile(fut_vol.dropna(), float(cfg.vol_pct_range))) if fut_vol.notna().any() else 0.0

    y = pd.Series("transition", index=bars.index, dtype=object)
    trend_mask = fut_ret.abs() > (1.5 * fut_vol)
    y.loc[trend_mask & (fut_ret > 0)] = "trend_up"
    y.loc[trend_mask & (fut_ret < 0)] = "trend_down"
    y.loc[(~trend_mask) & (fut_vol < vol_cut)] = "range"
    return y


def predict_regime(
    model,
    feature_row: pd.Series,
) -> dict[str, float]:
    """
    Predict class probabilities using a light linear-softmax model.

    model schema:
    {
      "classes": [...],
      "weights": {"f1": [w1,w2,...], "f2":[...]},
      "bias": [b1,b2,...]
    }
    """
    classes = list(model.get("classes", ["trend_up", "trend_down", "range", "transition"]))
    weights = dict(model.get("weights", {}))
    bias = np.array(model.get("bias", [0.0] * len(classes)), dtype=float)
    logits = bias.copy()

    for fname, val in feature_row.items():
        if fname not in weights:
            continue
        wv = np.array(weights[fname], dtype=float)
        if len(wv) != len(classes):
            continue
        logits += float(val) * wv

    logits = logits - np.max(logits)
    ex = np.exp(logits)
    prob = ex / np.sum(ex)
    return {str(c): float(p) for c, p in zip(classes, prob)}

