"""§05-03 Regime 切换信号 / 状态机."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


RegimeLabel = Literal[
    "trend_up",
    "trend_down",
    "range",
    "compression",
    "expansion_trending",
    "transition",
]


@dataclass
class RegimeState:
    """One-bar regime state."""

    label: RegimeLabel
    confidence: float
    age_bars: int
    last_switch_bar: int

    def __post_init__(self) -> None:
        if self.label not in {
            "trend_up",
            "trend_down",
            "range",
            "compression",
            "expansion_trending",
            "transition",
        }:
            raise ValueError(f"label 非法: {self.label}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence 应在 [0,1]，got {self.confidence}")
        if self.age_bars < 0 or self.last_switch_bar < 0:
            raise ValueError("age_bars/last_switch_bar 应 >= 0")


def _clamp01(series: pd.Series) -> pd.Series:
    return series.clip(lower=0.0, upper=1.0)


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


def _to_series(v: pd.Series | str, df: pd.DataFrame) -> pd.Series:
    if isinstance(v, str):
        if v not in df.columns:
            raise KeyError(f"输入列不存在: {v}")
        return df[v]
    return v


def compute_regime(
    df: pd.DataFrame,
    trend_score: pd.Series | str,
    range_score: pd.Series | str,
    vol_state: pd.Series | str,
    interval: str = "day",
    min_switch_bars: int = 3,
) -> pd.DataFrame:
    """
    聚合 trend/range/vol_state 为 regime_label + confidence + age。

    输入
    ----
    - trend_score: 方向分数，推荐范围 [-1,1]
    - range_score: 震荡分数，推荐范围 [0,1]
    - vol_state: {'compression','expansion','low','high','normal',...}
    """
    if min_switch_bars <= 0:
        raise ValueError("min_switch_bars 应 > 0")
    _ = _norm_interval(interval)  # 兼容多周期命名，保持统一逻辑。

    idx = df.index
    trend = _to_series(trend_score, df).reindex(idx).astype(float)
    rscore = _to_series(range_score, df).reindex(idx).astype(float)
    vstate = _to_series(vol_state, df).reindex(idx).astype(str).str.lower()

    s_trend_up = _clamp01((trend - 0.5) / 0.5) * vstate.isin(
        {"high", "normal", "expansion", "low"}
    ).astype(float)
    s_trend_down = _clamp01((-trend - 0.5) / 0.5)
    s_range = _clamp01((rscore - 0.6) / 0.4) * (1.0 - vstate.eq("expansion").astype(float))
    s_compression = _clamp01((rscore - 0.5) / 0.5) * vstate.eq("compression").astype(float)
    s_expansion = _clamp01((trend.abs() - 0.5) / 0.5) * vstate.eq("expansion").astype(float)

    max_core = pd.concat(
        [s_trend_up, s_trend_down, s_range, s_compression, s_expansion],
        axis=1,
    ).max(axis=1)
    s_transition = (1.0 - max_core).clip(0.0, 1.0)

    score_df = pd.DataFrame(
        {
            "trend_up": s_trend_up.fillna(0.0),
            "trend_down": s_trend_down.fillna(0.0),
            "range": s_range.fillna(0.0),
            "compression": s_compression.fillna(0.0),
            "expansion_trending": s_expansion.fillna(0.0),
            "transition": s_transition.fillna(0.0),
        },
        index=idx,
    )

    top_label = score_df.idxmax(axis=1)
    sorted_vals = np.sort(score_df.to_numpy(dtype=float), axis=1)
    top = sorted_vals[:, -1]
    second = sorted_vals[:, -2]
    conf = pd.Series((top - second).clip(0.0, 1.0), index=idx)

    labels: list[str] = []
    current = str(top_label.iloc[0]) if len(top_label) else "transition"
    pending = current
    pending_cnt = 0
    for cand in top_label.tolist():
        cand_s = str(cand)
        if cand_s == current:
            pending = current
            pending_cnt = 0
        else:
            if cand_s == pending:
                pending_cnt += 1
            else:
                pending = cand_s
                pending_cnt = 1
            if pending_cnt >= min_switch_bars:
                current = cand_s
                pending = current
                pending_cnt = 0
        labels.append(current)

    label_s = pd.Series(labels, index=idx, dtype=object)
    grp = (label_s != label_s.shift(1)).cumsum()
    age = pd.Series(1, index=idx).groupby(grp).cumsum().astype(int)
    last_switch_bar = grp.groupby(grp).transform("idxmin")
    # idx may be non-int; use positional index for numeric consumers
    pos = pd.Series(np.arange(len(idx)), index=idx)
    last_switch_pos = pos.groupby(grp).transform("min").astype(int)

    # next regime = 2nd best label
    second_label_idx = score_df.apply(
        lambda row: row.sort_values(ascending=False).index[1], axis=1
    )
    second_label_prob = pd.Series(second, index=idx).clip(0.0, 1.0)

    out = pd.DataFrame(index=idx)
    out["regime_label"] = label_s
    out["regime_conf"] = conf
    out["regime_age"] = age
    out["last_switch_bar"] = last_switch_pos
    out["transition_flag"] = ((label_s == "transition") | (age < 5)).astype(int)
    out["transition_risk"] = (0.6 * (1.0 - conf) + 0.4 * out["transition_flag"]).clip(0.0, 1.0)
    out["next_regime"] = second_label_idx.astype(object)
    out["next_regime_prob"] = second_label_prob
    return out


def allowed_strategies(label: RegimeLabel) -> list[str]:
    """regime -> 可启用策略白名单。"""
    mapping: dict[RegimeLabel, list[str]] = {
        "trend_up": [
            "donchian_breakout",
            "atr_breakout",
            "ma_trend_following",
            "trend_hold_and_trailing",
        ],
        "trend_down": [
            "donchian_breakout",
            "atr_breakout",
            "ma_trend_following",
            "trend_hold_and_trailing",
        ],
        "range": [
            "range_boundary_reversal",
            "mean_reversion",
            "false_breakout_reversal",
        ],
        "compression": [
            "tight_range_breakout",
            "range_boundary_reversal",
        ],
        "expansion_trending": [
            "donchian_breakout",
            "atr_breakout",
            "breakout_pullback_continuation",
        ],
        "transition": [],
    }
    return list(mapping[label])

