import unittest

import pandas as pd

from stock.etf.experiments import _selection_audit
from stock.etf.run_winner_holding_experiments import build_experiment_configs


class WinnerExperimentTests(unittest.TestCase):
    def test_staged_configs_are_fixed_and_cost_stress_only_doubles_costs(self) -> None:
        configs = build_experiment_configs()

        self.assertEqual(
            set(configs),
            {
                "clean_baseline",
                "risk_controls_only",
                "winner_holding_only",
                "market_states_only",
                "combined",
                "combined_cost2x",
            },
        )
        combined = configs["combined"]
        self.assertEqual(combined.atr_period, 5)
        self.assertEqual(combined.adx_period, 10)
        self.assertEqual(combined.atr_stop_multiple, 3.0)
        self.assertEqual(combined.exit_rank, 20)
        self.assertEqual(combined.exit_rank_confirmation_days, 3)
        self.assertEqual(combined.max_industry_weight, 0.35)
        self.assertEqual(combined.correlation_threshold, 0.90)
        self.assertEqual(combined.max_correlation_weight, 0.30)
        self.assertEqual(combined.max_portfolio_stop_risk, 0.03)
        self.assertEqual(combined.max_cluster_stop_risk, 0.015)
        self.assertTrue(combined.winner_holding_enabled)
        self.assertTrue(combined.market_state_enabled)
        self.assertTrue(combined.dynamic_risk_enabled)
        stressed = configs["combined_cost2x"]
        differing_fields = {
            name
            for name in combined.__dataclass_fields__
            if getattr(combined, name) != getattr(stressed, name)
        }
        self.assertEqual(differing_fields, {"commission_rate", "slippage_rate"})
        self.assertEqual(stressed.commission_rate, combined.commission_rate * 2)
        self.assertEqual(stressed.slippage_rate, combined.slippage_rate * 2)

    def test_six_approved_gates_all_pass_at_the_boundaries(self) -> None:
        audit = self._audit()

        expected = {
            "max_drawdown_at_least_minus_20": True,
            "annual_one_way_turnover_at_most_8": True,
            "annual_return_at_least_4_percent": True,
            "sharpe_at_least_point_50": True,
            "median_bull_capture_at_least_50_percent": True,
            "double_cost_annual_return_positive": True,
            "all_numeric_gates_pass": True,
        }
        self.assertEqual({key: audit[key] for key in expected}, expected)

    def test_each_approved_gate_can_fail_independently(self) -> None:
        cases = [
            ("max_drawdown", -0.2001, "max_drawdown_at_least_minus_20"),
            (
                "annual_one_way_turnover",
                8.0001,
                "annual_one_way_turnover_at_most_8",
            ),
            ("annual_return", 0.0399, "annual_return_at_least_4_percent"),
            ("sharpe", 0.4999, "sharpe_at_least_point_50"),
            (
                "median_bull_capture_ratio",
                0.4999,
                "median_bull_capture_at_least_50_percent",
            ),
        ]
        for field, value, gate in cases:
            with self.subTest(gate=gate):
                audit = self._audit(candidate_overrides={field: value})
                self.assertFalse(audit[gate])
                self.assertFalse(audit["all_numeric_gates_pass"])

        no_episodes = self._audit(candidate_overrides={"bull_episode_count": 0})
        self.assertFalse(no_episodes["median_bull_capture_at_least_50_percent"])
        double_cost = self._audit(cost_overrides={"annual_return": 0.0})
        self.assertFalse(double_cost["double_cost_annual_return_positive"])
        self.assertFalse(double_cost["all_numeric_gates_pass"])

    @staticmethod
    def _audit(
        *,
        candidate_overrides: dict | None = None,
        cost_overrides: dict | None = None,
    ) -> dict:
        candidate = {
            "run_id": "combined",
            "max_drawdown": -0.20,
            "annual_one_way_turnover": 8.0,
            "annual_return": 0.04,
            "sharpe": 0.50,
            "bull_episode_count": 1,
            "median_bull_capture_ratio": 0.50,
            "largest_positive_industry": "metals",
            "largest_positive_industry_contribution_share": 0.6,
            "largest_winning_trade_share": 0.4,
        }
        candidate.update(candidate_overrides or {})
        stressed = {**candidate, "run_id": "combined_cost2x", "annual_return": 0.01}
        stressed.update(cost_overrides or {})
        baseline = {**candidate, "run_id": "clean_baseline"}
        summary = pd.DataFrame([baseline, candidate, stressed])
        annual = pd.DataFrame(
            {
                "run_id": ["clean_baseline", "combined", "combined_cost2x"],
                "year": [2025, 2025, 2025],
                "net_return": [0.03, 0.04, 0.01],
            }
        )
        return _selection_audit(
            summary,
            annual,
            "clean_baseline",
            "combined",
            "combined_cost2x",
        )


if __name__ == "__main__":
    unittest.main()
