import unittest

import numpy as np
import pandas as pd

from stock.etf.indicators import add_indicators, calculate_adx, calculate_atr


class IndicatorTests(unittest.TestCase):
    def test_add_indicators_uses_spec_formulas(self) -> None:
        close = pd.Series(np.arange(1.0, 101.0))
        frame = pd.DataFrame(
            {
                "open": close - 0.2,
                "high": close + 1.0,
                "low": close - 1.0,
                "close": close,
            }
        )

        result = add_indicators(frame)

        expected_ema5 = close.ewm(span=5, adjust=False, min_periods=5).mean()
        expected_ema10 = close.ewm(span=10, adjust=False, min_periods=10).mean()
        self.assertAlmostEqual(result.loc[99, "ema10"], expected_ema10.loc[99])
        self.assertAlmostEqual(result.loc[99, "return_3"], 100 / 97 - 1)
        self.assertAlmostEqual(result.loc[99, "return_5"], 100 / 95 - 1)
        self.assertAlmostEqual(result.loc[99, "return_10"], 100 / 90 - 1)
        self.assertAlmostEqual(result.loc[99, "return_20"], 100 / 80 - 1)
        self.assertAlmostEqual(
            result.loc[99, "ema5_slope"],
            expected_ema5.loc[99] / expected_ema5.loc[94] - 1,
        )
        self.assertAlmostEqual(
            result.loc[99, "normalized_atr5"],
            result.loc[99, "atr5"] / result.loc[99, "close"],
        )
        self.assertIn("atr5", result.columns)
        self.assertIn("risk_atr", result.columns)
        self.assertIn("trend_adx", result.columns)
        self.assertNotIn("atr10", result.columns)
        self.assertNotIn("adx10", result.columns)
        self.assertAlmostEqual(result.loc[99, "risk_atr"], result.loc[99, "atr5"])
        self.assertAlmostEqual(
            result.loc[99, "trend_adx"],
            calculate_adx(frame, 5).loc[99],
        )

    def test_turnover_median20_uses_twenty_valid_bars(self) -> None:
        close = pd.Series(np.arange(1.0, 31.0))
        turnover = pd.Series(np.arange(1.0, 31.0) * 10_000_000)
        frame = pd.DataFrame(
            {
                "open": close,
                "high": close + 1.0,
                "low": close - 0.5,
                "close": close,
                "turnover": turnover,
            }
        )

        result = add_indicators(frame)

        expected = turnover.rolling(20, min_periods=20).median()
        pd.testing.assert_series_equal(
            result["turnover_median20"],
            expected,
            check_names=False,
        )

    def test_atr_uses_wilder_smoothing(self) -> None:
        frame = pd.DataFrame(
            {
                "high": [11.0, 13.0, 12.0, 15.0],
                "low": [9.0, 10.0, 9.0, 12.0],
                "close": [10.0, 12.0, 10.0, 14.0],
            }
        )
        tr = pd.Series([2.0, 3.0, 3.0, 5.0])
        expected = tr.ewm(alpha=0.5, adjust=False, min_periods=2).mean()

        pd.testing.assert_series_equal(
            calculate_atr(frame, 2), expected, check_names=False
        )

    def test_strategy_indicator_periods_allow_five_or_ten_days(self) -> None:
        frame = pd.DataFrame(
            {
                "high": [11.0] * 20,
                "low": [9.0] * 20,
                "close": [10.0] * 20,
            }
        )

        result = add_indicators(frame, atr_period=10, adx_period=10)

        pd.testing.assert_series_equal(
            result["atr5"],
            calculate_atr(frame, 5),
            check_names=False,
        )
        pd.testing.assert_series_equal(
            result["risk_atr"],
            calculate_atr(frame, 10),
            check_names=False,
        )
        pd.testing.assert_series_equal(
            result["trend_adx"],
            calculate_adx(frame, 10),
            check_names=False,
        )
        with self.assertRaises(ValueError):
            add_indicators(frame, atr_period=14)
        with self.assertRaises(ValueError):
            add_indicators(frame, adx_period=14)

    def test_adx_is_positive_for_persistent_uptrend(self) -> None:
        close = pd.Series(np.arange(10.0, 50.0))
        frame = pd.DataFrame({"high": close + 1, "low": close - 1, "close": close})

        adx = calculate_adx(frame, 10)

        self.assertGreater(adx.iloc[-1], 20)


if __name__ == "__main__":
    unittest.main()
