"""Sim/live adapters wrapping OOT modules with per-bar/per-trade APIs.

roadmap §2.2 P1 — 已落地新特性的 sim/live 接入：
- P1-7: rotation_stepper — cross_sectional_momentum_rotation
- P1-8: position_evaluator (trailing_tp) — trailing_take_profit
- P1-9: position_evaluator (horizon) — profit_aware_horizon
- P1-10: entry_gate_chain (ma_cross + regime_short) — oot_gates
- P1-11: entry_gate_chain (trend_aware) — oot_trade_filter_gate
- P1-12: entry_gate_chain (bypass) — oot_trade_filter_gate
- P1-13: position_evaluator (intrabar_stop) — pipeline_oot_evaluation
"""
from cta.sim.adapters.entry_gate_chain import EntryGateChain, GateDecision
from cta.sim.adapters.position_evaluator import (
    PositionEvaluator,
    PositionExitDecision,
)
from cta.sim.adapters.rotation_stepper import RotationStepper
from cta.sim.adapters.state_provider import FeatureBasedStateProvider, StateProvider

__all__ = [
    "EntryGateChain",
    "FeatureBasedStateProvider",
    "GateDecision",
    "PositionEvaluator",
    "PositionExitDecision",
    "RotationStepper",
    "StateProvider",
]
