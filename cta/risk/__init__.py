"""CTA risk management system exports.

This package keeps import side effects minimal so submodules can be imported
independently in tests and tools.
"""
from __future__ import annotations

from importlib import import_module

from cta.risk.base import AdjustedDecision, PositionScaler, SignalContext, ThresholdAdjuster
from cta.risk.config import RiskSystemConfig
from cta.risk.orchestrator import RiskOrchestrator

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    # guards + guard configs
    "ConsecutiveLossGuard": ("cta.risk.guards", "ConsecutiveLossGuard"),
    "ConsecutiveLossGuardConfig": ("cta.risk.guards", "ConsecutiveLossGuardConfig"),
    "LimitMoveGuard": ("cta.risk.guards", "LimitMoveGuard"),
    "LimitMoveGuardConfig": ("cta.risk.guards", "LimitMoveGuardConfig"),
    "LiquidityFloorGuard": ("cta.risk.guards", "LiquidityFloorGuard"),
    "LiquidityFloorGuardConfig": ("cta.risk.guards", "LiquidityFloorGuardConfig"),
    "PredictionStaleGuard": ("cta.risk.guards", "PredictionStaleGuard"),
    "PredictionStaleGuardConfig": ("cta.risk.guards", "PredictionStaleGuardConfig"),
    "ProfitGiveBackGuard": ("cta.risk.guards", "ProfitGiveBackGuard"),
    "ProfitGiveBackConfig": ("cta.risk.sizing.config", "ProfitGiveBackConfig"),
    "RolloverFreezeGuard": ("cta.risk.guards", "RolloverFreezeGuard"),
    "RolloverFreezeGuardConfig": ("cta.risk.guards", "RolloverFreezeGuardConfig"),
    "ScoreDistributionDriftGuard": ("cta.risk.guards", "ScoreDistributionDriftGuard"),
    "ScoreDistributionDriftConfig": ("cta.risk.guards", "ScoreDistributionDriftConfig"),
    "SignalConcentrationGuard": ("cta.risk.guards", "SignalConcentrationGuard"),
    "SignalConcentrationGuardConfig": ("cta.risk.guards", "SignalConcentrationGuardConfig"),
    # monitors
    "DriftAssessment": ("cta.risk.monitors", "DriftAssessment"),
    "ScoreDistributionDriftMonitor": ("cta.risk.monitors", "ScoreDistributionDriftMonitor"),
    # sizing
    "DailyVaRBudgetConfig": ("cta.risk.sizing", "DailyVaRBudgetConfig"),
    "DailyVaRBudgetSizer": ("cta.risk.sizing", "DailyVaRBudgetSizer"),
    "ExecutionQualityConfig": ("cta.risk.sizing", "ExecutionQualityConfig"),
    "ExecutionQualityScaler": ("cta.risk.sizing", "ExecutionQualityScaler"),
    "HolidayPositionReducer": ("cta.risk.sizing", "HolidayPositionReducer"),
    "HolidayPositionReducerConfig": ("cta.risk.sizing", "HolidayPositionReducerConfig"),
    "NightSessionCarryConfig": ("cta.risk.sizing", "NightSessionCarryConfig"),
    "NightSessionCarryRule": ("cta.risk.sizing", "NightSessionCarryRule"),
    "ProfitGiveBackSizer": ("cta.risk.sizing", "ProfitGiveBackSizer"),
    "VolatilityRegimeScaler": ("cta.risk.sizing", "VolatilityRegimeScaler"),
    "VolatilityRegimeScalerConfig": ("cta.risk.sizing", "VolatilityRegimeScalerConfig"),
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


__all__ = [
    "AdjustedDecision",
    "PositionScaler",
    "SignalContext",
    "ThresholdAdjuster",
    "RiskSystemConfig",
    "RiskOrchestrator",
    *_LAZY_ATTRS.keys(),
]

