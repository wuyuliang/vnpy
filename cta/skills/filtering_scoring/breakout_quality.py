"""§06-02 breakout quality scoring."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np
import pandas as pd


@dataclass
class BreakoutQuality:
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


def score_breakout(
    df: pd.DataFrame,
    breakout_bar_idx: int,
    breakout_level: float,
    atr: float,
    weights: dict[str, float] | None = None,
    interval: str = "day",
    follow_bars: int = 0,
) -> BreakoutQuality:
    """
    Score breakout bar quality (0-1).

    Components follow chapter formula:
    s_cross/s_vol/s_body/s_follow/s_shadow.

    Parameters
    ----------
    follow_bars : int, default 0
        **仅在 post-hoc 标注/模型训练时显式设为 >0**。>0 时会读取 bar
        ``i+1..i+follow_bars`` 做 follow-through 分量，因此不能在实盘/
        live 回测中使用（会产生未来函数）。
        默认 0：live-safe；用「本 bar 收盘相对 range 的位置」作为同 bar
        proxy 代替 s_follow，不触达未来 bar。
    """
    need = {"open", "high", "low", "close", "volume"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"score_breakout 缺少列: {miss}")
    _ = _norm_interval(interval)
    i = int(breakout_bar_idx)
    if i < 0 or i >= len(df):
        raise IndexError(f"breakout_bar_idx 越界: {i}")
    atr_v = max(float(atr), 1e-9)

    row = df.iloc[i]
    o = float(row["open"])
    h = float(row["high"])
    l = float(row["low"])
    c = float(row["close"])

    cross = abs(c - float(breakout_level))
    s_cross = _clamp((cross / atr_v) / 0.5)

    vol_ma20 = float(df["volume"].astype(float).rolling(20, min_periods=1).mean().iloc[i])
    vol_ratio = float(row["volume"]) / max(vol_ma20, 1e-9)
    s_vol = _clamp((vol_ratio - 1.0) / 0.5)

    rng = max(h - l, 1e-9)
    body = abs(c - o)
    body_ratio = body / rng
    s_body = _clamp(body_ratio / 0.8)

    is_up = c >= float(breakout_level)
    fb = int(follow_bars)
    if fb > 0:
        follow_count = 0
        seen = 0
        for j in range(i + 1, i + 1 + fb):
            if j >= len(df):
                break
            seen += 1
            cj = float(df["close"].iloc[j])
            if (is_up and cj >= float(breakout_level)) or (
                (not is_up) and cj <= float(breakout_level)
            ):
                follow_count += 1
        s_follow = _clamp(follow_count / max(seen, 1))
    else:
        # live-safe 代理：本 bar close 相对 range 的位置
        # 上破时 close 越贴近 high 越强；下破反之
        rng_i = max(h - l, 1e-9)
        pos = (c - l) / rng_i  # 0=贴底, 1=贴顶
        s_follow = _clamp(pos if is_up else (1.0 - pos))

    upper = max(0.0, h - max(o, c))
    lower = max(0.0, min(o, c) - l)
    reverse_shadow = lower if is_up else upper
    reverse_shadow_ratio = reverse_shadow / rng
    s_shadow = 1.0 - _clamp(reverse_shadow_ratio / 0.5)

    components = {
        "s_cross": _clamp(s_cross),
        "s_vol": _clamp(s_vol),
        "s_body": _clamp(s_body),
        "s_follow": _clamp(s_follow),
        "s_shadow": _clamp(s_shadow),
    }
    w = weights or {
        "s_cross": 0.3,
        "s_vol": 0.2,
        "s_body": 0.2,
        "s_follow": 0.2,
        "s_shadow": 0.1,
    }
    denom = max(sum(abs(v) for v in w.values()), 1e-9)
    score = sum(components.get(k, 0.0) * v for k, v in w.items()) / denom
    return BreakoutQuality(score=_clamp(score), components=components)


def breakout_quality_gate(
    bq: BreakoutQuality,
    min_score: float = 0.3,
) -> bool:
    """True if breakout quality passes threshold."""
    return float(bq.score) >= float(min_score)

