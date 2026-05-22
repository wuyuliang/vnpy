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



class TestBaselineSkillSuitePart03(unittest.TestCase):
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

    def test_strategy_factory_all_signal_types(self) -> None:
        self.assertGreaterEqual(len(BASELINE_SIGNAL_TYPES), 4)
        for signal_type in BASELINE_SIGNAL_TYPES:
            strategy = create_baseline_strategy(
                signal_type=signal_type,
                frame=self.frame,
                contract=self.contract,
                trade_side_mode="both",
            )
            # run a short scan to ensure no exceptions
            for i in range(5, min(len(self.frame) - 1, 80)):
                orders = strategy.on_bar(i, self.frame.iloc[i], position=0)
                for od in orders:
                    side = str(od.get("side", "")).lower()
                    self.assertIn(side, {"long", "short", "flat"})

    def test_baseline_signal_types_do_not_publish_range_mean_reversion(self) -> None:
        self.assertNotIn("mean_reversion_range", BASELINE_SIGNAL_TYPES)

    def test_generate_candidate_uses_entry_bar_atr_for_label_norm(self) -> None:
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=5, freq="D"),
                "open": [100.0, 100.0, 106.0, 106.0, 106.0],
                "high": [101.0, 105.0, 111.0, 107.0, 106.0],
                "low": [99.0, 99.0, 106.0, 105.0, 104.0],
                "close": [100.0, 105.0, 110.0, 106.0, 105.0],
                "volume": [1000, 1000, 1000, 1000, 1000],
                "open_interest": [1000, 1000, 1000, 1000, 1000],
                "turnover": [1e6, 1e6, 1e6, 1e6, 1e6],
                "atr14": [2.0, 2.0, 10.0, 10.0, 10.0],
                "trend_score": [0.0, 0.0, 0.0, 0.0, 0.0],
                "trend_dir": [0, 0, 0, 0, 0],
                "don_upper_entry": [104.0, 104.0, 104.0, 104.0, 104.0],
                "don_lower_entry": [95.0, 95.0, 95.0, 95.0, 95.0],
                "don_upper_exit": [120.0, 120.0, 120.0, 120.0, 120.0],
                "don_lower_exit": [80.0, 80.0, 80.0, 80.0, 80.0],
            }
        )
        candidate = generate_candidate_opportunities(
            frame=frame,
            symbol="RB0",
            exchange="SHFE",
            interval="day",
            signal_type="donchian_breakout",
            horizon_bars=2,
            trade_side_mode="both",
        )
        filled = candidate.loc[candidate["candidate_status"] == "filled"].copy()
        self.assertGreaterEqual(len(filled), 1)
        # R3 之后 ATR 归一化用 entry_bar 的 atr14（更贴近实盘风险预算口径）。
        # signal i=1 -> entry_i=2, entry_bar atr=10, mfe=(111-106)=5 => 0.5
        self.assertAlmostEqual(float(filled.iloc[0]["future_mfe_atr"]), 0.5, places=6)
        # entry_bar atr14 是有效值 => atr_warmed=1
        self.assertEqual(int(filled.iloc[0]["atr_warmed"]), 1)

    def test_load_top_n_symbols_from_ranking_sorted_by_research_rank(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_baseline_rank_") as td:
            p = Path(td) / "ranking.csv"
            pd.DataFrame(
                [
                    {"symbol": "cu0 ", "exchange": " shfe", "research_rank": 3},
                    {"symbol": "rb0", "exchange": "SHFE", "research_rank": 1},
                    {"symbol": "i0", "exchange": "dce ", "research_rank": 2},
                ]
            ).to_csv(p, index=False, encoding="utf-8-sig")
            got = _load_top_n_symbols_from_ranking(p, top_n=2)
        self.assertEqual(got, [("RB0", "SHFE"), ("I0", "DCE")])

    def test_atr_warmup_marks_warmed_zero_when_atr14_nan(self) -> None:
        """T-I: when atr14 is NaN at entry bar, atr_warmed must be 0."""
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=5, freq="D"),
                "open": [100.0, 100.0, 106.0, 106.0, 106.0],
                "high": [101.0, 105.0, 111.0, 107.0, 106.0],
                "low": [99.0, 99.0, 106.0, 105.0, 104.0],
                "close": [100.0, 105.0, 110.0, 106.0, 105.0],
                "volume": [1000, 1000, 1000, 1000, 1000],
                "open_interest": [1000, 1000, 1000, 1000, 1000],
                "turnover": [1e6] * 5,
                # ATR is NaN for the first ~14 bars in real data; here mark entry/signal as NaN
                "atr14": [np.nan, np.nan, np.nan, np.nan, np.nan],
                "trend_score": [0.0] * 5,
                "trend_dir": [0] * 5,
                "don_upper_entry": [104.0] * 5,
                "don_lower_entry": [95.0] * 5,
                "don_upper_exit": [120.0] * 5,
                "don_lower_exit": [80.0] * 5,
            }
        )
        candidate = generate_candidate_opportunities(
            frame=frame,
            symbol="RB0",
            exchange="SHFE",
            interval="day",
            signal_type="donchian_breakout",
            horizon_bars=2,
            trade_side_mode="both",
        )
        # All samples should be marked as not-warmed (atr_warmed=0)
        self.assertGreaterEqual(len(candidate), 1)
        self.assertTrue((candidate["atr_warmed"].astype(int) == 0).all())


if __name__ == "__main__":
    unittest.main()
