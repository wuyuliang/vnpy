"""§03-03 MA trend following."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from ._common import ema, norm_interval, sma


@dataclass
class MASignal:
    side: Literal["long", "short", "flat"]
    fast: float
    slow: float
    slope_fast: float
    slope_slow: float


def compute_ma_features(
    df: pd.DataFrame,
    n_fast: int = 20,
    n_slow: int = 60,
    slope_lookback: int = 5,
    kind: Literal["sma", "ema"] = "ema",
    interval: str = "day",
) -> pd.DataFrame:
    """Compute MA and MA slopes."""
    _ = norm_interval(interval)
    if "close" not in df.columns:
        raise KeyError("compute_ma_features 缺少 close")
    out = df.copy()
    close = out["close"].astype(float)
    if kind == "sma":
        fast = sma(close, n_fast)
        slow = sma(close, n_slow)
    else:
        fast = ema(close, n_fast)
        slow = ema(close, n_slow)
    out["ma_fast"] = fast
    out["ma_slow"] = slow
    out["ma_slope_fast"] = (fast - fast.shift(slope_lookback)) / fast.shift(slope_lookback).replace(0.0, np.nan)
    out["ma_slope_slow"] = (slow - slow.shift(slope_lookback)) / slow.shift(slope_lookback).replace(0.0, np.nan)
    return out


def ma_decision(
    df: pd.DataFrame,
    bar_idx: int,
    require_slope: bool = True,
) -> Optional[MASignal]:
    """Generate MA trend signal."""
    i = int(bar_idx)
    if i < 0 or i >= len(df):
        return None
    row = df.iloc[i]
    fast = float(row.get("ma_fast", np.nan))
    slow = float(row.get("ma_slow", np.nan))
    sf = float(row.get("ma_slope_fast", np.nan))
    ss = float(row.get("ma_slope_slow", np.nan))
    if not np.isfinite(fast) or not np.isfinite(slow):
        return None

    if fast > slow:
        if (not require_slope) or (sf > 0 and ss > 0):
            return MASignal(side="long", fast=fast, slow=slow, slope_fast=sf, slope_slow=ss)
    if fast < slow:
        if (not require_slope) or (sf < 0 and ss < 0):
            return MASignal(side="short", fast=fast, slow=slow, slope_fast=sf, slope_slow=ss)
    return MASignal(side="flat", fast=fast, slow=slow, slope_fast=sf, slope_slow=ss)

