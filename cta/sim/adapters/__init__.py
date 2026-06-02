"""Sim/live adapters wrapping OOT modules with per-bar/per-trade APIs.

roadmap §2.2 P1 — 已落地新特性的 sim/live 接入：
- P1-7: rotation_stepper — cross_sectional_momentum_rotation
- P1-12: entry_gate_chain (bypass) — oot_trade_filter_gate
- P1-13: position_evaluator (intrabar_stop) — pipeline_oot_evaluation

注：2026-05-29 删除 trailing_take_profit / profit_aware_horizon / ma_cross_gate /
regime_short_filter / trend_aware_trade_filter 五个功能，原 P1-8/9/10/11 已下线。
"""
from cta.sim.adapters.entry_gate_chain import EntryGateChain, GateDecision
from cta.sim.adapters.position_evaluator import (
    PositionEvaluator,
    PositionExitDecision,
)
from cta.sim.adapters.rotation_order_wire import (
    RotationOrderWireConfig,
    dispatch_rotation_intents,
    wire_rotation_main_loop,
)
from cta.sim.adapters.rotation_stepper import RotationStepper
from cta.sim.adapters.rotation_stepper import intent_to_legacy_order
from cta.sim.adapters.state_provider import FeatureBasedStateProvider, StateProvider

__all__ = [
    "EntryGateChain",
    "FeatureBasedStateProvider",
    "GateDecision",
    "PositionEvaluator",
    "PositionExitDecision",
    "RotationOrderWireConfig",
    "RotationStepper",
    "StateProvider",
    "dispatch_rotation_intents",
    "intent_to_legacy_order",
    "wire_rotation_main_loop",
]
