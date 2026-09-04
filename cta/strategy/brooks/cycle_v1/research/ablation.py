"""Frozen one-module-at-a-time ablation sequence."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class AblationStage:
    stage: str
    module: str
    question: str


ABLATION_STAGES = (
    AblationStage("A0", "single_setup", "Does the raw pattern have any edge?"),
    AblationStage("A1", "large_timeframe_direction", "Does large context reduce countertrend loss?"),
    AblationStage("A2", "market_cycle", "Is edge conditional on Market Cycle?"),
    AblationStage("A3", "always_in", "Does hysteresis reduce direction churn?"),
    AblationStage("A4", "multi_timeframe_alignment", "Does alignment improve net expectancy?"),
    AblationStage("A5", "structural_stop", "Does structure improve tail risk?"),
    AblationStage("A6", "atr_risk_sizing", "Does sizing reduce instrument scale bias?"),
    AblationStage("A7", "trade_mode_management", "Does swing management retain trend profit?"),
    AblationStage("A8", "portfolio_sector_risk", "Does concentration control reduce drawdown?"),
    AblationStage("A9", "volume_adx_auxiliary", "Do auxiliary indicators add locked OOS value?"),
)


__all__ = ["ABLATION_STAGES", "AblationStage"]

