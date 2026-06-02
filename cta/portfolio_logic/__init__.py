"""Portfolio-level trading logic exports with lazy imports."""
from __future__ import annotations

from importlib import import_module

_LAZY_ATTRS: dict[str, tuple[str, str]] = {
    "CapsConfig": ("cta.portfolio_logic.config", "CapsConfig"),
    "CalibrationStats": ("cta.portfolio_logic.score_calibrator", "CalibrationStats"),
    "EquitySnapshot": ("cta.portfolio_logic.risk_throttle", "EquitySnapshot"),
    "EquityTracker": ("cta.portfolio_logic.risk_throttle", "EquityTracker"),
    "HorizonExtendConfig": ("cta.portfolio_logic.config", "HorizonExtendConfig"),
    "HtfGate": ("cta.portfolio_logic.interval_gate", "HtfGate"),
    "IntervalGateConfig": ("cta.portfolio_logic.config", "IntervalGateConfig"),
    "IntervalTrailingParams": ("cta.portfolio_logic.config", "IntervalTrailingParams"),
    "Layer": ("cta.portfolio_logic.pyramid_manager", "Layer"),
    "OpportunityRanker": ("cta.portfolio_logic.opportunity_ranker", "OpportunityRanker"),
    "OpportunityRankerConfig": ("cta.portfolio_logic.config", "OpportunityRankerConfig"),
    "PortfolioLogicConfig": ("cta.portfolio_logic.config", "PortfolioLogicConfig"),
    "PortfolioState": ("cta.portfolio_logic.portfolio_state", "PortfolioState"),
    "PyramidConfig": ("cta.portfolio_logic.config", "PyramidConfig"),
    "PyramidManager": ("cta.portfolio_logic.pyramid_manager", "PyramidManager"),
    "PyramidPosition": ("cta.portfolio_logic.pyramid_manager", "PyramidPosition"),
    "RiskThrottle": ("cta.portfolio_logic.risk_throttle", "RiskThrottle"),
    "RiskThrottleConfig": ("cta.portfolio_logic.config", "RiskThrottleConfig"),
    "ScoreCalibrator": ("cta.portfolio_logic.score_calibrator", "ScoreCalibrator"),
    "ThrottleLevel": ("cta.portfolio_logic.config", "ThrottleLevel"),
    "TrailingExitConfig": ("cta.portfolio_logic.config", "TrailingExitConfig"),
    "TrailingExitSimulator": ("cta.portfolio_logic.trailing_exit", "TrailingExitSimulator"),
    "normalize_portfolio_interval": ("cta.portfolio_logic.config", "normalize_portfolio_interval"),
    "simulate_trailing_exit": ("cta.portfolio_logic.trailing_exit", "simulate_trailing_exit"),
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

