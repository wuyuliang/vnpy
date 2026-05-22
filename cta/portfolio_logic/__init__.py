"""Portfolio-level trading logic modules shared by model/sim/live."""
from cta.portfolio_logic.config import (
    CapsConfig,
    HorizonExtendConfig,
    IntervalGateConfig,
    IntervalTrailingParams,
    OpportunityRankerConfig,
    OscillationTaperConfig,
    PortfolioLogicConfig,
    PyramidConfig,
    RiskThrottleConfig,
    ThrottleLevel,
    TrailingExitConfig,
    normalize_portfolio_interval,
)
from cta.portfolio_logic.interval_gate import HtfGate
from cta.portfolio_logic.opportunity_ranker import OpportunityRanker
from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.portfolio_logic.pyramid_manager import Layer, PyramidManager, PyramidPosition
from cta.portfolio_logic.risk_throttle import EquitySnapshot, EquityTracker, RiskThrottle
from cta.portfolio_logic.score_calibrator import CalibrationStats, ScoreCalibrator
from cta.portfolio_logic.trailing_exit import TrailingExitSimulator, simulate_trailing_exit
from cta.portfolio_logic.oscillation_taper import OscillationUpperBandTaper, TaperDecision

__all__ = [
    "CapsConfig",
    "CalibrationStats",
    "EquitySnapshot",
    "EquityTracker",
    "HorizonExtendConfig",
    "HtfGate",
    "IntervalGateConfig",
    "IntervalTrailingParams",
    "OpportunityRanker",
    "OpportunityRankerConfig",
    "OscillationTaperConfig",
    "OscillationUpperBandTaper",
    "PortfolioLogicConfig",
    "PortfolioState",
    "Layer",
    "PyramidManager",
    "PyramidPosition",
    "PyramidConfig",
    "RiskThrottle",
    "RiskThrottleConfig",
    "ScoreCalibrator",
    "ThrottleLevel",
    "TaperDecision",
    "TrailingExitSimulator",
    "TrailingExitConfig",
    "normalize_portfolio_interval",
    "simulate_trailing_exit",
]
