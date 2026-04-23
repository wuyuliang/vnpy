"""sector_exposure.py tests."""
from __future__ import annotations

import unittest

from cta.skills.position_portfolio.sector_exposure import (
    compute_portfolio_exposure,
    sector_cap_gate,
)


class TestSectorExposure(unittest.TestCase):
    def test_compute_portfolio_exposure(self) -> None:
        positions = [
            {"symbol": "rb888.SHFE", "direction": "long", "risk_amount": 3000.0},
            {"symbol": "hc888.SHFE", "direction": "long", "risk_amount": 2500.0},
            {"symbol": "cu888.SHFE", "direction": "short", "risk_amount": 1500.0},
        ]
        out = compute_portfolio_exposure(positions=positions, equity=1_000_000.0)
        self.assertIn("black", out)
        self.assertGreaterEqual(out["black"].same_dir_count, 1)

    def test_sector_cap_gate(self) -> None:
        positions = [
            {"symbol": "rb888.SHFE", "direction": "long", "risk_amount": 9000.0},
            {"symbol": "hc888.SHFE", "direction": "long", "risk_amount": 5000.0},
        ]
        exposure = compute_portfolio_exposure(positions=positions, equity=1_000_000.0)
        new_order = {"symbol": "i888.DCE", "direction": "long", "risk_amount": 5000.0}
        ok = sector_cap_gate(
            new_order=new_order,
            current_exposure=exposure,
            max_sector_net=0.01,
            max_same_dir=3,
        )
        self.assertEqual(ok, False)


if __name__ == "__main__":
    unittest.main()

