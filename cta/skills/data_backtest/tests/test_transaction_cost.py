"""transaction_cost.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.data_backtest.transaction_cost import apply_cost_to_pnl, estimate_cost


class TestTransactionCost(unittest.TestCase):
    def test_estimate_cost(self) -> None:
        c = estimate_cost(
            symbol="rb888.SHFE",
            price=3500.0,
            lots=2,
            side="long",
            multiplier=10.0,
            commission_rate=0.0001,
            tick_size=1.0,
            slippage_ticks=1.5,
            adv=1_000_000.0,
        )
        self.assertGreater(c.total, 0.0)
        self.assertGreaterEqual(c.impact, 0.0)

    def test_apply_cost_to_pnl(self) -> None:
        trades = pd.DataFrame(
            {
                "symbol": ["rb888.SHFE", "rb888.SHFE"],
                "price": [3500.0, 3510.0],
                "lots": [1, 1],
                "side": ["long", "short"],
                "gross_pnl": [100.0, -50.0],
                "multiplier": [10.0, 10.0],
                "commission_rate": [0.0001, 0.0001],
                "tick_size": [1.0, 1.0],
            }
        )
        out = apply_cost_to_pnl(trades, estimate_cost)
        self.assertIn("cost", out.columns)
        self.assertIn("net_pnl", out.columns)


if __name__ == "__main__":
    unittest.main()

