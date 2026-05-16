"""Tests for OOT evaluation config validation."""
from __future__ import annotations

import unittest

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.portfolio_logic.config import PortfolioLogicConfig


class TestOotEvaluationConfigValidation(unittest.TestCase):
    def test_accepts_default_values(self) -> None:
        cfg = OotEvaluationConfig()
        self.assertGreater(float(cfg.weekly_max_drawdown_pct), 0.0)
        self.assertGreater(float(cfg.monthly_max_drawdown_pct), 0.0)
        self.assertAlmostEqual(float(cfg.weekly_max_drawdown_pct), 0.03, places=12)

    def test_rejects_out_of_range_risk_values(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(risk_per_trade_pct=1.2)
        with self.assertRaises(ValueError):
            OotEvaluationConfig(max_single_loss_pct=-0.01)
        with self.assertRaises(ValueError):
            OotEvaluationConfig(weekly_max_drawdown_pct=1.2)
        with self.assertRaises(ValueError):
            OotEvaluationConfig(monthly_max_drawdown_pct=1.2)
        with self.assertRaises(ValueError):
            OotEvaluationConfig(weekly_dd_position_scale_after_breach=1.5)

    def test_accepts_portfolio_logic_config(self) -> None:
        cfg = OotEvaluationConfig(portfolio_logic=PortfolioLogicConfig(enable_htf_gate=True))
        self.assertTrue(cfg.portfolio_logic.enable_htf_gate)

    def test_rejects_invalid_portfolio_logic_dependency(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(
                portfolio_logic=PortfolioLogicConfig(
                    enable_pyramid=True,
                    enable_trailing=False,
                )
            )

    def test_stop_loss_consistency_guard_is_opt_in_for_custom_eval(self) -> None:
        # 研究/单测场景：允许临时偏离 label stop loss，不强制一致性。
        cfg = OotEvaluationConfig(
            use_intrabar_stop_tracking=True,
            intrabar_stop_loss_pct=0.001,
            enforce_stop_loss_consistency=False,
        )
        self.assertAlmostEqual(float(cfg.intrabar_stop_loss_pct), 0.001, places=12)

        # 生产 guard：显式开启后，偏离默认 label stop loss 会抛错。
        with self.assertRaises(ValueError):
            OotEvaluationConfig(
                use_intrabar_stop_tracking=True,
                intrabar_stop_loss_pct=0.001,
                enforce_stop_loss_consistency=True,
            )


if __name__ == "__main__":
    unittest.main()
