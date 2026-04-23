"""drawdown_control.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.position_portfolio.drawdown_control import (
    apply_dd_to_sizing,
    compute_dd_state,
)


class TestDrawdownControl(unittest.TestCase):
    def test_compute_dd_state(self) -> None:
        equity = pd.Series([1_000_000, 990_000, 970_000, 940_000, 960_000])
        out = compute_dd_state(equity)
        self.assertIn("dd", out.columns)
        self.assertIn("level", out.columns)
        self.assertEqual(str(out["level"].iloc[-1]), "mild")

    def test_apply_dd_to_sizing(self) -> None:
        state_df = compute_dd_state(pd.Series([100.0, 95.0]))
        lots = apply_dd_to_sizing(base_lots=10, dd_state=state_df.iloc[-1])
        self.assertGreaterEqual(lots, 0)
        self.assertLessEqual(lots, 10)


if __name__ == "__main__":
    unittest.main()
