"""§05-04 过渡期风控 / Transition Risk Control."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional


@dataclass
class RiskAdjustment:
    """Transition-aware risk controls for one decision point."""

    size_multiplier: float
    stop_multiplier: float
    allow_new_entry: bool
    allow_add_on: bool
    max_hold_bars: Optional[int]

    def __post_init__(self) -> None:
        if self.size_multiplier < 0:
            raise ValueError("size_multiplier 应 >= 0")
        if self.stop_multiplier <= 0:
            raise ValueError("stop_multiplier 应 > 0")
        if self.max_hold_bars is not None and self.max_hold_bars <= 0:
            raise ValueError("max_hold_bars 应 > 0 或 None")


def transition_risk_adjustment(
    regime_label: str,
    regime_age: int,
    portfolio_transition_ratio: float = 0.0,
    transition_size_multiplier: float = 0.5,
    transition_stop_multiplier: float = 0.7,
    switch_protect_bars: int = 5,
    transition_max_hold_bars: int = 10,
) -> RiskAdjustment:
    """
    生成过渡期风控参数。

    规则
    ----
    - regime='transition' 或 age < switch_protect_bars:
      降仓、收紧止损、禁新开、禁加仓、限制持仓周期。
    - 当组合 transition 占比 > 0.3 时，在既有 size_multiplier 上再乘 0.7。
    """
    if regime_age < 0:
        raise ValueError(f"regime_age 应 >= 0，got {regime_age}")
    if not (0.0 <= portfolio_transition_ratio <= 1.0):
        raise ValueError(
            "portfolio_transition_ratio 应在 [0,1] 之间，"
            f"got {portfolio_transition_ratio}"
        )

    is_transition = (regime_label == "transition") or (regime_age < switch_protect_bars)
    if is_transition:
        size = float(transition_size_multiplier)
        stop = float(transition_stop_multiplier)
        allow_new = False
        allow_add = False
        max_hold = int(transition_max_hold_bars)
    else:
        size = 1.0
        stop = 1.0
        allow_new = True
        allow_add = True
        max_hold = None

    if portfolio_transition_ratio > 0.3:
        size *= 0.7

    return RiskAdjustment(
        size_multiplier=size,
        stop_multiplier=stop,
        allow_new_entry=allow_new,
        allow_add_on=allow_add,
        max_hold_bars=max_hold,
    )


def enforce_transition_max_hold(position_bars: int, adj: RiskAdjustment) -> bool:
    """是否因超过 transition 持仓上限而应强平。"""
    if position_bars < 0:
        raise ValueError(f"position_bars 应 >= 0，got {position_bars}")
    if adj.max_hold_bars is None:
        return False
    return position_bars > adj.max_hold_bars

