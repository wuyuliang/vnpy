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
        dates = pd.bdate_range("2026-01-02", periods=13)
        opens = [
            10.0,
            11.0,
            12.0,
            13.0,
            14.0,
            15.0,
            16.0,
            17.0,
            18.0,
            19.0,
            18.0,
            16.5,
            15.5,
        ]
        closes = [
            10.0,
            11.0,
            12.0,
            13.0,
            14.0,
            15.0,
            16.0,
            17.0,
            18.0,
            19.0,
            18.0,
            17.0,
            16.0,
        ]
        return pd.DataFrame(
            {
                "symbol": ["159915.SZ"] * 13,
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
                "volume": [1_000.0] * 13,
            }
        )

    @staticmethod
    def _round_trip_bars() -> pd.DataFrame:
        return Ema5OpenStrategyTests._bars()

    def test_signals_use_previous_ema5_and_ema10_without_lookahead(self) -> None:
        result = build_ema5_open_signals(prepare_symbol_bars(self._bars(), "159915.SZ"))

        self.assertTrue(result.loc[:4, "previous_ema5"].isna().all())
        self.assertTrue(result.loc[:9, "previous_ema10"].isna().all())
        self.assertFalse(result.loc[:9, "entry_signal"].any())
        self.assertTrue(result.loc[10, "entry_signal"])
        self.assertFalse(result.loc[10, "exit_signal"])
        self.assertFalse(result.loc[11, "entry_signal"])
        self.assertFalse(result.loc[11, "exit_signal"])
        self.assertTrue(result.loc[12, "exit_signal"])

    def test_open_equal_to_previous_ema10_does_not_exit_bullish_trend(self) -> None:
        bars = self._bars()
        reference = build_ema5_open_signals(prepare_symbol_bars(bars, "159915.SZ"))
        previous_ema10 = reference.loc[10, "previous_ema10"]
        bars.loc[10, "open"] = previous_ema10
        bars.loc[10, "high"] = max(previous_ema10, bars.loc[10, "close"]) + 0.2
        bars.loc[10, "low"] = min(previous_ema10, bars.loc[10, "close"]) - 0.2

        result = build_ema5_open_signals(prepare_symbol_bars(bars, "159915.SZ"))

        self.assertFalse(result.loc[10, "entry_signal"])
        self.assertFalse(result.loc[10, "exit_signal"])

    def test_open_equal_to_previous_ema5_does_not_enter(self) -> None:
        bars = self._bars()
        reference = build_ema5_open_signals(prepare_symbol_bars(bars, "159915.SZ"))
        previous_ema5 = reference.loc[10, "previous_ema5"]
        bars.loc[10, "open"] = previous_ema5
        bars.loc[10, "high"] = max(previous_ema5, bars.loc[10, "close"]) + 0.2
        bars.loc[10, "low"] = min(previous_ema5, bars.loc[10, "close"]) - 0.2

        result = build_ema5_open_signals(prepare_symbol_bars(bars, "159915.SZ"))

        self.assertGreater(
            result.loc[10, "previous_ema5"], result.loc[10, "previous_ema10"]
        )
        self.assertFalse(result.loc[10, "entry_signal"])
        self.assertFalse(result.loc[10, "exit_signal"])

    def test_equal_ema5_and_ema10_exits_even_when_open_is_high(self) -> None:
        bars = self._bars()
        bars["close"] = 10.0
        bars["open"] = 20.0
        bars["high"] = 20.2
        bars["low"] = 9.8

        result = build_ema5_open_signals(prepare_symbol_bars(bars, "159915.SZ"))

        self.assertEqual(
            result.loc[10, "previous_ema5"], result.loc[10, "previous_ema10"]
        )
        self.assertFalse(result.loc[10, "entry_signal"])
        self.assertTrue(result.loc[10, "exit_signal"])

    def test_ema_dead_cross_exits_even_when_open_is_high(self) -> None:
        bars = self._bars()
        descending = list(reversed(range(10, 23)))
        bars["close"] = [float(value) for value in descending]
        bars["open"] = 30.0
        bars["high"] = bars["open"] + 0.2
        bars["low"] = bars["close"] - 0.2

        result = build_ema5_open_signals(prepare_symbol_bars(bars, "159915.SZ"))

        self.assertGreater(result.loc[10, "open"], result.loc[10, "previous_ema5"])
        self.assertLessEqual(
            result.loc[10, "previous_ema5"], result.loc[10, "previous_ema10"]
        )
        self.assertFalse(result.loc[10, "entry_signal"])
        self.assertTrue(result.loc[10, "exit_signal"])

    def test_prepare_rejects_duplicate_dates(self) -> None:
        duplicate = self._bars().iloc[[0]].copy()
        duplicate["datetime"] += pd.Timedelta(hours=12)
        bars = pd.concat([self._bars(), duplicate], ignore_index=True)

        with self.assertRaisesRegex(ValueError, "duplicate dates"):
            prepare_symbol_bars(bars, "159915.SZ")

    def test_prepare_rejects_missing_dates(self) -> None:
        bars = self._bars()
        bars.loc[2, "datetime"] = pd.NaT

        with self.assertRaisesRegex(ValueError, "missing dates"):
            prepare_symbol_bars(bars, "159915.SZ")

    def test_prepare_rejects_non_finite_or_invalid_ohlc(self) -> None:
        for column, value in (("high", np.inf), ("low", 13.0)):
            with self.subTest(column=column, value=value):
                bars = self._bars()
                bars.loc[2, column] = value

                with self.assertRaisesRegex(ValueError, "invalid OHLCV"):
                    prepare_symbol_bars(bars, "159915.SZ")

    def test_prepare_requires_eleven_rows(self) -> None:
        with self.assertRaisesRegex(ValueError, "at least 11 rows"):
            prepare_symbol_bars(self._bars().iloc[:10], "159915.SZ")

    def test_config_requires_exactly_one_hundred_share_lots(self) -> None:
        for invalid_lot_size in (50, 100.0, 100.5):
            with self.subTest(lot_size=invalid_lot_size):
                with self.assertRaisesRegex(ValueError, "lot_size must be integer 100"):
                    Ema5OpenConfig(lot_size=invalid_lot_size)

    def test_backtest_executes_full_position_round_trip_and_closes_position(
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

        self.assertEqual(result.trades["side"].tolist(), ["buy", "sell"])
        self.assertEqual(result.trades["quantity"].tolist(), [500, 500])
        self.assertEqual(
            result.signals.loc[10:12, "action"].tolist(), ["buy", "hold", "sell"]
        )
        self.assertAlmostEqual(result.trades.loc[1, "realized_pnl"], -1_250.0)
        self.assertAlmostEqual(result.equity_curve.iloc[-1]["equity"], 8_750.0)
        self.assertTrue(
            result.positions.empty
            or result.positions.iloc[-1]["datetime"]
            < result.equity_curve.iloc[-1]["datetime"]
        )
        self.assertFalse(result.summary["is_open"])

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

    def test_open_position_is_not_rebought_or_forced_closed_at_end(self) -> None:
        bars = self._round_trip_bars()
        bars.loc[12, "open"] = 17.0
        bars.loc[12, "high"] = 17.2

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
        self.assertEqual(result.trades.loc[0, "quantity"], 500)
        self.assertEqual(
            result.signals.loc[10:12, "action"].tolist(), ["buy", "hold", "hold"]
        )
        self.assertTrue(result.signals.loc[12, "target_invested"])
        self.assertEqual(
            result.positions.iloc[-1]["datetime"],
            result.equity_curve.iloc[-1]["datetime"],
        )
        self.assertEqual(result.positions.iloc[-1]["quantity"], 500)
        self.assertAlmostEqual(result.positions.iloc[-1]["market_value"], 8_000.0)
        self.assertAlmostEqual(result.equity_curve.iloc[-1]["equity"], 9_000.0)
        self.assertAlmostEqual(result.summary["final_equity"], 9_000.0)
        self.assertTrue(result.summary["is_open"])


if __name__ == "__main__":
    unittest.main()
