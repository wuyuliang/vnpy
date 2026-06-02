"""P2.1: BaselineSuiteConfig / StrategyConfig / BacktestConfig 值域校验。"""
from __future__ import annotations

import unittest

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.config.baseline_skill_suite_config import BaselineSuiteConfig
from cta.config.skill_tight_range_breakout_config import BacktestConfig, StrategyConfig


class TestBaselineSuiteConfigValidation(unittest.TestCase):
    def test_accepts_default(self) -> None:
        cfg = BaselineSuiteConfig()
        self.assertGreater(float(cfg.initial_capital), 0.0)
        self.assertEqual(cfg.trade_side_mode, "both")

    def test_default_initial_capital_matches_deployable_scale(self) -> None:
        self.assertEqual(float(BaselineSuiteConfig().initial_capital), 10_000_000.0)

    def test_rejects_unknown_side_mode(self) -> None:
        with self.assertRaises(ValueError):
            BaselineSuiteConfig(trade_side_mode="upside")

    def test_rejects_non_positive_capital(self) -> None:
        with self.assertRaises(ValueError):
            BaselineSuiteConfig(initial_capital=0.0)
        with self.assertRaises(ValueError):
            BaselineSuiteConfig(initial_capital=-1.0)

    def test_rejects_empty_signal_types(self) -> None:
        with self.assertRaises(ValueError):
            BaselineSuiteConfig(signal_types=())

    def test_rejects_inverted_date_range(self) -> None:
        with self.assertRaises(ValueError):
            BaselineSuiteConfig(start_date="2025-01-01", end_date="2020-01-01")

    def test_rejects_empty_symbol(self) -> None:
        with self.assertRaises(ValueError):
            BaselineSuiteConfig(symbol="")


class TestStrategyConfigValidation(unittest.TestCase):
    def test_accepts_default(self) -> None:
        cfg = StrategyConfig()
        self.assertGreater(float(cfg.risk_per_trade_pct), 0.0)

    def test_rejects_risk_per_trade_typo(self) -> None:
        # 典型 typo：少打一个 0，从 0.005 变成 0.05；正好在边界外（>5%）。
        # 这条校验是 P2.1 加进来的主要价值之一。
        with self.assertRaises(ValueError):
            StrategyConfig(risk_per_trade_pct=0.5)  # 50% 单笔 → 必拦
        with self.assertRaises(ValueError):
            StrategyConfig(risk_per_trade_pct=0.0)  # 0 也不合法

    def test_rejects_non_positive_lookback_or_lots(self) -> None:
        with self.assertRaises(ValueError):
            StrategyConfig(lookback=0)
        with self.assertRaises(ValueError):
            StrategyConfig(lots=0)
        with self.assertRaises(ValueError):
            StrategyConfig(max_holding_bars=0)

    def test_rejects_breakout_score_out_of_range(self) -> None:
        with self.assertRaises(ValueError):
            StrategyConfig(min_breakout_score=1.5)
        with self.assertRaises(ValueError):
            StrategyConfig(min_breakout_score=-0.1)

    def test_rejects_non_positive_atr_multiplier(self) -> None:
        with self.assertRaises(ValueError):
            StrategyConfig(initial_stop_atr_mult=0.0)
        with self.assertRaises(ValueError):
            StrategyConfig(trailing_stop_atr_mult=-1.0)


class TestBacktestConfigValidation(unittest.TestCase):
    def test_accepts_default(self) -> None:
        cfg = BacktestConfig()
        self.assertGreater(float(cfg.initial_capital), 0.0)
        self.assertGreater(int(cfg.periods_per_year), 0)

    def test_default_initial_capital_matches_deployable_scale(self) -> None:
        self.assertEqual(float(BacktestConfig().initial_capital), 10_000_000.0)

    def test_rejects_invalid_values(self) -> None:
        with self.assertRaises(ValueError):
            BacktestConfig(initial_capital=0.0)
        with self.assertRaises(ValueError):
            BacktestConfig(periods_per_year=0)
        with self.assertRaises(ValueError):
            BacktestConfig(interval="")


class TestOotEvaluationConfigCapital(unittest.TestCase):
    def test_default_initial_capital_matches_deployable_scale(self) -> None:
        self.assertEqual(float(OotEvaluationConfig().initial_capital), 10_000_000.0)


if __name__ == "__main__":
    unittest.main()
