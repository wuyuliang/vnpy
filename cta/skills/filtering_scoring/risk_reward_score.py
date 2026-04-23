"""§06-04 risk-reward scoring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


@dataclass
class RRAssessment:
    entry: float
    stop: float
    target: float
    rr: float
    target_source: Literal["swing", "measured_move", "atr", "blended"]


def _norm_interval(interval: str) -> str:
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


def _atr(df: pd.DataFrame, i: int) -> float:
    if "atr_14" in df.columns:
        v = float(df["atr_14"].iloc[i])
        if np.isfinite(v) and v > 0:
            return v
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return float(tr.rolling(14, min_periods=1).mean().iloc[i])


def compute_rr(
    df: pd.DataFrame,
    bar_idx: int,
    entry: float,
    stop: float,
    direction: Literal["long", "short"],
    lookback: int = 50,
    atr_k: float = 2.0,
    interval: str = "day",
) -> RRAssessment:
    """Estimate trade RR with swing/measured-move/ATR target."""
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"compute_rr 缺少列: {miss}")
    _ = _norm_interval(interval)
    i = int(bar_idx)
    if i < 0 or i >= len(df):
        raise IndexError(f"bar_idx 越界: {i}")
    risk = abs(float(entry) - float(stop))
    if risk <= 0:
        raise ValueError("entry 与 stop 不能相同")

    st = max(0, i - int(lookback) + 1)
    win = df.iloc[st : i + 1]
    h_max = float(win["high"].astype(float).max())
    l_min = float(win["low"].astype(float).min())
    atr_v = max(_atr(df, i), 1e-9)

    if direction == "long":
        swing = max(float(entry) + 1e-9, h_max)
        measured = max(float(entry) + 1e-9, float(entry) + (float(entry) - l_min))
        atr_target = float(entry) + atr_k * atr_v
        target = min(swing, measured, atr_target)
        min_target = float(entry) + 0.5 * risk
        target = max(target, min_target)
    else:
        swing = min(float(entry) - 1e-9, l_min)
        measured = min(float(entry) - 1e-9, float(entry) - (h_max - float(entry)))
        atr_target = float(entry) - atr_k * atr_v
        target = max(swing, measured, atr_target)
        max_target = float(entry) - 0.5 * risk
        target = min(target, max_target)
    rr = abs(target - float(entry)) / risk
    return RRAssessment(
        entry=float(entry),
        stop=float(stop),
        target=float(target),
        rr=float(rr),
        target_source="blended",
    )


def rr_gate(
    rr: float,
    min_rr: float = 1.5,
) -> bool:
    """Return True if rr >= threshold."""
    return float(rr) >= float(min_rr)
