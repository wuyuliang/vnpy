from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from cta.strategy.baseline_setup_detection import _build_raw_setup_candidates
from cta.strategy import baseline_setup_detection as setup_detection
from cta.strategy.baseline_strategies import create_baseline_strategy
from cta.strategy.skill_tight_range_backtest import build_contract_spec


def _mk_frame_for_bull_signals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "open": [99.0, 100.0, 100.5],
            "high": [100.0, 103.0, 101.0],
            "low": [98.5, 99.5, 100.0],
            "close": [99.5, 102.8, 100.8],
            "don_upper_entry": [99.8, 101.0, 101.2],
            "don_lower_entry": [98.8, 98.9, 99.0],
            "tr_valid": [0, 1, 0],
            "tr_upper": [99.8, 101.5, 101.4],
            "tr_lower": [98.8, 100.2, 100.1],
            "tr_range_atr": [1.5, 0.7, 0.8],
            "tr_count": [4, 6, 5],
            "tr_direction_bias": [1, 1, 1],
            "breakout_pass": [0, 1, 1],
            "bp_valid": [0, 1, 0],
            "bp_direction": ["", "long", ""],
            "bp_breakout_level": [np.nan, 101.3, np.nan],
            "bp_pullback_low": [np.nan, 100.8, np.nan],
            "bp_bars_since_breakout": [0, 3, 0],
            "bp_confirmed": [0, 1, 0],
            "trend_dir": [0.5, 1.0, 0.9],
            "trend_score": [0.3, 0.8, 0.7],
            "breakout_body_strength": [0.2, 0.95, 0.3],
            "trend_acceleration_score": [0.1, 0.85, 0.2],
            "pullback_quality": [0.2, 0.75, 0.2],
            "volatility_contraction_pctl": [0.6, 0.15, 0.4],
        }
    )


class TestBullBaselineExtensions(unittest.TestCase):
    def test_build_raw_setup_candidates_supports_new_bull_signals(self) -> None:
        frame = _mk_frame_for_bull_signals()
        contract = build_contract_spec("RB0", "SHFE")

        for signal_type in (
            "trend_acceleration_breakout",
            "bull_pullback_continuation",
            "bull_volatility_contraction_breakout",
        ):
            got = _build_raw_setup_candidates(frame, 1, signal_type, contract, "both")
            self.assertTrue(got, msg=f"{signal_type} should emit at least one setup")
            self.assertEqual(str(got[0].get("side", "")), "long")

    def test_strategy_factory_supports_new_bull_signals(self) -> None:
        frame = _mk_frame_for_bull_signals()
        contract = build_contract_spec("RB0", "SHFE")
        for signal_type in (
            "trend_acceleration_breakout",
            "bull_pullback_continuation",
            "bull_volatility_contraction_breakout",
        ):
            strategy = create_baseline_strategy(signal_type, frame, contract, "both")
            orders = strategy.on_bar(1, frame.iloc[1], position=0)
            self.assertIsInstance(orders, list)

    def test_bull_signal_thresholds_are_config_driven(self) -> None:
        frame = _mk_frame_for_bull_signals()
        contract = build_contract_spec("RB0", "SHFE")

        with patch.object(setup_detection, "TREND_ACCELERATION_LONG_MIN_SCORE", 0.90, create=True):
            got = _build_raw_setup_candidates(frame, 1, "trend_acceleration_breakout", contract, "both")
        self.assertEqual(got, [])

        with patch.object(setup_detection, "BULL_PULLBACK_MIN_QUALITY", 0.80, create=True):
            got = _build_raw_setup_candidates(frame, 1, "bull_pullback_continuation", contract, "both")
        self.assertEqual(got, [])


if __name__ == "__main__":
    unittest.main()
