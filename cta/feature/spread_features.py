"""Feature helpers for spread arbitrage."""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_log_spread(price1: pd.Series, price2: pd.Series) -> pd.Series:
    """Compute log(price1 / price2) with NaN-safe coercion."""
    p1 = pd.to_numeric(pd.Series(price1), errors="coerce")
    p2 = pd.to_numeric(pd.Series(price2), errors="coerce")
    ratio = p1 / p2
    ratio = ratio.where((ratio > 0.0) & np.isfinite(ratio), np.nan)
    return np.log(ratio)


def compute_spread_zscore(
    spread: pd.Series,
    *,
    rolling_window_days: int,
) -> pd.Series:
    """Compute rolling z-score for spread series."""
    window = int(rolling_window_days)
    if window <= 0:
        raise ValueError("rolling_window_days must be > 0")
    s = pd.to_numeric(pd.Series(spread), errors="coerce")
    mean = s.rolling(window=window, min_periods=window).mean()
    std = s.rolling(window=window, min_periods=window).std(ddof=0)
    z = (s - mean) / std
    return z.where((std > 0.0) & np.isfinite(std), np.nan)


def compute_dynamic_hedge_ratio(
    price1: pd.Series,
    price2: pd.Series,
    *,
    window_days: int,
) -> pd.Series:
    """Compute rolling OLS beta in ``price1 ~ alpha + beta * price2``."""
    window = int(window_days)
    if window <= 1:
        raise ValueError("window_days must be > 1")
    p1 = pd.to_numeric(pd.Series(price1), errors="coerce")
    p2 = pd.to_numeric(pd.Series(price2), errors="coerce")
    cov = p1.rolling(window=window, min_periods=window).cov(p2, ddof=0)
    var = p2.rolling(window=window, min_periods=window).var(ddof=0)
    beta = cov / var
    return beta.where((var > 0.0) & np.isfinite(var), np.nan)


__all__ = [
    "compute_log_spread",
    "compute_spread_zscore",
    "compute_dynamic_hedge_ratio",
]

