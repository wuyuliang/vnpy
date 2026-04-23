"""§06-01 setup quality scoring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd

SetupType = Literal[
    "tight_range",
    "bull_flag",
    "bear_flag",
    "bp",
    "failed_break",
    "hl_reversal",
]

_DEFAULT_WEIGHTS: dict[str, dict[str, float]] = {
    "tight_range": {"s_body": 0.30, "s_shadow": 0.20, "s_prior": 0.20, "s_atr": 0.15, "s_clean": 0.15},
    "bull_flag": {"s_body": 0.33, "s_shadow": 0.17, "s_prior": 0.20, "s_atr": 0.15, "s_clean": 0.15},
    "bear_flag": {"s_body": 0.33, "s_shadow": 0.17, "s_prior": 0.20, "s_atr": 0.15, "s_clean": 0.15},
    "bp": {"s_body": 0.32, "s_shadow": 0.18, "s_prior": 0.20, "s_atr": 0.15, "s_clean": 0.15},
    "failed_break": {"s_body": 0.26, "s_shadow": 0.24, "s_prior": 0.20, "s_atr": 0.15, "s_clean": 0.15},
    "hl_reversal": {"s_body": 0.30, "s_shadow": 0.20, "s_prior": 0.20, "s_atr": 0.15, "s_clean": 0.15},
}


@dataclass
class SetupQuality:
    score: float
    components: dict[str, float]
    setup_type: SetupType

    def __post_init__(self) -> None:
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score 应在 [0,1]，got {self.score}")


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


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return float(min(hi, max(lo, x)))


def _triangular(x: float, peak: float = 0.4, width: float = 0.4) -> float:
    if np.isnan(x):
        return 0.5
    d = abs(x - peak)
    return _clamp(1.0 - d / max(width, 1e-9))


def _atr_at(df: pd.DataFrame, idx: int) -> float:
    if "atr_14" in df.columns:
        v = float(df["atr_14"].iloc[idx])
        if np.isfinite(v) and v > 0:
            return v
    h = df["high"].astype(float)
    l = df["low"].astype(float)
    c = df["close"].astype(float)
    prev = c.shift(1)
    tr = pd.concat([(h - l), (h - prev).abs(), (l - prev).abs()], axis=1).max(axis=1)
    return float(tr.rolling(14, min_periods=1).mean().iloc[idx])


def score_setup(
    df: pd.DataFrame,
    bar_idx: int,
    setup_type: SetupType,
    weights: dict[str, float] | None = None,
    interval: str = "day",
) -> SetupQuality:
    """Score a setup with 0-1 scale."""
    need = {"open", "high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"score_setup 缺少列: {miss}")
    if setup_type not in _DEFAULT_WEIGHTS:
        raise ValueError(f"未知 setup_type: {setup_type}")
    _ = _norm_interval(interval)
    i = int(bar_idx)
    if i < 0 or i >= len(df):
        raise IndexError(f"bar_idx 越界: {i}")

    row = df.iloc[i]
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])
    atr = max(_atr_at(df, i), 1e-9)

    body = abs(c - o)
    upper = max(0.0, h - max(o, c))
    lower = max(0.0, min(o, c) - l)
    shadow_sum = upper + lower

    s_body = _clamp(body / atr)
    s_shadow = 1.0 - _clamp(shadow_sum / max(body, 1e-9))

    prior_tight = 0.0
    if "pa_tight_range_count" in df.columns:
        prior_tight = 1.0 if float(df["pa_tight_range_count"].iloc[i]) > 0 else 0.0
    else:
        st = max(0, i - 5)
        win = df.iloc[st : i + 1]
        avg_range = float((win["high"] - win["low"]).mean())
        prior_tight = 1.0 if avg_range <= 1.5 * atr else 0.0

    if "atr_pct" in df.columns:
        atr_pct = float(df["atr_pct"].iloc[i])
    else:
        atr_s = pd.Series([_atr_at(df, j) for j in range(len(df))], index=df.index)
        atr_pct = float(atr_s.rank(pct=True).iloc[i])
    s_atr = _triangular(atr_pct, peak=0.4, width=0.4)

    if "recent_fakes" in df.columns:
        recent_fakes = float(df["recent_fakes"].iloc[i])
    else:
        recent_fakes = 0.0
    s_clean = 1.0 - _clamp(recent_fakes / 10.0)

    components = {
        "s_body": _clamp(s_body),
        "s_shadow": _clamp(s_shadow),
        "s_prior": _clamp(prior_tight),
        "s_atr": _clamp(s_atr),
        "s_clean": _clamp(s_clean),
    }
    w = weights or _DEFAULT_WEIGHTS[setup_type]
    denom = max(sum(abs(v) for v in w.values()), 1e-9)
    score = sum(components.get(k, 0.0) * v for k, v in w.items()) / denom
    return SetupQuality(score=_clamp(score), components=components, setup_type=setup_type)


def setup_quality_gate(
    sq: SetupQuality,
    min_score: float = 0.3,
) -> bool:
    """Return True if score passes threshold."""
    return float(sq.score) >= float(min_score)

