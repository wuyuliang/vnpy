"""Parity checks between batch trailing exit and streaming simulator."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.portfolio_logic.config import IntervalTrailingParams, PyramidConfig, TrailingExitConfig
from cta.portfolio_logic.pyramid_manager import PyramidManager
from cta.portfolio_logic.trailing_exit import TrailingExitSimulator, simulate_trailing_exit


class TestOotSimParity(unittest.TestCase):
    def test_single_layer_trailing_exit_parity(self) -> None:
        bars = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2024-01-02 09:00:00",
                        "2024-01-02 10:00:00",
                        "2024-01-02 11:00:00",
                    ]
                ),
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "open": [100.0, 103.0, 102.0],
                "high": [100.0, 104.0, 102.5],
                "low": [100.0, 103.0, 101.8],
                "close": [100.0, 103.5, 102.0],
            }
        )
        cfg = TrailingExitConfig(
            interval_params={
                "60min": IntervalTrailingParams(
                    atr_multiplier=1.0,
                    activation_profit_atr=0.2,
                    fallback_hard_stop_pct=0.02,
                )
            }
        )
        batch = simulate_trailing_exit(
            side="long",
            entry_ts=pd.Timestamp("2024-01-02 09:00:00"),
            planned_exit_ts=pd.Timestamp("2024-01-02 12:00:00"),
            entry_price_hint=100.0,
            stop_loss_pct=0.02,
            bars=bars[["datetime", "open", "high", "low", "close"]],
            interval="60min",
            atr_pct_at_entry=0.01,
            regime_label="trend_up",
            cfg=cfg,
        )

        manager = PyramidManager(PyramidConfig(cooldown_bars_per_interval={"60min": 0}))
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
            trailing_cfg=cfg,
        )
        stream = TrailingExitSimulator(cfg)
        events = []
        for _, row in bars.iloc[1:].iterrows():
            events.extend(
                stream.update_one_bar(
                    [pos],
                    row.to_dict(),
                    regime_label_by_symbol={("RB0", "SHFE"): "trend_up"},
                )
            )
            if events:
                break
        self.assertTrue(events)
        self.assertEqual(pd.Timestamp(batch["final_exit_datetime"]), pd.Timestamp(events[0]["exit_datetime"]))


if __name__ == "__main__":
    unittest.main()
