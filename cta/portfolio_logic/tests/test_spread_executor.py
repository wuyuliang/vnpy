"""Tests for spread arbitrage portfolio executor."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.config.spread_arbitrage_config import SpreadArbitrageConfig
from cta.config.spread_pair_registry import SpreadPair
from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.portfolio_logic.spread_executor import SpreadArbitrageExecutor


def _pair() -> SpreadPair:
    return SpreadPair(
        pair_key="rb_hc",
        pair_type="cross_instrument",
        leg1_symbol="RB",
        leg2_symbol="HC",
        leg1_exchange="SHFE",
        leg2_exchange="SHFE",
        hedge_ratio=1.0,
        cluster="black",
    )


def _bars(close_values: list[float]) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=len(close_values), freq="D"),
            "close": close_values,
        }
    )


class TestSpreadArbitrageExecutor(unittest.TestCase):
    def test_default_disabled_returns_empty(self) -> None:
        executor = SpreadArbitrageExecutor(
            cfg=SpreadArbitrageConfig(),
            pairs=(_pair(),),
            bars_provider=lambda _ts: {
                "RB": _bars([100, 101, 99, 100, 101, 120]),
                "HC": _bars([100, 100, 100, 100, 100, 100]),
            },
            interval="day",
        )
        state = PortfolioState(equity=1_000_000.0)
        intents = executor.step(pd.Timestamp("2024-01-06"), state)
        self.assertEqual(intents, [])

    def test_emits_intents_when_enabled(self) -> None:
        cfg = SpreadArbitrageConfig(
            use_spread_arbitrage=True,
            rolling_window_days=5,
            z_entry=2.0,
            enabled_by_pair_interval={"rb_hc|day": True},
        )
        bars = {
            "RB": _bars([100, 101, 99, 100, 101, 120]),
            "HC": _bars([100, 100, 100, 100, 100, 100]),
        }
        executor = SpreadArbitrageExecutor(
            cfg=cfg,
            pairs=(_pair(),),
            bars_provider=lambda _ts: bars,
            interval="day",
        )
        state = PortfolioState(equity=1_000_000.0)
        # warmup 1~5
        for ts in pd.date_range("2024-01-01", periods=5, freq="D"):
            _ = executor.step(ts, state)
        intents = executor.step(pd.Timestamp("2024-01-06"), state)
        self.assertEqual(len(intents), 2)
        self.assertTrue(all(intent.signal_type == "spread_arbitrage" for intent in intents))


if __name__ == "__main__":
    unittest.main()

