"""Tests for OOT evaluation config validation."""
from __future__ import annotations

import unittest

from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG, OotEvaluationConfig
from cta.portfolio_logic.config import PortfolioLogicConfig
from cta.risk.config import RiskSystemConfig


class TestOotEvaluationConfigValidation(unittest.TestCase):
    def test_accepts_default_values(self) -> None:
        cfg = OotEvaluationConfig()
        self.assertGreater(float(cfg.weekly_max_drawdown_pct), 0.0)
        self.assertGreater(float(cfg.monthly_max_drawdown_pct), 0.0)
        # 2026-05-27：从 0.03 收紧到 0.025
        self.assertAlmostEqual(float(cfg.weekly_max_drawdown_pct), 0.025, places=12)
        self.assertAlmostEqual(float(cfg.max_position_scale), 0.10, places=12)
        self.assertAlmostEqual(float(cfg.max_single_loss_pct), 0.002, places=12)
        self.assertAlmostEqual(float(cfg.max_symbol_notional_pct), 0.30, places=12)
        self.assertEqual(int(cfg.max_concurrent_positions_per_symbol), 3)
        self.assertEqual(int(cfg.max_concurrent_positions_total), 10)
        self.assertTrue(bool(cfg.enforce_stop_loss_consistency))
        self.assertTrue(bool(cfg.enforce_intrabar_bar_volume_cap))
        self.assertAlmostEqual(float(cfg.intrabar_max_bar_volume_participation_pct), 0.01, places=12)
        self.assertEqual(str(cfg.intrabar_volume_column), "volume")
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
            OotEvaluationConfig(intrabar_stop_loss_pct_by_cluster_interval={"index|bad_interval": 0.02})

    def test_mapping_fields_are_read_only_views(self) -> None:
        cfg = OotEvaluationConfig(
            intrabar_stop_loss_pct_by_cluster_interval={"index|day": 0.02},
        )
        with self.assertRaises(TypeError):
            cfg.intrabar_stop_loss_pct_by_cluster_interval["index|day"] = 0.03  # type: ignore[index]

    def test_signal_type_size_multiplier_defaults_and_normalization(self) -> None:
        default_cfg = OotEvaluationConfig()
        self.assertEqual(
            set(default_cfg.signal_type_size_multiplier.keys()),
            {
                "bull_pullback_continuation",
                "breakout_pullback_continuation",
                "cross_sectional_momentum",
                "trend_acceleration_breakout",
                "atr_breakout",
                "donchian_breakout",
                "tight_range_breakout",
            },
        )
        self.assertAlmostEqual(
            float(default_cfg.signal_type_size_multiplier["bull_pullback_continuation"]),
            4.0,
            places=12,
        )
        self.assertAlmostEqual(
            float(default_cfg.signal_type_size_multiplier["breakout_pullback_continuation"]),
            3.0,
            places=12,
        )
        self.assertAlmostEqual(
            float(default_cfg.signal_type_size_multiplier["cross_sectional_momentum"]),
            0.8,
            places=12,
        )
        self.assertAlmostEqual(
            float(default_cfg.signal_type_size_multiplier["trend_acceleration_breakout"]),
            0.5,
            places=12,
        )
        self.assertAlmostEqual(
            float(default_cfg.signal_type_size_multiplier["atr_breakout"]),
            0.2,
            places=12,
        )
        self.assertAlmostEqual(
            float(default_cfg.signal_type_size_multiplier["donchian_breakout"]),
            0.25,
            places=12,
        )
        self.assertAlmostEqual(
            float(default_cfg.signal_type_size_multiplier["tight_range_breakout"]),
            0.15,
            places=12,
        )

        cfg = OotEvaluationConfig(
            signal_type_size_multiplier={
                "  BULL_PULLBACK_CONTINUATION  ": 2.0,
                " Breakout_Pullback_Continuation ": 1.5,
                "Cross_Sectional_Momentum": 1.0,
                "trend_acceleration_breakout": 0.7,
                "Atr_Breakout": 0.4,
                "donchian_breakout": 0.5,
                "tight_range_breakout": 0.3,
            }
        )
        self.assertAlmostEqual(
            float(cfg.signal_type_size_multiplier["bull_pullback_continuation"]),
            2.0,
            places=12,
        )
        self.assertAlmostEqual(
            float(cfg.signal_type_size_multiplier["atr_breakout"]),
            0.4,
            places=12,
        )
        with self.assertRaises(TypeError):
            cfg.signal_type_size_multiplier["atr_breakout"] = 0.5  # type: ignore[index]

    def test_signal_type_size_multiplier_rejects_out_of_range_values(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(signal_type_size_multiplier={"atr_breakout": 0.0})
        with self.assertRaises(ValueError):
            OotEvaluationConfig(signal_type_size_multiplier={"atr_breakout": 5.1})

    def test_trade_filter_signal_type_delta_defaults_follow_usage_pct_tiering(self) -> None:
        # 2026-05-31 目标配比版 + 方案A：阈值 delta 做"粗分流"（pullback 强放量负向、低质类型强缩量正向），
        # signal_type_max_notional_pct 做"二次限流"（低质/肥尾类型硬性份额上限）。
        cfg = OotEvaluationConfig()
        pctl = cfg.trade_filter_percentile_threshold_delta_by_signal_type
        ranker_pctl = cfg.ranker_prob_pctl_delta_by_signal_type
        self.assertAlmostEqual(float(pctl["bull_pullback_continuation"]), -20.0, places=12)
        self.assertAlmostEqual(float(pctl["breakout_pullback_continuation"]), -16.0, places=12)
        self.assertAlmostEqual(float(pctl["atr_breakout"]), 30.0, places=12)
        self.assertAlmostEqual(float(pctl["donchian_breakout"]), 25.0, places=12)
        self.assertAlmostEqual(float(pctl["tight_range_breakout"]), 35.0, places=12)
        self.assertAlmostEqual(float(ranker_pctl["bull_pullback_continuation"]), -20.0, places=12)
        self.assertAlmostEqual(float(ranker_pctl["breakout_pullback_continuation"]), -10.0, places=12)
        self.assertAlmostEqual(float(ranker_pctl["atr_breakout"]), 30.0, places=12)
        self.assertAlmostEqual(float(ranker_pctl["donchian_breakout"]), 20.0, places=12)
        # 负向放宽核心 alpha、正向缩量低质类型的方向性
        self.assertLess(float(pctl["bull_pullback_continuation"]), 0.0)
        self.assertGreater(float(pctl["atr_breakout"]), 0.0)
        self.assertLess(float(ranker_pctl["bull_pullback_continuation"]), 0.0)
        self.assertGreater(float(ranker_pctl["atr_breakout"]), 0.0)
        # 二次限流：硬性 notional 份额上限——低质/肥尾类型给低份额，核心 pullback 不设
        self.assertAlmostEqual(cfg.resolve_signal_type_max_notional_pct("cross_sectional_momentum"), 0.10, places=12)
        self.assertAlmostEqual(cfg.resolve_signal_type_max_notional_pct("atr_breakout"), 0.08, places=12)
        self.assertAlmostEqual(cfg.resolve_signal_type_max_notional_pct("donchian_breakout"), 0.06, places=12)
        self.assertAlmostEqual(cfg.resolve_signal_type_max_notional_pct("trend_acceleration_breakout"), 0.05, places=12)
        self.assertAlmostEqual(cfg.resolve_signal_type_max_notional_pct("tight_range_breakout"), 0.04, places=12)
        self.assertIsNone(cfg.resolve_signal_type_max_notional_pct("bull_pullback_continuation"))

    def test_signal_type_blacklist_defaults_and_normalization(self) -> None:
        default_cfg = OotEvaluationConfig()
        self.assertEqual(
            tuple(default_cfg.signal_type_blacklist),
            (
                "atr_breakout",
                "donchian_breakout",
                "trend_acceleration_breakout",
                "bull_volatility_contraction_breakout",
            ),
        )
        cfg = OotEvaluationConfig(
            signal_type_blacklist=(
                " ATR_BREAKOUT ",
                "trend_acceleration_breakout",
                "",
                "atr_breakout",
            )
        )
        self.assertEqual(
            tuple(cfg.signal_type_blacklist),
            ("atr_breakout", "trend_acceleration_breakout"),
        )
        self.assertTrue(cfg.is_signal_type_blacklisted("Atr_Breakout"))
        self.assertTrue(default_cfg.is_signal_type_blacklisted("BULL_VOLATILITY_CONTRACTION_BREAKOUT"))
        self.assertFalse(cfg.is_signal_type_blacklisted("bull_pullback_continuation"))

    def test_trade_filter_signal_type_delta_defaults_and_normalization(self) -> None:
        cfg = OotEvaluationConfig(
            trade_filter_percentile_threshold_delta_by_signal_type={
                "  Bull_Pullback_Continuation ": -5.0,
                "trend_acceleration_breakout": 5.0,
            },
            trade_filter_raw_threshold_delta_by_signal_type={
                " Breakout_Pullback_Continuation ": -0.02,
            },
            ranker_prob_pctl_delta_by_signal_type={
                " ATR_Breakout ": 20.0,
                "Bull_Pullback_Continuation": -15.0,
            },
        )
        self.assertAlmostEqual(
            float(cfg.trade_filter_percentile_threshold_delta_by_signal_type["bull_pullback_continuation"]),
            -5.0,
            places=12,
        )
        self.assertAlmostEqual(
            float(cfg.trade_filter_percentile_threshold_delta_by_signal_type["trend_acceleration_breakout"]),
            5.0,
            places=12,
        )
        self.assertAlmostEqual(
            float(cfg.trade_filter_raw_threshold_delta_by_signal_type["breakout_pullback_continuation"]),
            -0.02,
            places=12,
        )
        self.assertAlmostEqual(float(cfg.resolve_ranker_prob_pctl_delta("atr_breakout")), 20.0, places=12)
        self.assertAlmostEqual(
            float(cfg.resolve_ranker_prob_pctl_delta("bull_pullback_continuation")),
            -15.0,
            places=12,
        )
        with self.assertRaises(TypeError):
            cfg.trade_filter_percentile_threshold_delta_by_signal_type["bull_pullback_continuation"] = -3.0  # type: ignore[index]
        with self.assertRaises(TypeError):
            cfg.ranker_prob_pctl_delta_by_signal_type["atr_breakout"] = 5.0  # type: ignore[index]

    def test_trade_filter_signal_type_delta_rejects_out_of_range_values(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(
                trade_filter_percentile_threshold_delta_by_signal_type={"bull_pullback_continuation": -120.0}
            )
        with self.assertRaises(ValueError):
            OotEvaluationConfig(
                trade_filter_raw_threshold_delta_by_signal_type={"bull_pullback_continuation": 1.2}
            )
        with self.assertRaises(ValueError):
            OotEvaluationConfig(
                ranker_prob_pctl_delta_by_signal_type={"bull_pullback_continuation": -120.0}
            )

    def test_signal_type_max_notional_pct_normalization_and_validation(self) -> None:
        cfg = OotEvaluationConfig(
            signal_type_max_notional_pct={"  ATR_Breakout ": 0.3, "donchian_breakout": 0.2}
        )
        self.assertAlmostEqual(cfg.resolve_signal_type_max_notional_pct("atr_breakout"), 0.3, places=12)
        self.assertIsNone(cfg.resolve_signal_type_max_notional_pct("bull_pullback_continuation"))
        with self.assertRaises(TypeError):
            cfg.signal_type_max_notional_pct["atr_breakout"] = 0.5  # type: ignore[index]
        with self.assertRaises(ValueError):
            OotEvaluationConfig(signal_type_max_notional_pct={"atr_breakout": 0.0})
        with self.assertRaises(ValueError):
            OotEvaluationConfig(signal_type_max_notional_pct={"atr_breakout": 1.5})

    def test_signal_type_max_concurrent_defaults_and_normalization(self) -> None:
        default_cfg = OotEvaluationConfig()
        self.assertEqual(
            dict(default_cfg.signal_type_max_concurrent_positions),
            {
                "bull_pullback_continuation": 10,
                "breakout_pullback_continuation": 8,
                "cross_sectional_momentum": 2,
                "trend_acceleration_breakout": 1,
                "atr_breakout": 1,
                "donchian_breakout": 1,
                "tight_range_breakout": 1,
            },
        )

        cfg = OotEvaluationConfig(
            signal_type_max_concurrent_positions={
                "  ATR_BREAKOUT ": 3,
                "Bull_Pullback_Continuation": 6,
            }
        )
        self.assertEqual(int(cfg.signal_type_max_concurrent_positions["atr_breakout"]), 3)
        self.assertEqual(int(cfg.signal_type_max_concurrent_positions["bull_pullback_continuation"]), 6)
        with self.assertRaises(TypeError):
            cfg.signal_type_max_concurrent_positions["atr_breakout"] = 2  # type: ignore[index]

    def test_signal_type_max_concurrent_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            OotEvaluationConfig(signal_type_max_concurrent_positions={"atr_breakout": 0})

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
        with self.assertRaises(ValueError):
            OotEvaluationConfig(intrabar_max_bar_volume_participation_pct=-0.1)
        with self.assertRaises(ValueError):
            OotEvaluationConfig(intrabar_max_bar_volume_participation_pct=1.2)
        with self.assertRaises(ValueError):
            OotEvaluationConfig(intrabar_volume_column="  ")

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

    def test_accepts_optional_risk_system(self) -> None:
        cfg = OotEvaluationConfig(
            risk_system=RiskSystemConfig(enable_quantile_threshold=False),
        )
        self.assertIsNotNone(cfg.risk_system)
        assert cfg.risk_system is not None
        self.assertFalse(bool(cfg.risk_system.enable_quantile_threshold))

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
