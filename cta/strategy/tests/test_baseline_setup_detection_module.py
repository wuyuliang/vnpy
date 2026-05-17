from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.strategy.baseline_candidate_gen import (
    _build_raw_setup_candidates,
    _infer_regime_label,
    _resolve_candidate_entry,
    _simulate_candidate_execution_path,
)
from cta.strategy.skill_tight_range_backtest import build_contract_spec


class TestBaselineSetupDetectionModule(unittest.TestCase):
    def setUp(self) -> None:
        # Import inside setUp so this test fails before module split exists.
        from cta.strategy import baseline_setup_detection as impl  # type: ignore

        self.impl = impl

    def test_infer_regime_label_consistent(self) -> None:
        row = pd.Series({"trend_score": 0.5})
        self.assertEqual(self.impl._infer_regime_label(row), _infer_regime_label(row))

    def test_resolve_candidate_entry_consistent(self) -> None:
        order = {"side": "long", "order_type": "stop", "price": 101.0}
        next_bar = pd.Series({"open": 101.2, "high": 102.0, "low": 100.5})
        self.assertEqual(self.impl._resolve_candidate_entry(order, next_bar), _resolve_candidate_entry(order, next_bar))

    def test_simulate_candidate_execution_path_consistent(self) -> None:
        frame = pd.DataFrame(
            {
                "open": [100.0, 100.5, 101.0, 99.5],
                "high": [100.8, 101.2, 101.4, 100.2],
                "low": [99.8, 100.2, 99.9, 98.5],
                "close": [100.4, 100.9, 100.1, 99.0],
            }
        )
        got = self.impl._simulate_candidate_execution_path(
            frame,
            entry_i=1,
            horizon_i=3,
            side="long",
            entry_price=100.5,
            atr_v=1.0,
            stop_loss_pct=0.01,
        )
        exp = _simulate_candidate_execution_path(
            frame,
            entry_i=1,
            horizon_i=3,
            side="long",
            entry_price=100.5,
            atr_v=1.0,
            stop_loss_pct=0.01,
        )
        self.assertEqual(got["exit_i"], exp["exit_i"])
        self.assertEqual(got["stop_hit"], exp["stop_hit"])
        for key in ("exit_price", "mfe_atr", "mae_atr", "pnl_atr"):
            self.assertAlmostEqual(float(got[key]), float(exp[key]), places=10)

    def test_build_raw_setup_candidates_consistent(self) -> None:
        frame = pd.DataFrame(
            {
                "close": [100.0, 101.0],
                "high": [100.5, 101.5],
                "low": [99.5, 100.3],
                "don_upper_entry": [100.2, 100.4],
                "don_lower_entry": [99.6, 99.7],
            }
        )
        contract = build_contract_spec("RB0", "SHFE")
        got = self.impl._build_raw_setup_candidates(frame, 1, "donchian_breakout", contract, "both")
        exp = _build_raw_setup_candidates(frame, 1, "donchian_breakout", contract, "both")
        self.assertEqual(got, exp)


if __name__ == "__main__":
    unittest.main()
