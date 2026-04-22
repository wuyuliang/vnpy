"""中周期(60m / 30m)setup 识别。

利用 pa_h123(H1/H2/H3/L1/L2/L3 阶段码,1-3 为多方 setup)、pa_pullback_depth_20、
pa_two_leg_pullback 识别"已经回调出 setup、等待入场"的中周期结构。
"""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd

from cta.strategy.brooks.config.params import MtfSignalCfg

SetupType = Literal["h1", "h2", "h3", "l1", "l2", "l3", "none"]


@dataclass
class MtfSetup:
    direction: int                 # 1 bull, -1 bear, 0 none
    setup_type: SetupType
    pullback_depth: float
    two_leg: int                   # -1/0/1
    reason: str = ""

    @property
    def is_valid(self) -> bool:
        return self.setup_type != "none"


_H123_TO_SETUP_BULL = {1: "h1", 2: "h2", 3: "h3"}


def detect_mtf_setup(
    feat: pd.Series | None,
    htf_direction: int,
    cfg: MtfSignalCfg,
) -> MtfSetup:
    """识别中周期 setup。

    v3 限定做多单侧:pa_h123 >=1 表示 bull pullback setup(H1/H2/H3)。
    若 align_htf_direction=True 则要求 htf_direction==1。
    """
    if feat is None:
        return MtfSetup(0, "none", 0.0, 0, "missing_feature")

    h123 = int(feat.get("pa_h123", 0) or 0)
    pullback_depth = float(feat.get("pa_pullback_depth_20", 0.0))
    two_leg = int(feat.get("pa_two_leg_pullback", 0) or 0)

    if math.isnan(pullback_depth):
        pullback_depth = 0.0

    # 目前仅支持多方 setup(h123 1-3 对应 H1/H2/H3)
    if h123 not in cfg.h123_valid:
        return MtfSetup(0, "none", pullback_depth, two_leg, f"h123={h123}_not_in_valid")

    if cfg.align_htf_direction and htf_direction != 1:
        return MtfSetup(0, "none", pullback_depth, two_leg,
                        f"htf_dir={htf_direction}_not_bull")

    # 回调深度过滤
    if pullback_depth < cfg.pullback_depth_min:
        return MtfSetup(0, "none", pullback_depth, two_leg, "pullback_too_shallow")
    if pullback_depth > cfg.pullback_depth_max:
        return MtfSetup(0, "none", pullback_depth, two_leg, "pullback_too_deep")

    return MtfSetup(
        direction=1,
        setup_type=_H123_TO_SETUP_BULL.get(h123, "none"),  # type: ignore[arg-type]
        pullback_depth=pullback_depth,
        two_leg=two_leg,
        reason="ok",
    )


__all__ = ["MtfSetup", "SetupType", "detect_mtf_setup"]
