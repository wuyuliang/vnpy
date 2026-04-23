"""event_driven_backtest.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.data_backtest.event_driven_backtest import (
    EngineConfig,
    run_backtest,
    simulate_fill,
)


class _DummyStrategy:
    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict]:
        if i == 0 and position == 0:
            return [{"side": "long", "lots": 1, "order_type": "market"}]
        if i == 3 and position > 0:
            return [{"side": "flat", "lots": 1, "order_type": "market"}]
        return []


def _bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-01", periods=8, freq="D"),
            "open": [100, 101, 102, 103, 104, 105, 106, 107],
            "high": [101, 102, 103, 104, 105, 106, 107, 108],
            "low": [99, 100, 101, 102, 103, 104, 105, 106],
            "close": [100.5, 101.5, 102.2, 103.1, 104.4, 105.2, 106.3, 107.4],
        }
    )


class TestEventDrivenBacktest(unittest.TestCase):
    def test_simulate_fill(self) -> None:
        bars = _bars()
        order = {"side": "long", "order_type": "market", "lots": 1}
        fill = simulate_fill(order, bars.iloc[0], bars.iloc[1], EngineConfig())
        self.assertIsNotNone(fill)
        self.assertEqual(fill["price"], bars.iloc[1]["open"])

    def test_run_backtest(self) -> None:
        bars = _bars()
        out = run_backtest(bars, _DummyStrategy(), EngineConfig())
        self.assertIn("trade_log", out)
        self.assertIn("equity_curve", out)
        self.assertGreaterEqual(len(out["trade_log"]), 1)


if __name__ == "__main__":
    unittest.main()

