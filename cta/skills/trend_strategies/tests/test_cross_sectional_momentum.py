"""cross_sectional_momentum.py tests."""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.skills.trend_strategies.cross_sectional_momentum import (
    XSPortfolioTarget,
    build_xs_portfolio,
    compute_xs_momentum,
)


def _panel(n: int = 180) -> pd.DataFrame:
    idx = pd.date_range("2023-01-01", periods=n, freq="D")
    rng = np.random.default_rng(2)
    return pd.DataFrame(
        {
            "RB": 100 + np.cumsum(rng.normal(0.08, 0.3, size=n)),
            "CU": 90 + np.cumsum(rng.normal(0.03, 0.4, size=n)),
            "AL": 80 + np.cumsum(rng.normal(-0.02, 0.25, size=n)),
            "MA": 70 + np.cumsum(rng.normal(-0.04, 0.35, size=n)),
            "M": 60 + np.cumsum(rng.normal(0.01, 0.2, size=n)),
        },
        index=idx,
    )


class TestCrossSectionalMomentum(unittest.TestCase):
    def test_compute_xs_momentum(self) -> None:
        mom = compute_xs_momentum(_panel(), lookback=60, vol_window=60)
        self.assertEqual(mom.shape[1], 5)

    def test_build_xs_portfolio(self) -> None:
        mom = compute_xs_momentum(_panel())
        targets = build_xs_portfolio(mom, top_pct=0.2, bot_pct=0.2, gross_exposure=1.0)
        self.assertGreater(len(targets), 0)
        self.assertIsInstance(targets[0], XSPortfolioTarget)


if __name__ == "__main__":
    unittest.main()

