"""§06-03 context scoring and final combination."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


@dataclass
class ContextScore:
    score: float
    components: dict[str, float]

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


def _infer_direction(close: pd.Series, i: int, lb: int = 20) -> str:
    st = max(0, i - lb)
    if i <= st:
        return "flat"
    slope = float(close.iloc[i] - close.iloc[st])
    if slope > 0:
        return "long"
    if slope < 0:
        return "short"
    return "flat"


def compute_context_score(
    df_ltf: pd.DataFrame,
    df_mtf: pd.DataFrame,
    df_htf: pd.DataFrame,
    bar_idx_ltf: int,
    setup_type: str,
    setup_dir: Literal["long", "short"],
    interval: str = "day",
) -> ContextScore:
    """Compute context score from MTF alignment + regime + location + session."""
    for name, df in (("ltf", df_ltf), ("mtf", df_mtf), ("htf", df_htf)):
        if "close" not in df.columns:
            raise KeyError(f"{name} 缺少 close 列")
    _ = _norm_interval(interval)
    i = int(bar_idx_ltf)
    i_ltf = min(max(i, 0), len(df_ltf) - 1)
    i_mtf = min(i_ltf, len(df_mtf) - 1)
    i_htf = min(i_ltf, len(df_htf) - 1)

    ltf_dir = setup_dir
    mtf_dir = _infer_direction(df_mtf["close"].astype(float), i_mtf)
    htf_dir = _infer_direction(df_htf["close"].astype(float), i_htf)

    s_htf = 1.0 if htf_dir == ltf_dir else 0.0
    s_mtf = 1.0 if mtf_dir == ltf_dir else 0.3

    regime = str(df_ltf.get("regime", pd.Series(["trend"] * len(df_ltf))).iloc[i_ltf]).lower()
    setup_group = "trend" if setup_type in {"tight_range", "bull_flag", "bear_flag", "bp"} else "range"
    if regime in {"transition", "normal"}:
        s_regime = 0.5
    elif setup_group == "trend" and regime in {"trend", "trend_up", "trend_down", "expansion"}:
        s_regime = 1.0
    elif setup_group == "range" and regime in {"range", "compression"}:
        s_regime = 1.0
    else:
        s_regime = 0.2

    leg_pos = str(df_ltf.get("leg_position", pd.Series(["mid"] * len(df_ltf))).iloc[i_ltf]).lower()
    pos_weight = {"start": 1.0, "mid": 0.7, "end": 0.3}
    s_pos = float(pos_weight.get(leg_pos, 0.6))

    session = str(df_ltf.get("session", pd.Series(["open"] * len(df_ltf))).iloc[i_ltf]).lower()
    time_weight = {"open": 1.0, "mid": 0.65, "close": 0.9, "night": 0.8}
    s_time = float(time_weight.get(session, 0.7))

    components = {
        "s_htf": _clamp(s_htf),
        "s_mtf": _clamp(s_mtf),
        "s_regime": _clamp(s_regime),
        "s_pos": _clamp(s_pos),
        "s_time": _clamp(s_time),
    }
    score = (
        0.30 * components["s_htf"]
        + 0.20 * components["s_mtf"]
        + 0.20 * components["s_regime"]
        + 0.20 * components["s_pos"]
        + 0.10 * components["s_time"]
    )
    return ContextScore(score=_clamp(score), components=components)


def combine_final_score(
    setup_q: float,
    breakout_q: float,
    context: float,
) -> float:
    """Combine three scores by geometric mean."""
    vals = np.array(
        [
            _clamp(float(setup_q)),
            _clamp(float(breakout_q)),
            _clamp(float(context)),
        ]
    )
    return float(np.prod(vals) ** (1.0 / len(vals)))

