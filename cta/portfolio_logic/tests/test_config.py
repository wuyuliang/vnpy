"""Unit tests for portfolio logic configuration."""
from __future__ import annotations

import dataclasses
import unittest

from cta.portfolio_logic.config import (
    CapsConfig,
    IntervalGateConfig,
    OpportunityRankerConfig,
    PortfolioLogicConfig,
)


class TestPortfolioLogicConfig(unittest.TestCase):
    def test_interval_gate_config_rejects_invalid_fallback(self) -> None:
        with self.assertRaises(ValueError):
            IntervalGateConfig(fallback_when_htf_missing="invalid")

    def test_interval_gate_config_rejects_invalid_interval_weight(self) -> None:
        with self.assertRaises(ValueError):
            IntervalGateConfig(interval_rank={"day": 1.0, "60min": 0.0})

    def test_interval_gate_config_is_frozen(self) -> None:
        cfg = IntervalGateConfig()
        with self.assertRaises(dataclasses.FrozenInstanceError):
            cfg.htf_intervals = ("day",)  # type: ignore[misc]

    def test_opportunity_ranker_weights_must_sum_to_one(self) -> None:
        with self.assertRaises(ValueError):
            OpportunityRankerConfig(w_prob=0.5, w_edge=0.3, w_rank=0.2, w_align=0.2)
        with self.assertRaises(ValueError):
            OpportunityRankerConfig(base_notional_pct=0.0)

    def test_caps_config_validates_notional_hierarchy(self) -> None:
        with self.assertRaises(ValueError):
            CapsConfig(
                max_symbol_notional_pct=0.4,
                max_cluster_notional_pct=0.3,
                max_total_notional_pct=1.5,
            )
        with self.assertRaises(ValueError):
            CapsConfig(
                max_symbol_notional_pct=0.3,
                max_cluster_notional_pct=1.6,
                max_total_notional_pct=1.5,
            )

    def test_portfolio_logic_config_requires_trailing_when_pyramid_enabled(self) -> None:
        with self.assertRaises(ValueError):
            PortfolioLogicConfig(enable_pyramid=True, enable_trailing=False)

    def test_portfolio_logic_config_requires_calibration_when_throttle_enabled(self) -> None:
        with self.assertRaises(ValueError):
            PortfolioLogicConfig(enable_risk_throttle=True, enable_score_calibration=False)


if __name__ == "__main__":
    unittest.main()
