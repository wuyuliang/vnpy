"""Sim cost-manifest parity tests (P0Δ-1)."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.pipeline_oot_evaluation_inputs import resolve_per_row_cost_pct
from cta.sim.costs import resolve_trade_cost_pct


class TestCostManifestParity(unittest.TestCase):
    def test_resolve_trade_cost_pct_matches_oot_per_row_logic(self) -> None:
        cfg = OotEvaluationConfig()
        cells = [
            ("RB0", "day"),
            ("RB0", "60min"),
            ("IF0", "day"),
            ("T0", "day"),
            ("T0", "60min"),
        ]
        for symbol, interval in cells:
            df = pd.DataFrame([{"symbol": symbol, "interval": interval}])
            expected = float(resolve_per_row_cost_pct(df, cfg)[0])
            actual = float(resolve_trade_cost_pct(symbol=symbol, interval=interval, cfg=cfg))
            self.assertAlmostEqual(actual, expected, places=12)

    def test_bond_cost_lower_than_black_cost_for_same_interval(self) -> None:
        cfg = OotEvaluationConfig()
        bond = resolve_trade_cost_pct(symbol="T0", interval="day", cfg=cfg)
        black = resolve_trade_cost_pct(symbol="RB0", interval="day", cfg=cfg)
        self.assertLess(bond, black)


if __name__ == "__main__":
    unittest.main()

