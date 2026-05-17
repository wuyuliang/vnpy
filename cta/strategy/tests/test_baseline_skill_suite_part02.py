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



class TestBaselineSkillSuitePart02(unittest.TestCase):
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

    def test_prepare_master_feature_frame_limit_move_flags(self) -> None:
        # P0.2 修正后语义：一字板 + close 相对前收变化 ≥ cluster 涨跌停 - 0.1%。
        # 默认 symbol="" 走 other cluster 的 5% limit，所以测试要造 +5% / -5% 的 close。
        bars = pd.DataFrame(
            {
                "datetime": pd.date_range("2024-01-01", periods=3, freq="D"),
                "open": [100.0, 105.0, 99.75],
                "high": [100.0, 105.0, 99.75],
                "low": [100.0, 105.0, 99.75],
                "close": [100.0, 105.0, 99.75],
                "volume": [100.0, 200.0, 300.0],
            }
        )
        out = prepare_master_feature_frame(bars, interval="day", symbol="RB0")
        # symbol=RB0 → black cluster → limit_pct=0.06；test bar 1 涨 5%，不够 5.9% 阈值
        # 这条测试切换到 symbol="other" 模式（默认 5%）+ close 5% 涨跌
        out2 = prepare_master_feature_frame(bars, interval="day")  # symbol=None → 5% limit
        self.assertTrue((out2["is_one_way_bar"].astype(int) == 1).all())
        self.assertEqual(int(out2["is_limit_up_close"].iloc[1]), 1)
        self.assertEqual(int(out2["is_limit_down_close"].iloc[2]), 1)

    def test_generate_candidate_includes_negative_samples(self) -> None:
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=5, freq="D"),
                "open": [100.0, 100.0, 100.0, 100.0, 100.0],
                "high": [101.0, 102.0, 101.0, 101.0, 101.0],
                "low": [99.0, 99.5, 99.0, 99.0, 99.0],
                "close": [100.0, 105.0, 100.0, 100.0, 100.0],
                "volume": [1000, 1000, 1000, 1000, 1000],
                "open_interest": [1000, 1000, 1000, 1000, 1000],
                "turnover": [1e6, 1e6, 1e6, 1e6, 1e6],
                "atr14": [2.0, 2.0, 2.0, 2.0, 2.0],
                "trend_score": [0.0, 0.0, 0.0, 0.0, 0.0],
                "trend_dir": [0, 0, 0, 0, 0],
                "don_upper_entry": [104.0, 104.0, 104.0, 104.0, 104.0],
                "don_lower_entry": [95.0, 95.0, 95.0, 95.0, 95.0],
                "don_upper_exit": [110.0, 110.0, 110.0, 110.0, 110.0],
                "don_lower_exit": [90.0, 90.0, 90.0, 90.0, 90.0],
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
        self.assertIn("candidate_status", candidate.columns)
        self.assertIn("is_executed", candidate.columns)
        self.assertGreaterEqual((candidate["is_executed"] == 0).sum(), 1)
        self.assertGreaterEqual((candidate["candidate_status"] != "filled").sum(), 1)

    def test_normalize_intervals_supports_mixed_tokens(self) -> None:
        got = _normalize_intervals(["day,60min", "30min,15min,5min", "min,day"])
        self.assertEqual(got, ("day", "minute60", "minute30", "minute15", "minute5", "minute"))

    def test_baseline_strategy_treats_nan_bp_valid_as_false(self) -> None:
        """B4: bp_valid 列被上游写成 NaN 时，breakout_pullback 候选必须当作未触发，
        不能因为 bool(np.nan)=True 误判为 valid setup。
        """
        n = 5
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=n, freq="D"),
                "open": [100.0] * n,
                "high": [101.0] * n,
                "low": [99.0] * n,
                "close": [100.0] * n,
                "volume": [1000] * n,
                "open_interest": [1000] * n,
                "turnover": [1e6] * n,
                "atr14": [2.0] * n,
                "trend_score": [0.0] * n,
                "trend_dir": [0] * n,
                "don_upper_entry": [120.0] * n,
                "don_lower_entry": [80.0] * n,
                "don_upper_exit": [125.0] * n,
                "don_lower_exit": [75.0] * n,
                # bp_valid 全部 NaN —— 模拟上游缺失/异常
                "bp_valid": [np.nan] * n,
                "bp_direction": [""] * n,
                "bp_breakout_level": [np.nan] * n,
                "bp_pullback_low": [np.nan] * n,
                "bp_bars_since_breakout": [0] * n,
                "bp_confirmed": [np.nan] * n,
            }
        )
        candidate = generate_candidate_opportunities(
            frame=frame,
            symbol="RB0",
            exchange="SHFE",
            interval="day",
            signal_type="breakout_pullback_continuation",
            horizon_bars=2,
            trade_side_mode="both",
        )
        # bp_valid 全 NaN 应该完全不产生候选样本
        self.assertEqual(len(candidate), 0)


if __name__ == "__main__":
    unittest.main()
