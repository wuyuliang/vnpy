"""portfolio_allocation.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.position_portfolio.portfolio_allocation import (
    allocate_portfolio,
    resolve_conflict,
)


class TestPortfolioAllocation(unittest.TestCase):
    def test_allocate_portfolio(self) -> None:
        pnl = pd.DataFrame(
            {
                "s1": np.array([1, -1, 2, 0, 1], dtype=float),
                "s2": np.array([0.5, 0.3, -0.4, 0.2, 0.1], dtype=float),
                "s3": np.array([2.0, -2.5, 1.2, 0.8, -0.6], dtype=float),
            }
        )
        plan = allocate_portfolio(
            strategy_pnl_panel=pnl,
            total_risk_pct=0.03,
            scheme="risk_parity",
        )
        self.assertAlmostEqual(sum(plan.weights.values()), 1.0, places=6)
        self.assertEqual(set(plan.weights), {"s1", "s2", "s3"})

    def test_resolve_conflict(self) -> None:
        existing = [{"symbol": "rb888.SHFE", "side": "long", "lots": 3}]
        new_order = {"symbol": "rb888.SHFE", "side": "short", "lots": 2}
        out = resolve_conflict(new_order, existing, policy="net")
        self.assertIsNotNone(out)
        self.assertEqual(out["side"], "long")
        self.assertEqual(out["lots"], 1)


if __name__ == "__main__":
    unittest.main()

