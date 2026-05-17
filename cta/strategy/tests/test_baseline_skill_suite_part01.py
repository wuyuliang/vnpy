"""Tests for baseline skill suite and training-sample builder."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pandas as pd

from cta.strategy.baseline_skill_suite import (
    BASELINE_SIGNAL_TYPES,
    BaselineSuiteRunResult,
    _load_top_n_symbols_from_ranking,
    _normalize_intervals,
    _safe_bool,
    build_training_samples_from_trade_log,
    create_baseline_strategy,
    generate_candidate_opportunities,
    prepare_master_feature_frame,
    run_baseline_suite,
)
from cta.strategy.skill_tight_range_breakout import ContractSpec


def _mk_bars(n: int = 260, seed: int = 2026) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    close = 100 + np.cumsum(rng.normal(0.03, 0.7, size=n))
    open_ = np.r_[close[0], close[:-1]]
    high = np.maximum(open_, close) + rng.uniform(0.1, 0.8, size=n)
    low = np.minimum(open_, close) - rng.uniform(0.1, 0.8, size=n)
    volume = rng.integers(200, 2000, size=n)
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2018-01-01", periods=n, freq="D"),
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "volume": volume,
            "open_interest": rng.integers(1000, 3000, size=n),
            "turnover": rng.uniform(1e6, 5e6, size=n),
        }
    )



class TestBaselineSkillSuitePart01(unittest.TestCase):
    def setUp(self) -> None:
        self.bars = _mk_bars()
        self.frame = prepare_master_feature_frame(self.bars, interval="day")
        self.contract = ContractSpec(
            symbol="RB0",
            exchange="SHFE",
            multiplier=10.0,
            tick_size=1.0,
            commission_rate=0.0001,
            slippage_ticks=1.0,
        )

    def test_prepare_master_feature_frame_columns(self) -> None:
        for col in (
            "atr14",
            "don_upper_entry",
            "don_lower_entry",
            "atr_upper",
            "atr_lower",
            "tr_valid",
            "breakout_score",
            "bp_valid",
            "bp_breakout_level",
            "is_one_way_bar",
            "is_limit_up_close",
            "is_limit_down_close",
        ):
            self.assertIn(col, self.frame.columns)

    def test_generate_candidate_opportunities(self) -> None:
        candidate = generate_candidate_opportunities(
            frame=self.frame,
            symbol="RB0",
            exchange="SHFE",
            interval="day",
            signal_type="tight_range_breakout",
            horizon_bars=20,
            trade_side_mode="both",
        )
        for col in (
            "symbol",
            "exchange",
            "interval",
            "datetime",
            "signal_datetime",
            "exit_datetime",
            "signal_type",
            "side",
            "entry_i",
            "entry_price",
            "exit_price_ref",
            "stop_price",
            "future_mfe_atr",
            "future_mae_atr",
            "label_class",
            "regime_label",
            "feature_close",
            "feature_atr14",
        ):
            self.assertIn(col, candidate.columns)
        if not candidate.empty:
            self.assertTrue(set(candidate["side"].astype(str).str.lower()).issubset({"long", "short"}))

    def test_run_baseline_suite_smoke_rb0(self) -> None:
        if not Path("cta/data/origin/minute60/RB").exists():
            self.skipTest("RB minute60 data not found")
        with tempfile.TemporaryDirectory(prefix="cta_baseline_suite_") as td:
            out = run_baseline_suite(
                symbol="RB0",
                exchange="SHFE",
                interval="60min",
                start_date="2019-01-01",
                end_date="2019-01-31",
                signal_types=("tight_range_breakout", "donchian_breakout"),
                trade_side_mode="both",
                output_root=Path(td),
            )
            self.assertTrue(out.summary_path.exists())
            self.assertTrue(out.training_samples_path.exists())

    def test_safe_bool_handles_nan_none_and_truthy_values(self) -> None:
        """B4: bool(np.nan) == True 是 Python 陷阱；_safe_bool 必须把 NaN/None 视为 False。"""
        self.assertFalse(_safe_bool(np.nan))
        self.assertFalse(_safe_bool(None))
        self.assertFalse(_safe_bool(np.float64("nan")))
        self.assertFalse(_safe_bool(False))
        self.assertFalse(_safe_bool(0))
        self.assertTrue(_safe_bool(True))
        self.assertTrue(_safe_bool(1))
        self.assertTrue(_safe_bool(1.5))


if __name__ == "__main__":
    unittest.main()
