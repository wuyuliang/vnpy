import unittest

from stock.etf.config import StrategyConfig
from stock.etf.portfolio import (
    Portfolio,
    Position,
    PositionState,
    calculate_order_quantity,
)


class PortfolioTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = StrategyConfig(
            initial_capital=100_000,
            commission_rate=0,
            min_commission=0,
            slippage_rate=0,
        )

    def test_quantity_uses_smallest_limit_and_rounds_to_lot(self) -> None:
        quantity = calculate_order_quantity(
            equity=100_000,
            available_cash=100_000,
            estimated_fill_price=10,
            risk_atr=0.5,
            config=self.config,
        )

        self.assertEqual(quantity, 1000)  # risk limit: 1000 / (2 * 0.5)

    def test_quantity_respects_twenty_percent_weight_cap(self) -> None:
        quantity = calculate_order_quantity(
            equity=100_000,
            available_cash=100_000,
            estimated_fill_price=10,
            risk_atr=0.1,
            config=self.config,
        )

        self.assertEqual(quantity, 2000)

    def test_quantity_respects_additional_notional_cap(self) -> None:
        quantity = calculate_order_quantity(
            equity=100_000,
            available_cash=100_000,
            estimated_fill_price=10,
            risk_atr=0.01,
            config=self.config,
            max_additional_notional=10_000,
        )

        self.assertEqual(quantity, 1000)

    def test_quantity_returns_zero_when_notional_cap_is_below_one_lot(self) -> None:
        quantity = calculate_order_quantity(
            equity=100_000,
            available_cash=100_000,
            estimated_fill_price=10,
            risk_atr=0.01,
            config=self.config,
            max_additional_notional=999,
        )

        self.assertEqual(quantity, 0)

    def test_quantity_scales_risk_budget_for_caution_entries(self) -> None:
        quantity = calculate_order_quantity(
            equity=100_000,
            available_cash=100_000,
            estimated_fill_price=10,
            risk_atr=0.5,
            config=self.config,
            risk_fraction=0.5,
        )

        self.assertEqual(quantity, 500)

    def test_industry_weight_must_be_a_positive_fraction(self) -> None:
        with self.assertRaises(ValueError):
            StrategyConfig(max_industry_weight=0)
        with self.assertRaises(ValueError):
            StrategyConfig(max_industry_weight=1.01)

    def test_strategy_periods_allow_only_five_or_ten_days(self) -> None:
        config = StrategyConfig()

        self.assertEqual(config.atr_period, 5)
        self.assertEqual(config.adx_period, 5)
        self.assertEqual(StrategyConfig(atr_period=10).atr_period, 10)
        self.assertEqual(StrategyConfig(adx_period=10).adx_period, 10)
        with self.assertRaises(ValueError):
            StrategyConfig(atr_period=14)
        with self.assertRaises(ValueError):
            StrategyConfig(adx_period=14)

    def test_entry_and_correlation_controls_are_validated(self) -> None:
        with self.assertRaises(ValueError):
            StrategyConfig(entry_confirmation_days=0)
        with self.assertRaises(ValueError):
            StrategyConfig(max_entry_gap_atr=0)
        with self.assertRaises(ValueError):
            StrategyConfig(correlation_lookback=39, min_correlation_observations=40)
        with self.assertRaises(ValueError):
            StrategyConfig(correlation_threshold=1.01)
        with self.assertRaises(ValueError):
            StrategyConfig(max_correlation_weight=0)

    def test_winner_control_parameters_are_validated(self) -> None:
        config = StrategyConfig(
            winner_holding_enabled=True,
            market_state_enabled=True,
            dynamic_risk_enabled=True,
            atr_stop_multiple=3.0,
            exit_rank=20,
            exit_rank_confirmation_days=3,
            winner_promotion_atr_multiple=2.0,
            max_portfolio_stop_risk=0.03,
            max_cluster_stop_risk=0.015,
            caution_max_gross_weight=0.50,
            caution_entry_rank=3,
            caution_risk_fraction=0.50,
            risk_off_breadth_threshold=0.35,
            market_state_confirmation_days=2,
        )

        self.assertEqual(config.exit_rank, 20)
        with self.assertRaises(ValueError):
            StrategyConfig(caution_risk_fraction=0)
        with self.assertRaises(ValueError):
            StrategyConfig(
                max_cluster_stop_risk=0.04,
                max_portfolio_stop_risk=0.03,
            )

    def test_partial_sell_preserves_position_and_allocates_entry_cost(self) -> None:
        config = StrategyConfig(
            initial_capital=100_000,
            commission_rate=0.001,
            min_commission=0,
            slippage_rate=0,
        )
        portfolio = Portfolio(100_000, config)
        portfolio.buy(
            "A.SH",
            "2026-01-02",
            10.0,
            1000,
            risk_atr=0.5,
            reason="entry",
        )

        trade = portfolio.sell_quantity(
            "A.SH",
            "2026-01-03",
            11.0,
            400,
            "portfolio_trim",
        )

        self.assertEqual(trade.quantity, 400)
        self.assertAlmostEqual(trade.realized_pnl, 391.6)
        self.assertEqual(portfolio.positions["A.SH"].quantity, 600)
        self.assertAlmostEqual(portfolio.positions["A.SH"].entry_commission, 6.0)

    def test_increase_preserves_state_and_stop_while_updating_cost(self) -> None:
        portfolio = Portfolio(100_000, self.config)
        portfolio.buy(
            "A.SH",
            "2026-01-02",
            10.0,
            1000,
            risk_atr=0.5,
            reason="entry",
        )
        position = portfolio.positions["A.SH"]
        position.state = "winner"
        position.highest_close = 12.5
        position.stop_price = 11.0

        trade = portfolio.increase(
            "A.SH",
            "2026-01-03",
            12.0,
            500,
            reason="portfolio_restore",
        )

        restored = portfolio.positions["A.SH"]
        self.assertEqual(trade.quantity, 500)
        self.assertEqual(restored.quantity, 1500)
        self.assertAlmostEqual(restored.average_price, 32 / 3)
        self.assertEqual(restored.state, "winner")
        self.assertEqual(restored.highest_close, 12.5)
        self.assertEqual(restored.stop_price, 11.0)

    def test_buy_sets_fixed_stop_and_sell_updates_cash(self) -> None:
        portfolio = Portfolio(100_000, self.config)

        buy = portfolio.buy(
            "A.SH",
            "2026-01-02",
            10.0,
            1000,
            risk_atr=0.5,
            reason="entry",
        )
        self.assertEqual(portfolio.positions["A.SH"].stop_price, 9.0)
        sell = portfolio.sell("A.SH", "2026-01-03", 11.0, reason="rs_out_top10")

        self.assertEqual(buy.quantity, 1000)
        self.assertEqual(buy.fill_price, 10.0)
        self.assertEqual(sell.realized_pnl, 1000.0)
        self.assertEqual(portfolio.cash, 101_000.0)
        self.assertNotIn("A.SH", portfolio.positions)

    def test_stop_uses_open_for_gap_and_stop_price_for_intraday(self) -> None:
        portfolio = Portfolio(100_000, self.config)
        portfolio.positions["A.SH"] = Position("A.SH", 1000, 10.0, 9.0, 0.0)

        gap = portfolio.check_stop("A.SH", "2026-01-03", open_price=8.5, low_price=8.0)

        self.assertIsNotNone(gap)
        assert gap is not None
        self.assertEqual(gap.raw_price, 8.5)
        self.assertEqual(gap.primary_reason, "atr_stop_gap")

        portfolio.positions["B.SH"] = Position("B.SH", 1000, 10.0, 9.0, 0.0)
        intraday = portfolio.check_stop(
            "B.SH", "2026-01-03", open_price=9.5, low_price=8.9
        )

        self.assertIsNotNone(intraday)
        assert intraday is not None
        self.assertEqual(intraday.raw_price, 9.0)
        self.assertEqual(intraday.primary_reason, "atr_stop_intraday")

    def test_gap_and_intraday_stop_checks_are_independently_callable(self) -> None:
        portfolio = Portfolio(100_000, self.config)
        portfolio.buy("A.SH", "2026-01-02", 10.0, 1000, 0.5, "entry")

        self.assertIsNone(
            portfolio.check_gap_stop("A.SH", "2026-01-03", open_price=9.5)
        )
        intraday = portfolio.check_intraday_stop("A.SH", "2026-01-03", low_price=8.9)

        self.assertIsNotNone(intraday)
        assert intraday is not None
        self.assertEqual(intraday.primary_reason, "atr_stop_intraday")

    def test_winner_promotion_and_trailing_stop_are_monotonic(self) -> None:
        config = StrategyConfig(
            initial_capital=100_000,
            commission_rate=0,
            min_commission=0,
            slippage_rate=0,
            atr_stop_multiple=3,
            winner_holding_enabled=True,
        )
        portfolio = Portfolio(100_000, config)
        portfolio.buy(
            "A.SH",
            "2026-01-02",
            10.0,
            1000,
            risk_atr=1.0,
            reason="entry",
        )

        transition = portfolio.update_after_close(
            "A.SH",
            close=12.1,
            ema10=11.5,
            ema20=11.0,
            atr5=1.0,
        )
        first_stop = portfolio.positions["A.SH"].stop_price
        portfolio.update_after_close(
            "A.SH",
            close=11.8,
            ema10=11.6,
            ema20=11.1,
            atr5=2.0,
        )

        position = portfolio.positions["A.SH"]
        self.assertEqual(transition, "winner_promoted")
        self.assertEqual(position.state, PositionState.WINNER)
        self.assertAlmostEqual(first_stop, 9.1)
        self.assertEqual(position.stop_price, first_stop)


if __name__ == "__main__":
    unittest.main()
