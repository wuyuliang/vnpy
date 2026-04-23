"""§04-02 mean reversion."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from cta.skills.price_action._common import norm_interval


@dataclass
class MRSignal:
    side: Literal["long", "short", "flat"]
    z: float
    mean: float
    sd: float


def compute_zscore(
    df: pd.DataFrame,
    ma_n: int = 20,
    sd_n: int = 50,
    interval: str = "day",
) -> pd.DataFrame:
    """Compute mean-reversion zscore."""
    _ = norm_interval(interval)
    if "close" not in df.columns:
        raise KeyError("compute_zscore 缺少 close")
    out = df.copy()
    close = out["close"].astype(float)
    mean = close.rolling(ma_n, min_periods=max(5, ma_n // 2)).mean()
    spread = close - mean
    sd = spread.rolling(sd_n, min_periods=max(8, sd_n // 3)).std(ddof=0)
    z = spread / sd.replace(0.0, np.nan)
    out["mr_mean"] = mean
    out["mr_sd"] = sd
    out["mr_z"] = z
    return out


def mr_decision(
    df: pd.DataFrame,
    bar_idx: int,
    z_threshold: float = 2.0,
    regime_gate: bool = True,
) -> Optional[MRSignal]:
    """Generate mean-reversion entry signal from zscore."""
    i = int(bar_idx)
    if i < 0 or i >= len(df):
        return None
    row = df.iloc[i]
    z = float(row.get("mr_z", np.nan))
    mean = float(row.get("mr_mean", np.nan))
    sd = float(row.get("mr_sd", np.nan))
    if not np.isfinite(z):
        return None
    if regime_gate and ("range_score" in df.columns) and float(row.get("range_score", 0.0)) < 0.5:
        return None

    if z <= -abs(float(z_threshold)):
        return MRSignal(side="long", z=z, mean=mean, sd=sd)
    if z >= abs(float(z_threshold)):
        return MRSignal(side="short", z=z, mean=mean, sd=sd)
    return MRSignal(side="flat", z=z, mean=mean, sd=sd)

