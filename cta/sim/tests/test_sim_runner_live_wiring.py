"""Sim runner wiring tests for live/sim shared gate components."""
from __future__ import annotations

import tempfile
import unittest

from cta.sim.sim_runner import SimRunConfig, SimnowSetting, run_sim
from cta.sim.tests.test_sim_runner import FakeMainEngine, _DummyStrategyClass


class TestSimRunnerLiveWiring(unittest.TestCase):
    def test_attach_entry_gate_and_position_evaluator_from_setting(self) -> None:
        me = FakeMainEngine()
        gate = object()
        evaluator = object()
        state_provider = object()
        signal_ctx = {"generate_fn": lambda **_: None}

        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb",
            vt_symbol="rb888.SHFE",
            setting={
                "entry_gate_chain": gate,
                "position_evaluator": evaluator,
                "state_provider": state_provider,
                "signal_generator_context": signal_ctx,
            },
        )
        sim = SimnowSetting(userid="u", password="p")
        run_sim(cfg, sim, main_engine_factory=lambda: me)

        s = me.cta_engine.strategies["rb"]
        self.assertIs(getattr(s, "entry_gate_chain", None), gate)
        self.assertIs(getattr(s, "position_evaluator", None), evaluator)
        self.assertIs(getattr(s, "state_provider", None), state_provider)
        self.assertIs(getattr(s, "signal_generator_context", None), signal_ctx)

    def test_auto_attach_oot_trade_logger_when_recorder_dir_given(self) -> None:
        me = FakeMainEngine()
        with tempfile.TemporaryDirectory(prefix="sim_oot_logger_") as td:
            cfg = SimRunConfig(
                strategy_class=_DummyStrategyClass,
                strategy_name="rb",
                vt_symbol="rb888.SHFE",
                trade_recorder_dir=td,
                setting={"run_tag": "sim_plan3"},
            )
            sim = SimnowSetting(userid="u", password="p")
            run_sim(cfg, sim, main_engine_factory=lambda: me)
            s = me.cta_engine.strategies["rb"]
            self.assertIsNotNone(getattr(s, "oot_trade_logger", None))


if __name__ == "__main__":
    unittest.main()
