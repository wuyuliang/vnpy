"""§05-02 突破模式打分 / Breakout Mode Scoring."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Mapping

import numpy as np
import pandas as pd


@dataclass
class BreakoutScoreResult:
    """Single-bar breakout score output."""

    score: float
    parts: dict[str, float]
    gate_pass: bool
    size_multiplier: float

    def __post_init__(self) -> None:
        if not (0.0 <= self.score <= 1.0):
            raise ValueError(f"score 应在 [0,1]，got {self.score}")
        if self.size_multiplier < 0:
            raise ValueError(f"size_multiplier 应 >= 0，got {self.size_multiplier}")


_DEFAULT_WEIGHTS: dict[str, float] = {
    "s_tight": 0.25,
    "s_atr": 0.20,
    "s_vol": 0.15,
    "s_htf": 0.20,
    "s_struct": 0.15,
    "s_time": 0.05,
}


def _clamp(x: float, lo: float = 0.0, hi: float = 1.0) -> float:
    return float(min(max(x, lo), hi))


def _norm_interval(interval: str) -> str:
    key = (interval or "minute30").strip().lower()
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


def _atr_series(df: pd.DataFrame, window: int) -> pd.Series:
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    close = df["close"].astype(float)
    prev_close = close.shift(1)
    tr = pd.concat(
        [high - low, (high - prev_close).abs(), (low - prev_close).abs()],
        axis=1,
    ).max(axis=1)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def _in_preferred_session(dt: pd.Timestamp) -> bool:
    # 开盘与尾盘窗口（可扩展）
    hm = dt.hour * 60 + dt.minute
    return (9 * 60 <= hm <= 10 * 60) or (13 * 60 + 30 <= hm <= 14 * 60 + 30)


def _resolve_weights(weights: Mapping[str, float] | None) -> dict[str, float]:
    if weights is None:
        return dict(_DEFAULT_WEIGHTS)
    merged = dict(_DEFAULT_WEIGHTS)
    merged.update({k: float(v) for k, v in weights.items() if k in merged})
    total = sum(max(v, 0.0) for v in merged.values())
    if total <= 0:
        return dict(_DEFAULT_WEIGHTS)
    return {k: max(v, 0.0) / total for k, v in merged.items()}


def compute_breakout_score(
    df: pd.DataFrame,
    bar_idx: int,
    htf_same_direction: bool,
    interval: str = "minute30",
    weights: Mapping[str, float] | None = None,
) -> BreakoutScoreResult:
    """
    对单一 bar 计算突破质量分数。

    结果遵循 md gate：
    - score < 0.3 : skip
    - 0.3~0.5     : base size (1.0)
    - >= 0.5      : 1.5x
    """
    need = {"open", "high", "low", "close", "volume"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"compute_breakout_score 缺少列: {miss}")
    if bar_idx < 0 or bar_idx >= len(df):
        raise IndexError(f"bar_idx 越界: {bar_idx}")
    _ = _norm_interval(interval)  # 兼容不同周期名，不同周期统一使用 bar-based 逻辑。

    w = _resolve_weights(weights)
    sub = df.iloc[: bar_idx + 1].copy()

    atr_fast = _atr_series(sub, 14)
    atr_slow = _atr_series(sub, 60)
    atr_ratio = float(
        (atr_fast.iloc[-1] / atr_slow.iloc[-1]) if atr_slow.iloc[-1] and not np.isnan(atr_slow.iloc[-1]) else np.nan
    )

    bar_range = (sub["high"] - sub["low"]).astype(float)
    atr_safe = atr_fast.where(atr_fast > 0)
    small_bar = (bar_range / atr_safe < 1.0).astype(float)
    tight_count = float(small_bar.tail(10).sum())

    vol_ma = sub["volume"].astype(float).rolling(20, min_periods=5).mean()
    vol_ratio = float(sub["volume"].iloc[-1] / vol_ma.iloc[-1]) if vol_ma.iloc[-1] and not np.isnan(vol_ma.iloc[-1]) else np.nan

    if len(sub) > 20:
        prev_high = float(sub["high"].iloc[-21:-1].max())
        prev_low = float(sub["low"].iloc[-21:-1].min())
    else:
        prev_high = float(sub["high"].iloc[:-1].max()) if len(sub) > 1 else float("nan")
        prev_low = float(sub["low"].iloc[:-1].min()) if len(sub) > 1 else float("nan")
    close_now = float(sub["close"].iloc[-1])
    struct_break = int((not np.isnan(prev_high) and close_now > prev_high) or (not np.isnan(prev_low) and close_now < prev_low))

    dt_ok = False
    if "datetime" in sub.columns and pd.notna(sub["datetime"].iloc[-1]):
        dt_ok = _in_preferred_session(pd.Timestamp(sub["datetime"].iloc[-1]))

    s_tight = _clamp(tight_count / 10.0)
    s_atr = _clamp((0.8 - atr_ratio) / 0.3) if not np.isnan(atr_ratio) else 0.0
    s_vol = _clamp((0.9 - vol_ratio) / 0.3) if not np.isnan(vol_ratio) else 0.0
    s_htf = 1.0 if htf_same_direction else 0.0
    s_struct = float(struct_break)
    s_time = 1.0 if dt_ok else 0.0

    parts = {
        "s_tight": s_tight,
        "s_atr": s_atr,
        "s_vol": s_vol,
        "s_htf": s_htf,
        "s_struct": s_struct,
        "s_time": s_time,
        "tight_count": tight_count,
        "atr_ratio": float(atr_ratio) if not np.isnan(atr_ratio) else np.nan,
        "vol_ratio": float(vol_ratio) if not np.isnan(vol_ratio) else np.nan,
        "structure_break": float(struct_break),
    }

    score = (
        w["s_tight"] * s_tight
        + w["s_atr"] * s_atr
        + w["s_vol"] * s_vol
        + w["s_htf"] * s_htf
        + w["s_struct"] * s_struct
        + w["s_time"] * s_time
    )
    score = _clamp(score)
    gate = score >= 0.3
    if score >= 0.5:
        size = 1.5
    elif score >= 0.3:
        size = 1.0
    else:
        size = 0.0
    return BreakoutScoreResult(score=score, parts=parts, gate_pass=gate, size_multiplier=size)


def calibrate_weights_from_history(
    df: pd.DataFrame,
    labels: pd.Series,
    feature_cols: tuple[str, ...] = ("s_tight", "s_atr", "s_vol", "s_htf", "s_struct", "s_time"),
) -> dict[str, float]:
    """
    用历史标签做轻量权重校准（无 sklearn 依赖）。

    方法：对每个特征计算与标签的相关系数，负相关截断为 0，再归一化为权重。
    """
    if len(df) != len(labels):
        raise ValueError("df 与 labels 长度必须一致")
    y = labels.astype(float)
    valid = y.notna()
    y = y[valid]
    X = df.loc[valid].copy()
    for c in feature_cols:
        if c not in X.columns:
            X[c] = np.nan

    raw: dict[str, float] = {}
    for c in feature_cols:
        if c not in X.columns:
            raw[c] = 0.0
            continue
        xs = X[c].astype(float)
        mask = xs.notna() & y.notna()
        if mask.sum() < 5:
            raw[c] = 0.0
            continue
        corr = xs[mask].corr(y[mask])
        raw[c] = max(float(corr) if corr == corr else 0.0, 0.0)

    total = sum(raw.values())
    if total <= 0:
        base = {k: 1.0 / len(feature_cols) for k in feature_cols}
        return base
    return {k: v / total for k, v in raw.items()}
