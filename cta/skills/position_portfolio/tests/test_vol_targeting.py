"""vol_targeting.py tests."""
from __future__ import annotations

import unittest

from cta.skills.position_portfolio.vol_targeting import combine_sizing, vol_target_size


class TestVolTargeting(unittest.TestCase):
    def test_vol_target_size(self) -> None:
        out = vol_target_size(
            equity=1_000_000.0,
            atr=5.0,
            contract_multiplier=10.0,
            target_vol=0.1,
        )
        self.assertGreater(out.target_daily_std, 0.0)
        self.assertGreater(out.per_lot_daily_std, 0.0)
        self.assertGreaterEqual(out.target_lots, 0)

    def test_combine_sizing(self) -> None:
        lots = combine_sizing(risk_pct_lots=8, vol_target_lots=6, max_lots=5)
        self.assertEqual(lots, 5)


if __name__ == "__main__":
    unittest.main()

