"""Common helpers for §03 trend_strategies."""
from __future__ import annotations

import pandas as pd

from cta.skills.price_action._common import atr, norm_interval

__all__ = ["atr", "norm_interval", "ema", "sma"]


def sma(s: pd.Series, n: int) -> pd.Series:
    return s.astype(float).rolling(n, min_periods=max(2, n // 2)).mean()


def ema(s: pd.Series, n: int) -> pd.Series:
    return s.astype(float).ewm(span=n, adjust=False, min_periods=max(2, n // 2)).mean()

