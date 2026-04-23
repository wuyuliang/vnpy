"""05 状态切换类策略 / Regime Switch Strategies."""
from __future__ import annotations

from cta.skills.regime_switch.breakout_score import (
    BreakoutScoreResult,
    calibrate_weights_from_history,
    compute_breakout_score,
)
from cta.skills.regime_switch.switch_machine import (
    RegimeLabel,
    RegimeState,
    allowed_strategies,
    compute_regime,
)
from cta.skills.regime_switch.transition_risk import (
    RiskAdjustment,
    enforce_transition_max_hold,
    transition_risk_adjustment,
)
from cta.skills.regime_switch.volatility_transition import (
    VolTransition,
    VolTransitionEvent,
    detect_vol_transition,
    rollback_if_false_switch,
)

__all__ = [
    "BreakoutScoreResult",
    "calibrate_weights_from_history",
    "compute_breakout_score",
    "RegimeLabel",
    "RegimeState",
    "allowed_strategies",
    "compute_regime",
    "RiskAdjustment",
    "enforce_transition_max_hold",
    "transition_risk_adjustment",
    "VolTransition",
    "VolTransitionEvent",
    "detect_vol_transition",
    "rollback_if_false_switch",
]

