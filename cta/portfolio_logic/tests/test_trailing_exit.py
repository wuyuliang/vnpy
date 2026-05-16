"""Unit tests for trailing-exit simulation."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.portfolio_logic.config import IntervalTrailingParams, PyramidConfig, TrailingExitConfig
from cta.portfolio_logic.pyramid_manager import PyramidManager
from cta.portfolio_logic.trailing_exit import TrailingExitSimulator, simulate_trailing_exit


class TestTrailingExit(unittest.TestCase):
    def test_trailing_activates_and_exits_before_horizon(self) -> None:
        bars = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2024-01-02 09:00:00",
                        "2024-01-02 10:00:00",
                        "2024-01-02 11:00:00",
                    ]
                ),
                "open": [100.0, 103.0, 101.5],
                "high": [100.0, 104.0, 102.0],
                "low": [100.0, 103.0, 100.5],
                "close": [100.0, 103.5, 101.0],
            }
        )
        sim = simulate_trailing_exit(
            side="long",
            entry_ts=pd.Timestamp("2024-01-02 09:00:00"),
            planned_exit_ts=pd.Timestamp("2024-01-02 12:00:00"),
            entry_price_hint=100.0,
            stop_loss_pct=0.02,
            bars=bars,
            interval="30min",
            atr_pct_at_entry=0.01,
            regime_label="trend_up",
            cfg=TrailingExitConfig(),
        )
        self.assertEqual(str(sim["exit_reason"]), "trailing_stop")
        self.assertEqual(int(sim["trailing_activated"]), 1)
        self.assertEqual(pd.Timestamp(sim["final_exit_datetime"]), pd.Timestamp("2024-01-02 11:00:00"))
        self.assertAlmostEqual(float(sim["final_exit_price"]), 101.0, places=6)

    def test_range_regime_keeps_hard_stop_only(self) -> None:
        bars = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2024-01-02 09:00:00",
                        "2024-01-02 10:00:00",
                    ]
                ),
                "open": [100.0, 102.0],
                "high": [100.0, 103.0],
                "low": [100.0, 101.0],
                "close": [100.0, 102.5],
            }
        )
        sim = simulate_trailing_exit(
            side="long",
            entry_ts=pd.Timestamp("2024-01-02 09:00:00"),
            planned_exit_ts=pd.Timestamp("2024-01-02 10:00:00"),
            entry_price_hint=100.0,
            stop_loss_pct=0.02,
            bars=bars,
            interval="30min",
            atr_pct_at_entry=0.01,
            regime_label="range",
            cfg=TrailingExitConfig(),
        )
        self.assertEqual(str(sim["exit_reason"]), "horizon_exit")
        self.assertEqual(int(sim["trailing_activated"]), 0)

    def test_unknown_interval_uses_default_params(self) -> None:
        bars = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2024-01-02 09:00:00",
                        "2024-01-02 10:00:00",
                        "2024-01-02 11:00:00",
                    ]
                ),
                "open": [100.0, 103.0, 99.0],
                "high": [100.0, 104.0, 100.0],
                "low": [100.0, 102.5, 98.0],
                "close": [100.0, 103.2, 99.2],
            }
        )
        sim = simulate_trailing_exit(
            side="long",
            entry_ts=pd.Timestamp("2024-01-02 09:00:00"),
            planned_exit_ts=pd.Timestamp("2024-01-02 12:00:00"),
            entry_price_hint=100.0,
            stop_loss_pct=0.02,
            bars=bars,
            interval="unknown_interval",
            atr_pct_at_entry=0.01,
            regime_label="trend_up",
            cfg=TrailingExitConfig(default_interval_params_key="30min"),
        )
        self.assertEqual(str(sim["exit_reason"]), "trailing_stop")

    def test_minute60_alias_uses_60min_trailing_params(self) -> None:
        bars = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2024-01-02 09:00:00",
                        "2024-01-02 10:00:00",
                    ]
                ),
                "open": [100.0, 103.5],
                "high": [100.0, 105.0],
                "low": [100.0, 103.5],
                "close": [100.0, 103.5],
            }
        )
        cfg = TrailingExitConfig(
            interval_params={
                "60min": IntervalTrailingParams(
                    atr_multiplier=1.0,
                    activation_profit_atr=0.0,
                    fallback_hard_stop_pct=0.50,
                ),
                "30min": IntervalTrailingParams(
                    atr_multiplier=10.0,
                    activation_profit_atr=0.0,
                    fallback_hard_stop_pct=0.50,
                ),
            },
            default_interval_params_key="30min",
        )
        sim = simulate_trailing_exit(
            side="long",
            entry_ts=pd.Timestamp("2024-01-02 09:00:00"),
            planned_exit_ts=pd.Timestamp("2024-01-02 10:00:00"),
            entry_price_hint=100.0,
            stop_loss_pct=0.50,
            bars=bars,
            interval="minute60",
            atr_pct_at_entry=0.01,
            regime_label="trend_up",
            cfg=cfg,
        )
        self.assertEqual(str(sim["exit_reason"]), "trailing_stop")
        self.assertAlmostEqual(float(sim["trailing_stop_price"]), 104.0, places=6)

    def test_streaming_simulator_updates_layer_and_emits_exit(self) -> None:
        manager = PyramidManager(cfg=PyramidConfig(cooldown_bars_per_interval={"60min": 0}))
        trailing_cfg = TrailingExitConfig(
            interval_params={
                "60min": IntervalTrailingParams(
                    atr_multiplier=1.0,
                    activation_profit_atr=0.2,
                    fallback_hard_stop_pct=0.03,
                )
            }
        )
        pos = manager.open_first_layer(
            symbol="RB0",
            exchange="SHFE",
            direction="long",
            interval="60min",
            entry_time=pd.Timestamp("2024-01-02 09:00:00"),
            entry_price=100.0,
            notional=100000.0,
            atr_pct_at_entry=0.01,
            signal_score=0.8,
            trade_filter_prob=0.7,
            trade_filter_prob_pctl=75.0,
            trailing_cfg=trailing_cfg,
        )
        sim = TrailingExitSimulator(trailing_cfg)
        bar1 = {
            "datetime": pd.Timestamp("2024-01-02 10:00:00"),
            "symbol": "RB0",
            "exchange": "SHFE",
            "open": 103.7,
            "high": 104.0,
            "low": 103.6,
            "close": 103.0,
        }
        events1 = sim.update_one_bar([pos], bar1, regime_label_by_symbol={("RB0", "SHFE"): "trend_up"})
        self.assertEqual(len(events1), 0)
        self.assertTrue(pos.layers[0].trailing_activated)
        self.assertGreater(float(pos.layers[0].trail_stop_price), float(pos.layers[0].hard_stop_price))

        bar2 = {
            "datetime": pd.Timestamp("2024-01-02 11:00:00"),
            "symbol": "RB0",
            "exchange": "SHFE",
            "open": 102.0,
            "high": 102.5,
            "low": 101.8,
            "close": 102.0,
        }
        events2 = sim.update_one_bar([pos], bar2, regime_label_by_symbol={("RB0", "SHFE"): "trend_up"})
        self.assertEqual(len(events2), 1)
        self.assertTrue(pos.layers[0].exited)
        self.assertEqual(str(events2[0]["reason"]), "trailing_stop")


if __name__ == "__main__":
    unittest.main()
