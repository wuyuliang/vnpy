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



class TestBaselineSkillSuitePart05(unittest.TestCase):
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

    def test_build_training_samples_skip_unknown_side(self) -> None:
        trade_log = pd.DataFrame(
            [
                {
                    "entry_i": 30,
                    "exit_i": 35,
                    "side": "flat",
                    "lots": 1,
                    "entry_price": float(self.frame.iloc[30]["close"]),
                    "exit_price": float(self.frame.iloc[35]["close"]),
                    "gross_pnl": 1.0,
                    "cost": 0.1,
                    "net_pnl": 0.9,
                }
            ]
        )
        samples = build_training_samples_from_trade_log(
            trade_log=trade_log,
            frame=self.frame,
            symbol="RB0",
            exchange="SHFE",
            interval="day",
            signal_type="tight_range_breakout",
        )
        self.assertEqual(len(samples), 0)

    def test_prepare_master_feature_frame_drop_duplicate_datetime(self) -> None:
        bars = self.bars.copy()
        dup = bars.iloc[[10]].copy()
        bars = pd.concat([bars, dup], axis=0, ignore_index=True)
        out = prepare_master_feature_frame(bars, interval="day")
        self.assertEqual(len(out), out["datetime"].nunique())
        self.assertTrue(out["tr_valid"].notna().any())

    def test_long_stop_entry_uses_max_open_trigger_when_open_above_trigger(self) -> None:
        """T-D: long stop with next_open > trigger should fill at next_open (gap up)."""
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=4, freq="D"),
                "open": [100.0, 100.0, 110.0, 110.0],  # entry bar opens 110, gap above trigger 106
                "high": [101.0, 105.0, 112.0, 112.0],
                "low": [99.0, 99.0, 109.0, 109.0],
                "close": [100.0, 105.0, 111.0, 111.0],
                "volume": [1000, 1000, 1000, 1000],
                "open_interest": [1000, 1000, 1000, 1000],
                "turnover": [1e6, 1e6, 1e6, 1e6],
                "atr14": [2.0, 2.0, 4.0, 4.0],
                "trend_score": [0.0, 0.0, 0.0, 0.0],
                "trend_dir": [0, 0, 0, 0],
                "don_upper_entry": [104.0] * 4,
                "don_lower_entry": [95.0] * 4,
                "don_upper_exit": [120.0] * 4,
                "don_lower_exit": [80.0] * 4,
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
        filled = candidate.loc[candidate["candidate_status"] == "filled"]
        self.assertGreaterEqual(len(filled), 1)
        # signal i=1 trigger = bar.high+tick = 105+1 = 106; next_open=110 > trigger
        # entry_price should be max(110, 106) = 110, NOT trigger 106
        self.assertAlmostEqual(float(filled.iloc[0]["entry_price"]), 110.0, places=6)

    def test_build_training_samples_from_trade_log_writes_atr_warmed_flag(self) -> None:
        """D6: 旧实现的 fallback 路径只算 mfe_atr/mae_atr 但没写 atr_warmed 列，
        下游 ``_ensure_training_columns`` 会默认填 1，于是 ATR warmup 期的样本
        被错误带进训练集。修复后该路径必须显式写 atr_warmed (0/1)。
        """
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2018-01-01", periods=4, freq="D"),
                "open": [100.0, 101.0, 102.0, 103.0],
                "high": [102.0, 103.0, 104.0, 105.0],
                "low": [99.0, 100.0, 101.0, 102.0],
                "close": [101.0, 102.0, 103.0, 104.0],
                # 第 0/2 行 ATR 缺失（warmup），第 1/3 行已热身
                "atr14": [np.nan, 1.0, np.nan, 1.5],
                "volume": [100, 100, 100, 100],
            }
        )
        trade_log = pd.DataFrame(
            [
                {
                    "side": "long",
                    "entry_i": 0,  # ATR 缺 → atr_warmed=0
                    "exit_i": 1,
                    "entry_price": 101.0,
                    "exit_price": 102.0,
                    "gross_pnl": 1.0,
                    "cost": 0.0,
                    "net_pnl": 1.0,
                },
                {
                    "side": "long",
                    "entry_i": 1,  # ATR=1.0 → atr_warmed=1
                    "exit_i": 3,
                    "entry_price": 102.0,
                    "exit_price": 104.0,
                    "gross_pnl": 2.0,
                    "cost": 0.0,
                    "net_pnl": 2.0,
                },
            ]
        )
        out = build_training_samples_from_trade_log(
            trade_log=trade_log,
            frame=frame,
            symbol="RB0",
            exchange="SHFE",
            interval="day",
            signal_type="donchian_breakout",
        )
        self.assertIn("atr_warmed", out.columns)
        self.assertEqual(int(out.iloc[0]["atr_warmed"]), 0)
        self.assertEqual(int(out.iloc[1]["atr_warmed"]), 1)

    def test_generate_candidate_marks_horizon_truncation(self) -> None:
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=4, freq="D"),
                "open": [100.0, 100.0, 110.0, 110.0],
                "high": [101.0, 105.0, 112.0, 112.0],
                "low": [99.0, 99.0, 109.0, 109.0],
                "close": [100.0, 105.0, 111.0, 111.0],
                "volume": [1000, 1000, 1000, 1000],
                "open_interest": [1000, 1000, 1000, 1000],
                "turnover": [1e6, 1e6, 1e6, 1e6],
                "atr14": [2.0, 2.0, 4.0, 4.0],
                "trend_score": [0.0, 0.0, 0.0, 0.0],
                "trend_dir": [0, 0, 0, 0],
                "don_upper_entry": [104.0] * 4,
                "don_lower_entry": [95.0] * 4,
                "don_upper_exit": [120.0] * 4,
                "don_lower_exit": [80.0] * 4,
            }
        )
        candidate = generate_candidate_opportunities(
            frame=frame,
            symbol="RB0",
            exchange="SHFE",
            interval="day",
            signal_type="donchian_breakout",
            horizon_bars=20,
            trade_side_mode="both",
        )
        self.assertIn("is_horizon_truncated", candidate.columns)
        self.assertTrue((candidate["is_horizon_truncated"].astype(int) == 1).all())

    def test_generate_candidate_can_drop_horizon_truncation_rows(self) -> None:
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=4, freq="D"),
                "open": [100.0, 100.0, 110.0, 110.0],
                "high": [101.0, 105.0, 112.0, 112.0],
                "low": [99.0, 99.0, 109.0, 109.0],
                "close": [100.0, 105.0, 111.0, 111.0],
                "volume": [1000, 1000, 1000, 1000],
                "open_interest": [1000, 1000, 1000, 1000],
                "turnover": [1e6, 1e6, 1e6, 1e6],
                "atr14": [2.0, 2.0, 4.0, 4.0],
                "trend_score": [0.0, 0.0, 0.0, 0.0],
                "trend_dir": [0, 0, 0, 0],
                "don_upper_entry": [104.0] * 4,
                "don_lower_entry": [95.0] * 4,
                "don_upper_exit": [120.0] * 4,
                "don_lower_exit": [80.0] * 4,
            }
        )
        candidate = generate_candidate_opportunities(
            frame=frame,
            symbol="RB0",
            exchange="SHFE",
            interval="day",
            signal_type="donchian_breakout",
            horizon_bars=20,
            trade_side_mode="both",
            drop_horizon_truncated=True,
        )
        self.assertEqual(len(candidate), 0)

    def test_build_training_samples_marks_exit_truncation(self) -> None:
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=4, freq="D"),
                "open": [100.0, 101.0, 102.0, 103.0],
                "high": [101.0, 102.0, 103.0, 104.0],
                "low": [99.0, 100.0, 101.0, 102.0],
                "close": [100.0, 101.0, 102.0, 103.0],
                "atr14": [1.0, 1.0, 1.0, 1.0],
            }
        )
        trade_log = pd.DataFrame(
            [
                {
                    "side": "long",
                    "entry_i": 1,
                    "exit_i": 99,  # intentionally overflow
                    "entry_price": 101.0,
                    "exit_price": 103.0,
                    "gross_pnl": 2.0,
                    "cost": 0.0,
                    "net_pnl": 2.0,
                }
            ]
        )
        out = build_training_samples_from_trade_log(
            trade_log=trade_log,
            frame=frame,
            symbol="RB0",
            exchange="SHFE",
            interval="day",
            signal_type="donchian_breakout",
        )
        self.assertIn("is_exit_truncated", out.columns)
        self.assertEqual(int(out.iloc[0]["is_exit_truncated"]), 1)
        self.assertEqual(int(out.iloc[0]["exit_i"]), 3)

    def test_build_training_samples_can_drop_exit_truncated_rows(self) -> None:
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=4, freq="D"),
                "open": [100.0, 101.0, 102.0, 103.0],
                "high": [101.0, 102.0, 103.0, 104.0],
                "low": [99.0, 100.0, 101.0, 102.0],
                "close": [100.0, 101.0, 102.0, 103.0],
                "atr14": [1.0, 1.0, 1.0, 1.0],
            }
        )
        trade_log = pd.DataFrame(
            [
                {
                    "side": "long",
                    "entry_i": 1,
                    "exit_i": 99,
                    "entry_price": 101.0,
                    "exit_price": 103.0,
                    "gross_pnl": 2.0,
                    "cost": 0.0,
                    "net_pnl": 2.0,
                }
            ]
        )
        out = build_training_samples_from_trade_log(
            trade_log=trade_log,
            frame=frame,
            symbol="RB0",
            exchange="SHFE",
            interval="day",
            signal_type="donchian_breakout",
            drop_exit_truncated=True,
        )
        self.assertEqual(len(out), 0)


if __name__ == "__main__":
    unittest.main()
