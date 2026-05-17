"""Tests for split OOT helper modules."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG
from cta.model.oot_intrabar import _simulate_intrabar_exit
from cta.model.oot_metrics import _calc_roll_cost, _count_roll_dates_between, _max_drawdown_from_return_series
from cta.model.oot_position_lifetime import _build_position_lifetime_table


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


if __name__ == "__main__":
    unittest.main()
