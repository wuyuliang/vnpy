"""single_trade_risk.py tests."""
from __future__ import annotations

import unittest

from cta.skills.position_portfolio.single_trade_risk import (
    resolve_multiplier,
    size_by_risk_pct,
)


class TestSingleTradeRisk(unittest.TestCase):
    def test_size_by_risk_pct(self) -> None:
        out = size_by_risk_pct(
            entry=100.0,
            stop=99.0,
            equity=1_000_000.0,
            contract_multiplier=10.0,
            risk_pct=0.001,
            max_lots=100,
        )
        self.assertEqual(out.skip, False)
        self.assertGreaterEqual(out.lot_size, 1)

    def test_skip_when_too_small(self) -> None:
        out = size_by_risk_pct(
            entry=100.0,
            stop=50.0,
            equity=10_000.0,
            contract_multiplier=20.0,
            risk_pct=0.001,
            max_lots=50,
        )
        self.assertEqual(out.skip, True)
        self.assertEqual(out.lot_size, 0)

    def test_resolve_multiplier(self) -> None:
        self.assertGreater(resolve_multiplier("rb888.SHFE"), 0)
        self.assertGreater(resolve_multiplier("RB0"), 0)


if __name__ == "__main__":
    unittest.main()

