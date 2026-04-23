"""Common helpers for §02 price_action modules."""
from __future__ import annotations

import numpy as np
import pandas as pd


def norm_interval(interval: str) -> str:
    """Normalize interval alias to canonical key."""
    key = (interval or "day").strip().lower()
    mapping = {
        "day": "day",
        "minute60": "minute60",
        "60min": "minute60",
        "minute30": "minute30",
        "30min": "minute30",
        "minute15": "minute15",
        "15min": "minute15",
        "minute5": "minute5",
        "5min": "minute5",
        "minute": "minute",
        "1min": "minute",
    }
    return mapping.get(key, key)


def true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def atr(high: pd.Series, low: pd.Series, close: pd.Series, n: int = 14) -> pd.Series:
    tr = true_range(high.astype(float), low.astype(float), close.astype(float))
    return tr.ewm(alpha=1.0 / n, adjust=False, min_periods=max(2, n // 2)).mean()


def rolling_slope(series: pd.Series, window: int) -> pd.Series:
    """OLS-like slope using first/last approximation."""
    s = series.astype(float)
    return (s - s.shift(window)) / max(window, 1)


def clip01(x: float) -> float:
    return float(min(1.0, max(0.0, x)))


def side_sign(side: str) -> int:
    return 1 if str(side).lower() == "long" else -1


def infer_bias(close: pd.Series, lookback: int = 20) -> pd.Series:
    diff = close.astype(float) - close.astype(float).shift(lookback)
    out = pd.Series(0, index=close.index, dtype=int)
    out[diff > 0] = 1
    out[diff < 0] = -1
    return out


def optional_value(v: float | None) -> float | None:
    if v is None:
        return None
    if not np.isfinite(v):
        return None
    return float(v)

