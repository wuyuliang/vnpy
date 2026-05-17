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



class TestBaselineSkillSuitePart04(unittest.TestCase):
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

    def test_generate_candidate_label_uses_stop_aware_execution_path(self) -> None:
        """P0.5: 标签应按执行路径计算，先止损后反弹不能记成正样本。"""
        frame = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=5, freq="D"),
                "open": [100.0, 100.0, 106.0, 96.0, 118.0],
                "high": [101.0, 105.0, 106.0, 120.0, 119.0],
                "low": [99.0, 99.0, 95.0, 95.0, 117.0],
                "close": [100.0, 105.0, 96.0, 118.0, 118.0],
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
            label_stop_loss_pct=0.02,
        )
        filled = candidate.loc[candidate["candidate_status"] == "filled"].copy()
        self.assertGreaterEqual(len(filled), 1)
        row = filled.iloc[0]
        # entry=106, stop=103.88, entry bar low=95 -> 先触发止损；
        # 虽然后续 bar 高点到 120，也不能把标签当成正样本。
        self.assertEqual(int(row["label_class"]), 0)
        self.assertLess(float(row["future_pnl_atr"]), 0.0)
        # 真实执行在 entry bar 已止损，未来 MFE 不应吃到后续 120 的高点。
        self.assertLess(float(row["future_mfe_atr"]), 0.2)

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


if __name__ == "__main__":
    unittest.main()
