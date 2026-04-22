"""高周期(day)偏向分类。

使用 pa_always_in_dir / pa_trend_strength_20 / pa_ema_slope_20 综合判定。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from cta.strategy.brooks.config.params import HtfSignalCfg

Direction = Literal[1, -1, 0]


@dataclass
class HtfBias:
    direction: Direction               # 1=bull, -1=bear, 0=sideways
    strength: float                    # |pa_trend_strength_20|
    always_in_dir: float
    ema_slope: float
    reason: str = ""


def _sign(x: float) -> Direction:
    if x > 0:
        return 1
    if x < 0:
        return -1
    return 0


def detect_htf_bias(feat: pd.Series | None, cfg: HtfSignalCfg) -> HtfBias:
    """返回高周期偏向(bull/bear/sideways)。

    判据:
    - |pa_always_in_dir| >= always_in_dir_min_abs AND
    - |pa_trend_strength_20| >= trend_strength_min_abs AND
    - 若 ema_slope_must_match: pa_ema_slope_20 与 always_in_dir 同号
    否则返回 sideways。
    """
    if feat is None:
        return HtfBias(0, 0.0, 0.0, 0.0, "missing_feature")

    always_dir = float(feat.get("pa_always_in_dir", 0.0))
    trend_s = float(feat.get("pa_trend_strength_20", 0.0))
    ema_slope = float(feat.get("pa_ema_slope_20", 0.0))

    # NaN safe
    if any(math.isnan(v) for v in (always_dir, trend_s, ema_slope)):
        return HtfBias(0, 0.0, always_dir, ema_slope, "nan_feature")

    if abs(always_dir) < cfg.always_in_dir_min_abs:
        return HtfBias(0, abs(trend_s), always_dir, ema_slope, "weak_always_in")
    if abs(trend_s) < cfg.trend_strength_min_abs:
        return HtfBias(0, abs(trend_s), always_dir, ema_slope, "weak_trend")

    dir_ = _sign(always_dir)
    if cfg.ema_slope_must_match and _sign(ema_slope) != dir_ and _sign(ema_slope) != 0:
        return HtfBias(0, abs(trend_s), always_dir, ema_slope, "ema_slope_opposes")

    return HtfBias(dir_, abs(trend_s), always_dir, ema_slope, "ok")


__all__ = ["HtfBias", "detect_htf_bias"]
