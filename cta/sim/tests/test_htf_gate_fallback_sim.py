"""HTF fallback parity tests for sim EntryGateChain (P0Δ-3)."""
from __future__ import annotations

from dataclasses import replace
import unittest

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.block_reasons import BR_HTF_MISSING
from cta.portfolio_logic.config import IntervalGateConfig
from cta.sim.adapters.entry_gate_chain import EntryGateChain


class TestHtfGateFallbackSim(unittest.TestCase):
    def test_metal_60min_missing_htf_uses_both_fallback(self) -> None:
        cfg = OotEvaluationConfig(
            use_portfolio_logic_runtime=True,
            use_trade_filter_gate=False,
            signal_type_blacklist=(),
        )
        chain = EntryGateChain(cfg)
        decision = chain.evaluate(
            {
                "symbol": "RB0",
                "exchange": "SHFE",
                "interval": "60min",
                "side": "long",
                "signal_type": "donchian_breakout",
            },
            dt=pd.Timestamp("2026-01-02 10:00:00"),
            htf_state={},
        )
        self.assertTrue(decision.passed)
        self.assertEqual(decision.block_reason, "")

    def test_bond_60min_missing_htf_passes_with_default_per_cell_fallback(self) -> None:
        cfg = OotEvaluationConfig(
            use_portfolio_logic_runtime=True,
            use_trade_filter_gate=False,
            signal_type_blacklist=(),
        )
        chain = EntryGateChain(cfg)
        decision = chain.evaluate(
            {
                "symbol": "T0",
                "exchange": "CFFEX",
                "interval": "60min",
                "side": "long",
                "signal_type": "donchian_breakout",
            },
            dt=pd.Timestamp("2026-01-02 10:00:00"),
            htf_state={},
        )
        self.assertTrue(decision.passed)
        self.assertEqual(decision.block_reason, "")

    def test_precious_60min_missing_htf_still_blocks_by_default(self) -> None:
        cfg = OotEvaluationConfig(
            use_portfolio_logic_runtime=True,
            use_trade_filter_gate=False,
            signal_type_blacklist=(),
        )
        chain = EntryGateChain(cfg)
        decision = chain.evaluate(
            {
                "symbol": "AU0",
                "exchange": "SHFE",
                "interval": "60min",
                "side": "long",
                "signal_type": "donchian_breakout",
            },
            dt=pd.Timestamp("2026-01-02 10:00:00"),
            htf_state={},
        )
        self.assertFalse(decision.passed)
        self.assertEqual(decision.block_stage, "htf")
        self.assertEqual(decision.block_reason, BR_HTF_MISSING)

    def test_explicit_empty_fallback_map_restores_strict_bond_block(self) -> None:
        base_cfg = OotEvaluationConfig(
            use_portfolio_logic_runtime=True,
            use_trade_filter_gate=False,
            signal_type_blacklist=(),
        )
        strict_interval_gate = IntervalGateConfig(
            fallback_when_htf_missing="skip",
            fallback_when_htf_missing_by_cluster_interval={},
        )
        strict_pl = replace(base_cfg.portfolio_logic, interval_gate=strict_interval_gate)
        cfg = replace(base_cfg, portfolio_logic=strict_pl)
        chain = EntryGateChain(cfg)
        decision = chain.evaluate(
            {
                "symbol": "T0",
                "exchange": "CFFEX",
                "interval": "60min",
                "side": "long",
                "signal_type": "donchian_breakout",
            },
            dt=pd.Timestamp("2026-01-02 10:00:00"),
            htf_state={},
        )
        self.assertFalse(decision.passed)
        self.assertEqual(decision.block_stage, "htf")
        self.assertEqual(decision.block_reason, BR_HTF_MISSING)


if __name__ == "__main__":
    unittest.main()
