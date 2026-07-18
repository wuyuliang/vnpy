import unittest

import numpy as np
import pandas as pd

from stock.etf.config import StrategyConfig
from stock.etf.risk import (
    MarketState,
    MarketStateTracker,
    calculate_breadth,
    classify_market_candidate,
)


class RiskTests(unittest.TestCase):
    def test_breadth_uses_valid_point_in_time_members(self) -> None:
        daily = pd.DataFrame(
            {
                "close": [12.0, 8.0, 9.0, 12.0],
                "ema20": [10.0, 10.0, np.nan, 10.0],
                "is_trading": [True, True, True, False],
            },
            index=["A.SH", "B.SH", "C.SH", "D.SH"],
        )

        self.assertEqual(calculate_breadth(daily), 0.5)

    def test_breadth_returns_none_without_valid_members(self) -> None:
        daily = pd.DataFrame(
            {
                "close": [12.0],
                "ema20": [np.nan],
                "is_trading": [True],
            }
        )

        self.assertIsNone(calculate_breadth(daily))

    def test_market_state_requires_two_matching_candidate_days(self) -> None:
        tracker = MarketStateTracker(
            state=MarketState.CAUTION,
            confirmation_days=2,
        )

        self.assertEqual(tracker.advance(MarketState.RISK_ON), MarketState.CAUTION)
        self.assertEqual(tracker.advance(MarketState.RISK_ON), MarketState.RISK_ON)
        self.assertEqual(tracker.advance(MarketState.RISK_OFF), MarketState.RISK_ON)
        self.assertEqual(tracker.advance(MarketState.CAUTION), MarketState.RISK_ON)
        self.assertEqual(tracker.advance(MarketState.RISK_OFF), MarketState.RISK_ON)
        self.assertEqual(tracker.advance(MarketState.RISK_OFF), MarketState.RISK_OFF)

    def test_market_candidate_uses_strict_risk_off_boundaries(self) -> None:
        config = StrategyConfig(risk_off_breadth_threshold=0.35)
        at_ema = pd.Series({"close": 100.0, "ema20": 100.0})
        below_ema = pd.Series({"close": 99.0, "ema20": 100.0})

        self.assertEqual(
            classify_market_candidate(at_ema, False, 0.20, config),
            MarketState.CAUTION,
        )
        self.assertEqual(
            classify_market_candidate(below_ema, False, 0.35, config),
            MarketState.CAUTION,
        )
        self.assertEqual(
            classify_market_candidate(below_ema, False, 0.349, config),
            MarketState.RISK_OFF,
        )
        self.assertEqual(
            classify_market_candidate(below_ema, False, None, config),
            MarketState.RISK_OFF,
        )
        self.assertEqual(
            classify_market_candidate(below_ema, True, 0.10, config),
            MarketState.RISK_ON,
        )


if __name__ == "__main__":
    unittest.main()
