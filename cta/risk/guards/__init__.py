"""Risk guard exports with lazy imports.

Avoid eager importing live/portfolio modules at package import time.
"""
from __future__ import annotations

from importlib import import_module

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    # configs
    "ConsecutiveLossGuardConfig": ("cta.risk.guards.config", "ConsecutiveLossGuardConfig"),
    "LimitMoveGuardConfig": ("cta.risk.guards.config", "LimitMoveGuardConfig"),
    "LiquidityFloorGuardConfig": ("cta.risk.guards.config", "LiquidityFloorGuardConfig"),
    "PredictionStaleGuardConfig": ("cta.risk.guards.config", "PredictionStaleGuardConfig"),
    "ProfitGiveBackConfig": ("cta.risk.guards.config", "ProfitGiveBackConfig"),
    "RolloverFreezeGuardConfig": ("cta.risk.guards.config", "RolloverFreezeGuardConfig"),
    "ScoreDistributionDriftConfig": ("cta.risk.guards.config", "ScoreDistributionDriftConfig"),
    "SignalConcentrationGuardConfig": ("cta.risk.guards.config", "SignalConcentrationGuardConfig"),
    # guards
    "ConsecutiveLossGuard": ("cta.risk.guards.consecutive_loss_guard", "ConsecutiveLossGuard"),
    "LimitMoveGuard": ("cta.risk.guards.limit_move_guard", "LimitMoveGuard"),
    "LiquidityFloorGuard": ("cta.risk.guards.liquidity_floor_guard", "LiquidityFloorGuard"),
    "PredictionStaleGuard": ("cta.risk.guards.prediction_stale_guard", "PredictionStaleGuard"),
    "ProfitGiveBackGuard": ("cta.risk.guards.profit_give_back_guard", "ProfitGiveBackGuard"),
    "RolloverFreezeGuard": ("cta.risk.guards.rollover_freeze_guard", "RolloverFreezeGuard"),
    "ScoreDistributionDriftGuard": (
        "cta.risk.guards.score_distribution_drift_guard",
        "ScoreDistributionDriftGuard",
    ),
    "SignalConcentrationGuard": (
        "cta.risk.guards.signal_concentration_guard",
        "SignalConcentrationGuard",
    ),
}


def __getattr__(name: str):
    target = _LAZY_ATTRS.get(name)
    if target is None:
        raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
    module_name, attr_name = target
    module = import_module(module_name)
    value = getattr(module, attr_name)
    globals()[name] = value
    return value


__all__ = sorted(_LAZY_ATTRS)

