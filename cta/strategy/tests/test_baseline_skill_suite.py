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


class TestBaselineSkillSuite(unittest.TestCase):
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
        ):
            self.assertIn(col, self.frame.columns)

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

    def test_build_training_samples_from_trade_log(self) -> None:
        trade_log = pd.DataFrame(
            [
                {
                    "entry_i": 30,
                    "exit_i": 35,
                    "side": "long",
                    "lots": 1,
                    "entry_price": float(self.frame.iloc[30]["close"]),
                    "exit_price": float(self.frame.iloc[35]["close"]),
                    "gross_pnl": 12.0,
                    "cost": 1.5,
                    "net_pnl": 10.5,
                },
                {
                    "entry_i": 120,
                    "exit_i": 125,
                    "side": "short",
                    "lots": 1,
                    "entry_price": float(self.frame.iloc[120]["close"]),
                    "exit_price": float(self.frame.iloc[125]["close"]),
                    "gross_pnl": -8.0,
                    "cost": 1.2,
                    "net_pnl": -9.2,
                },
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
        self.assertEqual(len(samples), 2)
        for col in (
            "symbol",
            "exchange",
            "interval",
            "datetime",
            "signal_type",
            "side",
            "label_net_pnl",
            "label_win",
            "feature_close",
            "feature_atr14",
            "feature_breakout_score",
        ):
            self.assertIn(col, samples.columns)

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
            "signal_type",
            "side",
            "entry_i",
            "entry_price",
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

    def test_prepare_master_feature_frame_drop_duplicate_datetime(self) -> None:
        bars = self.bars.copy()
        dup = bars.iloc[[10]].copy()
        bars = pd.concat([bars, dup], axis=0, ignore_index=True)
        out = prepare_master_feature_frame(bars, interval="day")
        self.assertEqual(len(out), out["datetime"].nunique())
        self.assertTrue(out["tr_valid"].notna().any())

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

    def test_normalize_intervals_supports_mixed_tokens(self) -> None:
        got = _normalize_intervals(["day,60min", "30min,15min,5min", "min,day"])
        self.assertEqual(got, ("day", "minute60", "minute30", "minute15", "minute5", "minute"))

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

    def test_run_baseline_suite_multi_dispatches_each_interval(self) -> None:
        import cta.strategy.baseline_skill_suite as suite

        seen: list[str] = []

        def _fake_run(**kwargs):
            interval = str(kwargs.get("interval", ""))
            seen.append(interval)
            return BaselineSuiteRunResult(
                output_dir=Path("/tmp") / f"run_{interval}",
                summary_path=Path("/tmp") / f"{interval}_summary.csv",
                training_samples_path=Path("/tmp") / f"{interval}_samples.csv",
                report_path=Path("/tmp") / f"{interval}_report.md",
            )

        with patch.object(suite, "run_baseline_suite", side_effect=_fake_run):
            out = suite.run_baseline_suite_multi(
                symbol="RB0",
                exchange="SHFE",
                intervals=("day,60min", "30min"),
                start_date="2019-01-01",
                end_date="2019-01-31",
            )

        self.assertEqual(seen, ["day", "minute60", "minute30"])
        self.assertEqual(len(out), 3)

    # ---------------- New regression tests (T-D, T-I) ----------------

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

    # ---------------- B4: _safe_bool 防 bool(np.nan) ----------------
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


    # ---------------- L2: trade_log 路径必须用 signal bar 取特征 + 写 signal_datetime
    def test_build_training_samples_from_trade_log_uses_signal_bar_features(self) -> None:
        """L2: 旧实现 ``sample[feature_*] = entry_row[c]`` 把 entry_bar (i+1) 的
        特征当成 "决策时刻特征"，模型推理时其实只看 signal_bar (i) → 训练里多看
        1 根 bar，构成 next-bar 穿越。修复后特征必须取自 signal_row (entry_i-1)，
        同时写出 ``signal_datetime`` 字段供下游 merge_asof 用。
        """
        # 在 atr14 列里塞独特数值，让来源可追踪
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2018-01-01", periods=4, freq="D"),
                "open": [100.0, 101.0, 102.0, 103.0],
                "high": [102.0, 103.0, 104.0, 105.0],
                "low": [99.0, 100.0, 101.0, 102.0],
                "close": [101.0, 102.0, 103.0, 104.0],
                # 4 根 bar 的 atr14 各不相同，方便断言来源
                "atr14": [11.0, 22.0, 33.0, 44.0],
                "volume": [100, 100, 100, 100],
            }
        )
        # signal i=1（atr14=22.0）→ entry i=2（atr14=33.0）。
        # 旧实现：feature_atr14 == 33.0（entry_bar）；
        # 修复后：feature_atr14 == 22.0（signal_bar）。
        trade_log = pd.DataFrame(
            [
                {
                    "side": "long",
                    "entry_i": 2,
                    "exit_i": 3,
                    "entry_price": 102.0,
                    "exit_price": 103.0,
                    "gross_pnl": 1.0,
                    "cost": 0.0,
                    "net_pnl": 1.0,
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
        self.assertEqual(len(out), 1)
        # 必须取 signal bar (i=1) 的 atr14
        self.assertAlmostEqual(float(out.iloc[0]["feature_atr14"]), 22.0)
        # 必须写出 signal_datetime
        self.assertIn("signal_datetime", out.columns)
        self.assertEqual(
            pd.Timestamp(out.iloc[0]["signal_datetime"]),
            pd.Timestamp("2018-01-02"),
        )
        # datetime 仍是 entry_bar 时间（i=2 → 2018-01-03），便于成交时点对账
        self.assertEqual(
            pd.Timestamp(out.iloc[0]["datetime"]),
            pd.Timestamp("2018-01-03"),
        )

    # ---------------- D6: trade_log 路径产物必须写出 atr_warmed ---------------
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


if __name__ == "__main__":
    unittest.main()
