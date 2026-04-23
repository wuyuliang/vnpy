"""transition_risk.py tests."""
from __future__ import annotations

import unittest

from cta.skills.regime_switch.transition_risk import (
    RiskAdjustment,
    enforce_transition_max_hold,
    transition_risk_adjustment,
)


class TestTransitionRisk(unittest.TestCase):
    def test_transition_label_reduces_risk(self) -> None:
        adj = transition_risk_adjustment(
            regime_label="transition",
            regime_age=1,
            portfolio_transition_ratio=0.0,
        )
        self.assertIsInstance(adj, RiskAdjustment)
        self.assertLess(adj.size_multiplier, 1.0)
        self.assertFalse(adj.allow_new_entry)
        self.assertFalse(adj.allow_add_on)
        self.assertEqual(adj.max_hold_bars, 10)

    def test_early_age_also_protected(self) -> None:
        adj = transition_risk_adjustment(
            regime_label="trend_up",
            regime_age=2,
            portfolio_transition_ratio=0.0,
        )
        self.assertLess(adj.size_multiplier, 1.0)
        self.assertFalse(adj.allow_new_entry)

    def test_portfolio_ratio_applies_extra_scaling(self) -> None:
        adj = transition_risk_adjustment(
            regime_label="trend_up",
            regime_age=20,
            portfolio_transition_ratio=0.35,
        )
        self.assertLess(adj.size_multiplier, 1.0)
        self.assertTrue(adj.allow_new_entry)

    def test_enforce_transition_max_hold(self) -> None:
        adj = RiskAdjustment(
            size_multiplier=0.5,
            stop_multiplier=0.7,
            allow_new_entry=False,
            allow_add_on=False,
            max_hold_bars=10,
        )
        self.assertFalse(enforce_transition_max_hold(position_bars=9, adj=adj))
        self.assertTrue(enforce_transition_max_hold(position_bars=11, adj=adj))


if __name__ == "__main__":
    unittest.main()

