import unittest

import numpy as np
import pandas as pd

from stock.etf.config import StrategyConfig
from stock.etf.risk import (
    MarketState,
    MarketStateTracker,
    RiskPosition,
    calculate_breadth,
    classify_market_candidate,
    correlation_clusters,
    plan_trim_quantities,
)


class RiskTests(unittest.TestCase):
    def test_correlation_clusters_are_point_in_time_connected_components(self) -> None:
        dates = pd.bdate_range("2026-01-02", periods=50)
        base = np.linspace(-0.02, 0.03, len(dates))
        returns = pd.DataFrame(
            {
                "A.SH": base,
                "B.SH": base,
                "C.SH": base,
                "D.SH": -base,
            },
            index=dates,
        )
        config = StrategyConfig(
            correlation_threshold=0.90,
            max_correlation_weight=0.30,
        )

        clusters = correlation_clusters(
            ["D.SH", "C.SH", "B.SH", "A.SH"],
            returns,
            dates[-1],
            config,
        )

        self.assertEqual(clusters, [{"A.SH", "B.SH", "C.SH"}, {"D.SH"}])

    def test_correlation_threshold_is_inclusive(self) -> None:
        dates = pd.bdate_range("2026-01-02", periods=40)
        x = np.linspace(-1.0, 1.0, len(dates))
        z = np.sin(np.linspace(0.0, 8.0 * np.pi, len(dates)))
        x = (x - x.mean()) / x.std(ddof=1)
        z = z - z.mean()
        z = z - x * np.dot(x, z) / np.dot(x, x)
        z = z / z.std(ddof=1)
        y = 0.90 * x + np.sqrt(1 - 0.90**2) * z
        returns = pd.DataFrame({"A.SH": x, "B.SH": y}, index=dates)
        self.assertAlmostEqual(returns.corr().loc["A.SH", "B.SH"], 0.90)
        config = StrategyConfig(
            correlation_threshold=0.90,
            max_correlation_weight=0.30,
        )

        clusters = correlation_clusters(["A.SH", "B.SH"], returns, dates[-1], config)

        self.assertEqual(clusters, [{"A.SH", "B.SH"}])

    def test_correlation_requires_40_overlapping_observations(self) -> None:
        dates = pd.bdate_range("2026-01-02", periods=40)
        returns = pd.DataFrame(
            {
                "A.SH": np.arange(40, dtype=float),
                "B.SH": [np.nan, *np.arange(39, dtype=float)],
            },
            index=dates,
        )
        config = StrategyConfig(
            correlation_threshold=0.90,
            max_correlation_weight=0.30,
        )

        clusters = correlation_clusters(["A.SH", "B.SH"], returns, dates[-1], config)

        self.assertEqual(clusters, [{"A.SH"}, {"B.SH"}])

    def test_trim_plan_enforces_every_hard_capacity(self) -> None:
        positions = [
            RiskPosition("A.SH", 2000, 10.0, 9.0, "trial", 25, -0.01, "growth"),
            RiskPosition("B.SH", 2000, 10.0, 9.0, "winner", 2, 0.02, "growth"),
        ]

        plan = plan_trim_quantities(
            positions,
            equity=100_000,
            lot_size=100,
            gross_cap=0.50,
            industry_cap=0.35,
            correlation_clusters=[{"A.SH", "B.SH"}],
            correlation_cap=0.30,
            portfolio_stop_risk_cap=0.03,
            cluster_stop_risk_cap=0.015,
        )

        self.assertEqual(plan, {"A.SH": 2000, "B.SH": 500})

    def test_trim_plan_prefers_worse_trial_then_weaker_slope(self) -> None:
        positions = [
            RiskPosition("A.SH", 1000, 10.0, 9.0, "trial", 10, 0.01, "tech"),
            RiskPosition("B.SH", 1000, 10.0, 9.0, "trial", 20, 0.02, "tech"),
            RiskPosition("C.SH", 1000, 10.0, 9.0, "trial", 20, -0.01, "tech"),
            RiskPosition("D.SH", 1000, 10.0, 9.0, "winner", 30, -0.02, "tech"),
        ]

        plan = plan_trim_quantities(
            positions,
            equity=100_000,
            lot_size=100,
            gross_cap=0.25,
            industry_cap=None,
            correlation_clusters=[],
            correlation_cap=None,
            portfolio_stop_risk_cap=None,
            cluster_stop_risk_cap=None,
        )

        self.assertEqual(plan, {"C.SH": 1000, "B.SH": 500})

    def test_trim_plan_rounds_up_to_the_smallest_resolving_board_lot(self) -> None:
        positions = [RiskPosition("A.SH", 1000, 10.0, 9.0, "trial", 1, 0.01, "tech")]

        plan = plan_trim_quantities(
            positions,
            equity=100_000,
            lot_size=100,
            gross_cap=0.095,
            industry_cap=None,
            correlation_clusters=[],
            correlation_cap=None,
            portfolio_stop_risk_cap=None,
            cluster_stop_risk_cap=None,
        )

        self.assertEqual(plan, {"A.SH": 100})

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
