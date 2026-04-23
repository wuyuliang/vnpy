"""§06 filtering_and_scoring implementations."""
from __future__ import annotations

from .breakout_quality import BreakoutQuality, breakout_quality_gate, score_breakout
from .context_score import ContextScore, combine_final_score, compute_context_score
from .ml_opportunity_model import MLGateResult, build_training_dataset, predict_ml_gate
from .risk_reward_score import RRAssessment, compute_rr, rr_gate
from .setup_quality import SetupQuality, score_setup, setup_quality_gate

__all__ = [
    "BreakoutQuality",
    "ContextScore",
    "MLGateResult",
    "RRAssessment",
    "SetupQuality",
    "breakout_quality_gate",
    "build_training_dataset",
    "combine_final_score",
    "compute_context_score",
    "compute_rr",
    "predict_ml_gate",
    "rr_gate",
    "score_breakout",
    "score_setup",
    "setup_quality_gate",
]

