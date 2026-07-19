import unittest

import numpy as np
import pandas as pd

from stock.etf.ema5_open_strategy import (
    Ema5OpenConfig,
    build_ema5_open_signals,
    calculate_full_position_quantity,
    prepare_symbol_bars,
    run_ema5_open_backtest,
)


class Ema5OpenStrategyTests(unittest.TestCase):
    @staticmethod
    def _bars() -> pd.DataFrame:
        dates = pd.bdate_range("2026-01-02", periods=7)
        return pd.DataFrame(
            {
                "symbol": ["159915.SZ"] * 7,
                "datetime": dates,
                "open": [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 14.0],
                "high": [10.2, 10.2, 10.2, 10.2, 10.2, 20.2, 20.2],
                "low": [9.8, 9.8, 9.8, 9.8, 9.8, 9.8, 13.8],
                "close": [10.0, 10.0, 10.0, 10.0, 10.0, 20.0, 20.0],
                "volume": [1_000.0] * 7,
            }
        )

    @staticmethod
    def _round_trip_bars() -> pd.DataFrame:
        dates = pd.bdate_range("2026-01-02", periods=8)
        opens = [10.0, 10.0, 10.0, 10.0, 10.0, 11.0, 9.0, 12.0]
        closes = [10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 10.0, 13.0]
        return pd.DataFrame(
            {
                "symbol": ["159915.SZ"] * 8,
                "datetime": dates,
                "open": opens,
                "high": [
                    max(open_, close) + 0.2
                    for open_, close in zip(opens, closes, strict=True)
                ],
                "low": [
                    min(open_, close) - 0.2
                    for open_, close in zip(opens, closes, strict=True)
                ],
                "close": closes,
                "volume": [1_000.0] * 8,
            }
        )

    def test_signal_uses_previous_close_ema5_without_lookahead(self) -> None:
        result = build_ema5_open_signals(prepare_symbol_bars(self._bars(), "159915.SZ"))

        self.assertTrue(result.loc[:4, "previous_ema5"].isna().all())
        self.assertAlmostEqual(result.loc[5, "previous_ema5"], 10.0)
        self.assertFalse(result.loc[5, "target_invested"])
        self.assertAlmostEqual(result.loc[6, "previous_ema5"], 40.0 / 3.0)
        self.assertTrue(result.loc[6, "target_invested"])

    def test_open_equal_to_previous_ema5_stays_empty(self) -> None:
        result = build_ema5_open_signals(prepare_symbol_bars(self._bars(), "159915.SZ"))

        self.assertEqual(result.loc[5, "open"], result.loc[5, "previous_ema5"])
        self.assertFalse(result.loc[5, "target_invested"])

    def test_prepare_rejects_duplicate_dates(self) -> None:
        bars = pd.concat([self._bars(), self._bars().iloc[[0]]], ignore_index=True)

        with self.assertRaisesRegex(ValueError, "duplicate dates"):
            prepare_symbol_bars(bars, "159915.SZ")

    def test_prepare_rejects_non_finite_or_invalid_ohlc(self) -> None:
        for column, value in (("high", np.inf), ("low", 11.0)):
            with self.subTest(column=column, value=value):
                bars = self._bars()
                bars.loc[2, column] = value

                with self.assertRaisesRegex(ValueError, "invalid OHLCV"):
                    prepare_symbol_bars(bars, "159915.SZ")

    def test_prepare_requires_six_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 6 rows"):
            prepare_symbol_bars(self._bars().iloc[:5], "159915.SZ")

    def test_backtest_executes_full_position_round_trip_and_keeps_final_position(
        self,
    ) -> None:
        config = Ema5OpenConfig(
            initial_capital=10_000.0,
            lot_size=100,
            commission_rate=0.0,
            min_commission=0.0,
            slippage_rate=0.0,
        )

        result = run_ema5_open_backtest(self._round_trip_bars(), config)

        self.assertEqual(result.trades["side"].tolist(), ["buy", "sell", "buy"])
        self.assertEqual(result.trades["quantity"].tolist(), [900, 900, 600])
        self.assertAlmostEqual(result.trades.loc[1, "realized_pnl"], -1_800.0)
        self.assertAlmostEqual(result.equity_curve.iloc[-1]["equity"], 8_800.0)
        self.assertEqual(result.positions.iloc[-1]["quantity"], 600)
        self.assertTrue(result.summary["is_open"])

    def test_full_position_quantity_includes_slippage_and_commission(self) -> None:
        config = Ema5OpenConfig(initial_capital=100_000.0)

        quantity = calculate_full_position_quantity(100_000.0, 100.0, config)

        fill = 100.0 * (1 + config.slippage_rate)
        cost = quantity * fill + max(
            quantity * fill * config.commission_rate,
            config.min_commission,
        )
        next_quantity = quantity + config.lot_size
        next_cost = next_quantity * fill + max(
            next_quantity * fill * config.commission_rate,
            config.min_commission,
        )
        self.assertEqual(quantity % 100, 0)
        self.assertLessEqual(cost, 100_000.0)
        self.assertGreater(next_cost, 100_000.0)

    def test_unchanged_long_signal_does_not_repeat_buy(self) -> None:
        bars = self._round_trip_bars()
        bars.loc[6:, "open"] = 12.0
        bars.loc[6:, "high"] = 13.2

        result = run_ema5_open_backtest(
            bars,
            Ema5OpenConfig(
                initial_capital=10_000.0,
                commission_rate=0.0,
                min_commission=0.0,
                slippage_rate=0.0,
            ),
        )

        self.assertEqual(result.trades["side"].tolist(), ["buy"])


if __name__ == "__main__":
    unittest.main()
