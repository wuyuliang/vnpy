"""Unit tests for pyramid position manager."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.portfolio_logic.config import PyramidConfig, TrailingExitConfig
from cta.portfolio_logic.pyramid_manager import PyramidManager


class TestPyramidManager(unittest.TestCase):
    def test_open_and_add_layer_respects_interval_rule(self) -> None:
        manager = PyramidManager(
            PyramidConfig(
                max_active_layers=3,
                cooldown_bars_per_interval={"60min": 0, "30min": 0, "15min": 0},
                one_layer_per_interval=True,
            )
        )
        trailing_cfg = TrailingExitConfig()
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
        can_same_interval = manager.decide_add_layer(
            pos=pos,
            new_interval="60min",
            current_time=pd.Timestamp("2024-01-02 10:00:00"),
            current_price=102.0,
            min_profit_atr_to_add=0.0,
            htf_aligned=True,
        )
        self.assertFalse(can_same_interval)
        can_new_interval = manager.decide_add_layer(
            pos=pos,
            new_interval="30min",
            current_time=pd.Timestamp("2024-01-02 10:00:00"),
            current_price=102.0,
            min_profit_atr_to_add=0.0,
            htf_aligned=True,
        )
        self.assertTrue(can_new_interval)

    def test_pyramid_rejects_disallowed_signal_interval(self) -> None:
        manager = PyramidManager(
            PyramidConfig(
                cooldown_bars_per_interval={"30min": 0},
                one_layer_per_interval=False,
                allowed_signal_type_interval=("bull_pullback_continuation|day",),
            )
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
            trailing_cfg=TrailingExitConfig(),
        )
        can_add = manager.decide_add_layer(
            pos=pos,
            new_interval="30min",
            new_signal_type="bull_pullback_continuation",
            current_time=pd.Timestamp("2024-01-02 10:00:00"),
            current_price=102.0,
            min_profit_atr_to_add=1.2,
            htf_aligned=True,
        )
        self.assertFalse(can_add)

    def test_pyramid_allows_bull_day_after_profit_threshold(self) -> None:
        manager = PyramidManager(
            PyramidConfig(
                cooldown_bars_per_interval={"day": 0},
                one_layer_per_interval=False,
                allowed_signal_type_interval=("bull_pullback_continuation|day",),
            )
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
            trailing_cfg=TrailingExitConfig(),
        )
        can_add = manager.decide_add_layer(
            pos=pos,
            new_interval="day",
            new_signal_type="bull_pullback_continuation",
            current_time=pd.Timestamp("2024-01-05 09:00:00"),
            current_price=101.3,
            min_profit_atr_to_add=1.2,
            htf_aligned=True,
        )
        self.assertTrue(can_add)

    def test_cooldown_and_max_layers(self) -> None:
        manager = PyramidManager(
            PyramidConfig(
                max_active_layers=2,
                cooldown_bars_per_interval={"30min": 2},
                one_layer_per_interval=False,
            )
        )
        trailing_cfg = TrailingExitConfig()
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
        can_in_cooldown = manager.decide_add_layer(
            pos=pos,
            new_interval="30min",
            current_time=pd.Timestamp("2024-01-02 09:30:00"),
            current_price=102.0,
            min_profit_atr_to_add=0.0,
            htf_aligned=True,
        )
        self.assertFalse(can_in_cooldown)
        can_after_cooldown = manager.decide_add_layer(
            pos=pos,
            new_interval="30min",
            current_time=pd.Timestamp("2024-01-02 10:00:00"),
            current_price=102.0,
            min_profit_atr_to_add=0.0,
            htf_aligned=True,
        )
        self.assertTrue(can_after_cooldown)
        manager.add_layer(
            pos=pos,
            interval="30min",
            entry_time=pd.Timestamp("2024-01-02 10:00:00"),
            entry_price=102.0,
            notional=50000.0,
            atr_pct_at_entry=0.01,
            signal_score=0.9,
            trade_filter_prob=0.75,
            trade_filter_prob_pctl=78.0,
            trailing_cfg=trailing_cfg,
        )
        cannot_exceed_layers = manager.decide_add_layer(
            pos=pos,
            new_interval="15min",
            current_time=pd.Timestamp("2024-01-02 11:00:00"),
            current_price=103.0,
            min_profit_atr_to_add=0.0,
            htf_aligned=True,
        )
        self.assertFalse(cannot_exceed_layers)

    def test_layer_has_hard_and_trail_stop_with_effective_stop(self) -> None:
        manager = PyramidManager(PyramidConfig(cooldown_bars_per_interval={"60min": 0}))
        trailing_cfg = TrailingExitConfig()
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
        layer = pos.layers[0]
        self.assertLess(float(layer.hard_stop_price), float(layer.entry_price))
        self.assertLess(float(layer.trail_stop_price), float(layer.hard_stop_price))
        layer.trail_stop_price = 99.5
        self.assertAlmostEqual(float(layer.effective_stop), 99.5, places=6)
        layer.trail_stop_price = float("nan")
        self.assertAlmostEqual(float(layer.effective_stop), float(layer.hard_stop_price), places=6)

    def test_force_close_all_marks_active_layers_exited(self) -> None:
        manager = PyramidManager(
            PyramidConfig(
                max_active_layers=3,
                cooldown_bars_per_interval={"60min": 0, "30min": 0},
                one_layer_per_interval=False,
            )
        )
        trailing_cfg = TrailingExitConfig()
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
        manager.add_layer(
            pos=pos,
            interval="30min",
            entry_time=pd.Timestamp("2024-01-02 10:00:00"),
            entry_price=101.0,
            notional=50000.0,
            atr_pct_at_entry=0.01,
            signal_score=0.9,
            trade_filter_prob=0.75,
            trade_filter_prob_pctl=78.0,
            trailing_cfg=trailing_cfg,
        )
        events = manager.force_close_all(
            pos=pos,
            exit_price=99.0,
            exit_time=pd.Timestamp("2024-01-02 11:00:00"),
            reason="risk_halt",
        )
        self.assertEqual(len(events), 2)
        self.assertEqual(len(pos.active_layers), 0)


if __name__ == "__main__":
    unittest.main()
