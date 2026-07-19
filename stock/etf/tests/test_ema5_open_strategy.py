import unittest

import numpy as np
import pandas as pd

from stock.etf.ema5_open_strategy import (
    build_ema5_open_signals,
    prepare_symbol_bars,
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

    def test_signal_uses_previous_close_ema5_without_lookahead(self) -> None:
        result = build_ema5_open_signals(
            prepare_symbol_bars(self._bars(), "159915.SZ")
        )

        self.assertTrue(result.loc[:4, "previous_ema5"].isna().all())
        self.assertAlmostEqual(result.loc[5, "previous_ema5"], 10.0)
        self.assertFalse(result.loc[5, "target_invested"])
        self.assertAlmostEqual(result.loc[6, "previous_ema5"], 40.0 / 3.0)
        self.assertTrue(result.loc[6, "target_invested"])

    def test_open_equal_to_previous_ema5_stays_empty(self) -> None:
        result = build_ema5_open_signals(
            prepare_symbol_bars(self._bars(), "159915.SZ")
        )

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


if __name__ == "__main__":
    unittest.main()
