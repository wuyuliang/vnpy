"""Tests for split OOT helper modules."""
from __future__ import annotations

import unittest
from unittest.mock import patch

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG
from cta.model.oot.oot_gates import filter_candidates
from cta.model.oot.oot_intrabar import _IntrabarBarCache, _simulate_intrabar_exit
from cta.model.oot.oot_metrics import _calc_roll_cost, _count_roll_dates_between, _max_drawdown_from_return_series
from cta.model.oot.oot_portfolio_constraints import ConstraintCaps, cap_notional
from cta.model.oot.oot_position_lifetime import _build_position_lifetime_table
from cta.model.oot.oot_position_sizing import compute_contract_position
from cta.model.oot.oot_trade_simulation import simulate_trades


class TestOotSplitModules(unittest.TestCase):
    def test_max_drawdown_from_return_series(self) -> None:
        ret = pd.Series([0.1, -0.2, 0.05, -0.1])
        mdd = _max_drawdown_from_return_series(ret)
        self.assertAlmostEqual(mdd, -0.25, places=9)

    def test_count_roll_dates_between(self) -> None:
        n = _count_roll_dates_between(
            pd.Timestamp("2024-01-10"),
            pd.Timestamp("2024-03-20"),
            roll_day_of_month=14,
        )
        self.assertEqual(n, 3)

    def test_calc_roll_cost_non_negative(self) -> None:
        cost = _calc_roll_cost(
            symbol="RB0",
            notional=100_000.0,
            entry_ts=pd.Timestamp("2024-01-10"),
            exit_ts=pd.Timestamp("2024-03-20"),
            cfg=DEFAULT_OOT_EVAL_CONFIG,
        )
        self.assertGreaterEqual(float(cost), 0.0)

    def test_simulate_intrabar_exit_triggers_stop(self) -> None:
        bars = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2024-01-01 09:00:00",
                        "2024-01-01 10:00:00",
                        "2024-01-01 11:00:00",
                    ]
                ),
                "open": [100.0, 99.6, 99.8],
                "high": [100.2, 99.9, 100.0],
                "low": [99.7, 98.8, 99.5],
                "close": [99.9, 99.0, 99.9],
            }
        )
        out = _simulate_intrabar_exit(
            side="long",
            entry_ts=pd.Timestamp("2024-01-01 09:00:00"),
            planned_exit_ts=pd.Timestamp("2024-01-01 11:00:00"),
            entry_price_hint=100.0,
            stop_loss_pct=0.01,
            bars=bars,
        )
        self.assertEqual(int(out["stop_triggered"]), 1)
        self.assertEqual(str(out["exit_reason"]), "stop_loss")
        self.assertTrue(np.isfinite(float(out["final_exit_price"])))

    def test_simulate_intrabar_exit_entry_fill_not_before_signal_time(self) -> None:
        bars = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2024-01-01 08:00:00",
                        "2024-01-01 09:00:00",
                        "2024-01-01 10:00:00",
                    ]
                ),
                "open": [99.0, 100.0, 101.0],
                "high": [99.5, 100.5, 101.5],
                "low": [98.5, 99.5, 100.5],
                "close": [99.2, 100.2, 101.2],
            }
        )
        ent = pd.Timestamp("2024-01-01 09:00:00")
        out = _simulate_intrabar_exit(
            side="long",
            entry_ts=ent,
            planned_exit_ts=pd.Timestamp("2024-01-01 10:00:00"),
            entry_price_hint=float("nan"),
            stop_loss_pct=0.01,
            bars=bars,
        )
        self.assertGreaterEqual(pd.Timestamp(out["entry_fill_datetime"]), ent)

    def test_build_position_lifetime_table(self) -> None:
        trades = pd.DataFrame(
            {
                "execution_status": ["executed", "executed"],
                "pos_id": ["p1", "p1"],
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "side": ["long", "long"],
                "entry_datetime": pd.to_datetime(["2024-01-01", "2024-01-02"]),
                "exit_datetime": pd.to_datetime(["2024-01-03", "2024-01-04"]),
                "layer_id": [1, 2],
                "position_notional": [10_000.0, 5_000.0],
            }
        )
        out = _build_position_lifetime_table(trades)
        self.assertEqual(len(out), 1)
        self.assertEqual(int(out.iloc[0]["layer_count"]), 2)
        self.assertGreater(float(out.iloc[0]["peak_notional"]), 0.0)

    def test_gate_module_marks_model_block_reason(self) -> None:
        df = pd.DataFrame(
            {
                "trade_filter_prob": [0.2, 0.9],
                "side": ["long", "long"],
            }
        )
        out = filter_candidates(
            df,
            use_trade_filter_gate=True,
            trade_filter_threshold=0.5,
            use_stacking_gate=False,
        )
        self.assertFalse(bool(out.iloc[0]["model_gate_pass"]))
        self.assertEqual(str(out.iloc[0]["model_gate_reason"]), "blocked_trade_filter")
        self.assertTrue(bool(out.iloc[1]["model_gate_pass"]))

    def test_position_sizing_contract_rounding(self) -> None:
        qty, ntl = compute_contract_position(
            desired_notional=10_000.0,
            entry_price=100.0,
            contract_size=10.0,
            lot_size=3.0,
        )
        self.assertAlmostEqual(qty, 9.0, places=9)
        self.assertAlmostEqual(ntl, 9_000.0, places=9)

    def test_portfolio_constraints_cap_reason_priority(self) -> None:
        capped, reason = cap_notional(
            desired_notional=100_000.0,
            caps=ConstraintCaps(
                cap_daily=10_000.0,
                cap_leverage=9_000.0,
                cap_cash=0.0,
                cap_week=8_000.0,
                cap_symbol=7_000.0,
                cap_cluster=6_000.0,
            ),
        )
        self.assertEqual(float(capped), 0.0)
        self.assertEqual(reason, "blocked_margin_cash")

    def test_trade_simulation_keeps_blocked_rows_flat(self) -> None:
        df = pd.DataFrame(
            {
                "execution_status": ["executed", "blocked_trade_filter", "executed"],
                "trade_return_pct": [0.01, 0.50, -0.02],
            }
        )
        out = simulate_trades(df, initial_capital=100_000.0)
        self.assertAlmostEqual(float(out.iloc[0]["equity_after"]), 101_000.0, places=6)
        self.assertAlmostEqual(float(out.iloc[1]["equity_after"]), 101_000.0, places=6)
        self.assertAlmostEqual(float(out.iloc[2]["equity_after"]), 98_980.0, places=6)

    def test_intrabar_cache_missing_data_error_returns_empty_frame(self) -> None:
        cache = _IntrabarBarCache(
            interval="60min",
            date_span_by_symbol={
                ("RB0", "SHFE"): (
                    pd.Timestamp("2024-01-01 00:00:00"),
                    pd.Timestamp("2024-01-02 00:00:00"),
                )
            },
        )
        with patch("cta.model.oot.oot_intrabar.load_bars", side_effect=FileNotFoundError("missing")):
            out = cache.load("RB0", "SHFE")
        self.assertTrue(out.empty)

    def test_intrabar_cache_unexpected_error_raises_runtime_error(self) -> None:
        cache = _IntrabarBarCache(
            interval="60min",
            date_span_by_symbol={
                ("RB0", "SHFE"): (
                    pd.Timestamp("2024-01-01 00:00:00"),
                    pd.Timestamp("2024-01-02 00:00:00"),
                )
            },
        )
        with patch("cta.model.oot.oot_intrabar.load_bars", side_effect=RuntimeError("boom")):
            with self.assertRaises(RuntimeError):
                cache.load("RB0", "SHFE")


if __name__ == "__main__":
    unittest.main()
