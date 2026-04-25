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
    if i is None or i < 0 or i >= len(close):
        return "flat"
    st = max(0, i - lb)
    if i <= st:
        return "flat"
    slope = float(close.iloc[i] - close.iloc[st])
    if slope > 0:
        return "long"
    if slope < 0:
        return "short"
    return "flat"


def _asof_index(ref_ts: pd.Timestamp, df: pd.DataFrame) -> int:
    """
    Return the largest index j such that df['datetime'][j] <= ref_ts.
    Returns -1 if ref_ts < first bar (no valid asof).
    """
    if "datetime" not in df.columns:
        # 无 datetime 列，退化为「取最后一根 <= 当前 LTF 位置」的位置对齐
        return -1
    ts = pd.to_datetime(df["datetime"])
    pos = int(ts.searchsorted(ref_ts, side="right")) - 1
    if pos < 0:
        return -1
    return min(pos, len(df) - 1)


def _resolve_regime_value(df_ltf: pd.DataFrame, i_ltf: int) -> str:
    """
    Resolve regime label with backward compatibility.

    Priority:
    1) regime_label (feature pipeline common name)
    2) regime (legacy name)
    3) default 'trend'
    """
    if "regime_label" in df_ltf.columns:
        return str(df_ltf["regime_label"].iloc[i_ltf]).lower()
    if "regime" in df_ltf.columns:
        return str(df_ltf["regime"].iloc[i_ltf]).lower()
    return "trend"


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

    # 对齐：优先用 timestamp asof；若 df_*tf 无 datetime 列，退化为位置对齐（legacy）。
    ref_ts: pd.Timestamp | None = None
    if "datetime" in df_ltf.columns:
        ref_ts = pd.to_datetime(df_ltf["datetime"].iloc[i_ltf])

    if ref_ts is not None and "datetime" in df_mtf.columns:
        i_mtf = _asof_index(ref_ts, df_mtf)
    else:
        i_mtf = min(i_ltf, len(df_mtf) - 1)
    if ref_ts is not None and "datetime" in df_htf.columns:
        i_htf = _asof_index(ref_ts, df_htf)
    else:
        i_htf = min(i_ltf, len(df_htf) - 1)

    ltf_dir = setup_dir
    # 若 asof 返回 -1（LTF 时间早于 HTF 首根 bar），方向置 flat
    mtf_dir = _infer_direction(df_mtf["close"].astype(float), i_mtf) if i_mtf >= 0 else "flat"
    htf_dir = _infer_direction(df_htf["close"].astype(float), i_htf) if i_htf >= 0 else "flat"

    s_htf = 1.0 if htf_dir == ltf_dir else 0.0
    s_mtf = 1.0 if mtf_dir == ltf_dir else 0.3

    regime = _resolve_regime_value(df_ltf, i_ltf)
    setup_group = "trend" if setup_type in {"tight_range", "bull_flag", "bear_flag", "bp"} else "range"
    if regime in {"transition", "normal"}:
        s_regime = 0.5
    elif setup_group == "trend" and regime in {
        "trend", "trend_up", "trend_down", "expansion", "expansion_trending"
    }:
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
