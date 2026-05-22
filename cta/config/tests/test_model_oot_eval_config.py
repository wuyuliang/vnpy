"""Tests for OOT evaluation config validation."""
from __future__ import annotations

import unittest

from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG, OotEvaluationConfig
from cta.portfolio_logic.config import PortfolioLogicConfig


class TestOotEvaluationConfigValidation(unittest.TestCase):
    def test_accepts_default_values(self) -> None:
        cfg = OotEvaluationConfig()
        self.assertGreater(float(cfg.weekly_max_drawdown_pct), 0.0)
        self.assertGreater(float(cfg.monthly_max_drawdown_pct), 0.0)
        self.assertAlmostEqual(float(cfg.weekly_max_drawdown_pct), 0.03, places=12)
        self.assertTrue(bool(cfg.enforce_stop_loss_consistency))
        self.assertEqual(str(cfg.ma_cross_alignment_column), "generic_ma_alignment")
        self.assertFalse(bool(cfg.portfolio_logic.horizon_extend.use_model_recommendation))
        self.assertFalse(bool(cfg.portfolio_logic.pyramid.apply_model_size_multiplier))

    def test_production_default_uses_cluster_interval_trade_filter_percentile(self) -> None:
        self.assertEqual(str(DEFAULT_OOT_EVAL_CONFIG.trade_filter_gate_mode), "cluster_interval_percentile")
        self.assertAlmostEqual(float(DEFAULT_OOT_EVAL_CONFIG.trade_filter_percentile_threshold), 70.0, places=12)

    def test_trade_filter_cluster_interval_threshold_validation(self) -> None:
        cfg = OotEvaluationConfig(
            trade_filter_gate_mode="raw",
            trade_filter_raw_threshold_by_cluster_interval={"index|day": 0.45},
            trade_filter_percentile_threshold_by_cluster_interval={"index|day": 65.0},
        )
        self.assertEqual(float(cfg.trade_filter_raw_threshold_by_cluster_interval["index|day"]), 0.45)
        with self.assertRaises(ValueError):
            OotEvaluationConfig(trade_filter_gate_mode="bad_mode")
        with self.assertRaises(ValueError):
            OotEvaluationConfig(trade_filter_raw_threshold_by_cluster_interval={"index|day": 1.2})
        with self.assertRaises(ValueError):
            OotEvaluationConfig(trade_filter_percentile_threshold_by_cluster_interval={"index|day": 120.0})

    def test_post_init_rejects_unknown_cluster_or_interval_in_cluster_interval_mappings(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(ma_cross_enabled_by_cluster_interval={"indxe|day": True})
        with self.assertRaises(ValueError):
            OotEvaluationConfig(ma_cross_enabled_by_cluster_interval={"index|bad_interval": True})
        with self.assertRaises(ValueError):
            OotEvaluationConfig(regime_short_filter_enabled_by_cluster_interval={"index|bad_interval": True})
        with self.assertRaises(ValueError):
            OotEvaluationConfig(intrabar_stop_loss_pct_by_cluster_interval={"index|bad_interval": 0.02})

    def test_mapping_fields_are_read_only_views(self) -> None:
        cfg = OotEvaluationConfig(
            ma_cross_enabled_by_cluster_interval={"index|day": True},
            regime_short_filter_enabled_by_cluster_interval={"index|day": True},
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        with self.assertRaises(TypeError):
            cfg.ma_cross_enabled_by_cluster_interval["index|day"] = False  # type: ignore[index]
        with self.assertRaises(TypeError):
            cfg.regime_short_filter_enabled_by_cluster_interval["index|day"] = False  # type: ignore[index]
        with self.assertRaises(TypeError):
            cfg.intrabar_stop_loss_pct_by_cluster_interval["index|day"] = 0.03  # type: ignore[index]

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
