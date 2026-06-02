"""Live/sim wiring tests for LegacyCtaAdapter.

覆盖点：
1. entry_gate_chain 阻断/缩量是否真正生效；
2. signal_generator_context 是否能补齐模型分数字段；
3. position_evaluator 是否能在持仓阶段触发强平。
"""
from __future__ import annotations

import unittest
from dataclasses import dataclass
from types import SimpleNamespace
from typing import Any

import pandas as pd

from cta.strategy.tests.test_cta_adapter import FakeCtaEngine, _ScriptedAdapter, _bar


@dataclass(frozen=True)
class _GateDecision:
    passed: bool
    block_reason: str
    block_stage: str
    adjusted_lots: int
    effective_threshold: float = float("nan")


class _CaptureGate:
    def __init__(self, decision: _GateDecision) -> None:
        self.decision = decision
        self.last_candidate: dict[str, Any] | None = None

    def evaluate(self, candidate: dict[str, Any], **_kwargs: Any) -> _GateDecision:
        self.last_candidate = dict(candidate)
        return self.decision


class _FixedPositionEvaluator:
    def __init__(self, *, should_exit_at_or_below: float) -> None:
        self.should_exit_at_or_below = float(should_exit_at_or_below)

    def evaluate(self, _position: dict[str, Any], bar: dict[str, Any], **_kwargs: Any) -> Any:
        close = float(bar.get("close", 0.0))
        if close <= self.should_exit_at_or_below:
            return SimpleNamespace(should_exit=True, exit_reason="hard_stop", exit_price=close, extra={})
        return SimpleNamespace(should_exit=False, exit_reason="", exit_price=float("nan"), extra={})


class TestAdapterEntryGateChain(unittest.TestCase):
    def _new(self, scripted: list[list[dict]]) -> tuple[_ScriptedAdapter, FakeCtaEngine]:
        eng = FakeCtaEngine()
        a = _ScriptedAdapter(eng, "test", "X0.SHFE", {})
        a.scripted = scripted
        a.trading = True
        a.on_init()
        return a, eng

    def test_entry_gate_chain_blocks_order(self) -> None:
        a, eng = self._new([[], [{"side": "long", "lots": 3, "order_type": "market"}]])
        a.entry_gate_chain = _CaptureGate(
            _GateDecision(
                passed=False,
                block_reason="blocked_trade_filter",
                block_stage="trade_filter",
                adjusted_lots=0,
            )
        )
        a.on_bar(_bar(0, 100.0))
        a.on_bar(_bar(1, 101.0))
        self.assertEqual(len(eng.orders), 0)

    def test_entry_gate_chain_adjusted_lots_used(self) -> None:
        a, eng = self._new([[], [{"side": "long", "lots": 5, "order_type": "market"}]])
        a.entry_gate_chain = _CaptureGate(
            _GateDecision(
                passed=True,
                block_reason="",
                block_stage="",
                adjusted_lots=2,
            )
        )
        a.on_bar(_bar(0, 100.0))
        a.on_bar(_bar(1, 101.0))
        self.assertEqual(len(eng.orders), 1)
        self.assertEqual(float(eng.orders[0]["volume"]), 2.0)

    def test_signal_generator_context_enriches_order_before_gate(self) -> None:
        a, _eng = self._new([[], [{"side": "long", "lots": 1, "order_type": "market"}]])
        gate = _CaptureGate(
            _GateDecision(passed=False, block_reason="blocked_trade_filter", block_stage="trade_filter", adjusted_lots=0)
        )
        a.entry_gate_chain = gate

        def _generate(**_kwargs: Any) -> pd.DataFrame:
            return pd.DataFrame(
                [
                    {
                        "side": "long",
                        "trade_filter_prob": 0.88,
                        "trade_filter_prob_pctl": 91.0,
                        "final_decision_score": 0.77,
                    }
                ]
            )

        a.signal_generator_context = {"generate_fn": _generate, "cfg": object()}
        a.on_bar(_bar(0, 100.0))
        a.on_bar(_bar(1, 101.0))
        self.assertIsNotNone(gate.last_candidate)
        cand = gate.last_candidate or {}
        self.assertAlmostEqual(float(cand.get("trade_filter_prob", 0.0)), 0.88, places=6)
        self.assertAlmostEqual(float(cand.get("trade_filter_prob_pctl", 0.0)), 91.0, places=6)


class TestAdapterPositionEvaluator(unittest.TestCase):
    def test_position_evaluator_forces_flat_exit(self) -> None:
        eng = FakeCtaEngine()
        a = _ScriptedAdapter(eng, "test", "X0.SHFE", {})
        a.scripted = [[], []]
        a.trading = True
        a.on_init()
        a.pos = 1.0
        a.position_evaluator = _FixedPositionEvaluator(should_exit_at_or_below=95.0)
        a._open_position_ctx = {
            "side": "long",
            "entry_price": 100.0,
            "symbol": "X0",
            "exchange": "SHFE",
            "interval": "60min",
        }

        a.on_bar(_bar(0, 100.0))
        a.on_bar(_bar(1, 94.0))

        self.assertGreaterEqual(len(eng.orders), 1)
        last = eng.orders[-1]
        self.assertEqual(str(last["direction"]).lower(), "direction.short")
        self.assertEqual(str(last["offset"]).lower(), "offset.close")


if __name__ == "__main__":
    unittest.main()

