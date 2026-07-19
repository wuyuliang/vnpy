import unittest

import numpy as np
import pandas as pd

from stock.etf.indicators import calculate_adx, calculate_atr
from stock.etf.industry import (
    INDUSTRY_OUTPUT_COLUMNS,
    add_industry_indicators,
    aggregate_industry_daily,
    align_share_size,
    build_industry_daily,
    build_industry_price_index,
    normalize_share_size,
)


class IndustryTests(unittest.TestCase):
    @staticmethod
    def _daily_fixture() -> pd.DataFrame:
        dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
        return pd.DataFrame(
            {
                "symbol": ["A.SH"] * 3 + ["B.SH"] * 3,
                "datetime": list(dates) + list(dates),
                "pre_close": [9.5, 10.0, 10.5, 19.5, 20.0, 20.5],
                "open": [10.0, 10.5, 11.0, 20.0, 20.5, 21.0],
                "high": [10.5, 11.0, 11.5, 20.5, 21.0, 21.5],
                "low": [9.5, 10.0, 10.5, 19.5, 20.0, 20.5],
                "close": [10.0, 10.5, 11.0, 20.0, 20.5, 21.0],
                "volume": [1.0, 2.0, 3.0, 2.0, 3.0, 4.0],
                "turnover": [100.0, 110.0, 120.0, 200.0, 210.0, 220.0],
            }
        )

    @staticmethod
    def _share_fixture() -> pd.DataFrame:
        return pd.DataFrame(
            {
                "symbol": ["A.SH", "A.SH", "B.SH", "B.SH"],
                "datetime": pd.to_datetime(
                    ["2026-01-02", "2026-01-03", "2026-01-01", "2026-01-03"]
                ),
                "fund_units": [100.0, 110.0, 200.0, 210.0],
                "market_close": [10.0, 11.0, 20.0, 21.0],
                "reported_total_size": [1_000.0, 1_210.0, 4_000.0, 4_410.0],
            }
        )

    @staticmethod
    def _industry_daily_fixture(days: int = 200) -> pd.DataFrame:
        dates = pd.bdate_range("2025-01-02", periods=days)
        rows: list[dict[str, object]] = []
        for industry, offset in (("materials", 0.0), ("finance", 1_000.0)):
            for index, date in enumerate(dates, start=1):
                close = 100.0 + offset + index
                rows.append(
                    {
                        "datetime": date,
                        "industry": industry,
                        "industry_name": (
                            "材料" if industry == "materials" else "金融"
                        ),
                        "etf_count": 2,
                        "market_value_etf_count": 2,
                        "share_coverage_ratio": 1.0,
                        "total_market_value": 1_000_000.0 + index,
                        "daily_turnover": float(index),
                        "daily_volume": float(index * 10),
                        "open": close - 0.2,
                        "high": close + 1.0,
                        "low": close - 1.0,
                        "close": close,
                    }
                )
        return pd.DataFrame(rows)

    def test_normalize_share_size_converts_units_and_keeps_latest_duplicate(
        self,
    ) -> None:
        raw = pd.DataFrame(
            {
                "trade_date": ["20260105", "20260102", "20260102", "20260106"],
                "ts_code": ["A.SH", "A.SH", "A.SH", "B.SH"],
                "total_share": [15.0, 10.0, 12.0, 8.0],
                "total_size": [153.0, 101.0, 121.2, np.nan],
                "close": [1.02, 1.0, 1.01, np.nan],
            }
        )

        result = normalize_share_size(raw)

        self.assertEqual(result["symbol"].tolist(), ["A.SH", "A.SH", "B.SH"])
        self.assertEqual(
            result["datetime"].tolist(),
            list(pd.to_datetime(["2026-01-02", "2026-01-05", "2026-01-06"])),
        )
        self.assertEqual(
            result["fund_units"].tolist(),
            [120_000.0, 150_000.0, 80_000.0],
        )
        self.assertEqual(result.loc[0, "market_close"], 1.01)
        self.assertEqual(result.loc[0, "reported_total_size"], 1_212_000.0)
        self.assertTrue(pd.isna(result.loc[2, "market_close"]))
        self.assertTrue(pd.isna(result.loc[2, "reported_total_size"]))

    def test_normalize_share_size_drops_invalid_share_observations(self) -> None:
        raw = pd.DataFrame(
            {
                "trade_date": ["20260102", "bad-date", "20260106"],
                "ts_code": ["A.SH", "B.SH", "C.SH"],
                "total_share": [0.0, 10.0, np.nan],
                "total_size": [0.0, 10.0, 10.0],
                "close": [1.0, 1.0, 1.0],
            }
        )

        result = normalize_share_size(raw)

        self.assertTrue(result.empty)
        self.assertEqual(
            result.columns.tolist(),
            [
                "symbol",
                "datetime",
                "fund_units",
                "market_close",
                "reported_total_size",
            ],
        )

    def test_align_share_size_forward_fills_only_units_without_lookahead(
        self,
    ) -> None:
        result = align_share_size(self._daily_fixture(), self._share_fixture())
        result = result.set_index(["symbol", "datetime"])

        self.assertTrue(pd.isna(result.loc[("A.SH", "2026-01-01"), "fund_units"]))
        self.assertEqual(result.loc[("A.SH", "2026-01-02"), "fund_units"], 100.0)
        self.assertEqual(result.loc[("B.SH", "2026-01-02"), "fund_units"], 200.0)
        self.assertTrue(pd.isna(result.loc[("B.SH", "2026-01-02"), "market_close"]))
        self.assertTrue(
            pd.isna(result.loc[("B.SH", "2026-01-02"), "reported_total_size"])
        )

    def test_aggregate_industry_daily_reconciles_liquidity(
        self,
    ) -> None:
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH", "B.SH"],
                "industry": ["materials", "materials"],
            }
        )

        result = aggregate_industry_daily(self._daily_fixture(), metadata).set_index(
            "datetime"
        )

        first = result.loc[pd.Timestamp("2026-01-01")]
        self.assertEqual(first["industry"], "materials")
        self.assertEqual(first["industry_name"], "材料")
        self.assertEqual(first["etf_count"], 2)
        self.assertEqual(first["daily_turnover"], 300.0)
        self.assertEqual(first["daily_volume"], 300.0)

        last = result.loc[pd.Timestamp("2026-01-03")]
        self.assertEqual(last["daily_turnover"], 340.0)
        self.assertEqual(last["daily_volume"], 700.0)
        for column in (
            "market_value_etf_count",
            "share_coverage_ratio",
            "total_market_value",
        ):
            self.assertNotIn(column, result.columns)

    def test_aggregate_industry_daily_rejects_missing_metadata(self) -> None:
        metadata = pd.DataFrame({"symbol": ["A.SH"], "industry": ["materials"]})

        with self.assertRaisesRegex(ValueError, "missing industry metadata.*B.SH"):
            aggregate_industry_daily(self._daily_fixture(), metadata)

    def test_industry_index_uses_previous_day_turnover_weights(self) -> None:
        dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
        daily = pd.DataFrame(
            {
                "symbol": ["A.SH"] * 3 + ["B.SH"] * 3,
                "datetime": list(dates) + list(dates),
                "pre_close": [10.0, 10.0, 11.0, 20.0, 20.0, 20.0],
                "open": [10.0, 10.5, 11.0, 20.0, 20.0, 20.0],
                "high": [10.0, 11.5, 11.0, 20.0, 20.4, 20.0],
                "low": [10.0, 10.0, 11.0, 20.0, 19.8, 20.0],
                "close": [10.0, 11.0, 11.0, 20.0, 20.0, 20.0],
                "volume": [1.0] * 6,
                "turnover": [100.0] * 3 + [300.0] * 3,
            }
        )
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH", "B.SH"],
                "industry": ["materials", "materials"],
            }
        )

        result = build_industry_price_index(daily, metadata).set_index("datetime")

        self.assertTrue(pd.isna(result.loc[dates[0], "close"]))
        self.assertAlmostEqual(result.loc[dates[1], "open"], 101.25)
        self.assertAlmostEqual(result.loc[dates[1], "high"], 105.25)
        self.assertAlmostEqual(result.loc[dates[1], "low"], 99.25)
        self.assertAlmostEqual(result.loc[dates[1], "close"], 102.5)
        self.assertAlmostEqual(result.loc[dates[2], "close"], 102.5)
        self.assertGreaterEqual(
            result.loc[dates[1], "high"],
            max(result.loc[dates[1], "open"], result.loc[dates[1], "close"]),
        )
        self.assertLessEqual(
            result.loc[dates[1], "low"],
            min(result.loc[dates[1], "open"], result.loc[dates[1], "close"]),
        )

    def test_industry_index_uses_previous_adjusted_close_for_returns(self) -> None:
        dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
        daily = pd.DataFrame(
            {
                "symbol": ["A.SH"] * 3,
                "datetime": dates,
                # The lifecycle cache adjusts OHLC but preserves raw pre_close.
                "pre_close": [5.0, 5.1, 5.2],
                "open": [0.5, 0.505, 0.51],
                "high": [0.5, 0.515, 0.51],
                "low": [0.5, 0.5, 0.51],
                "close": [0.5, 0.51, 0.51],
                "volume": [1.0] * 3,
                "turnover": [100.0] * 3,
            }
        )
        metadata = pd.DataFrame({"symbol": ["A.SH"], "industry": ["electronics"]})

        result = build_industry_price_index(daily, metadata).set_index("datetime")

        self.assertTrue(pd.isna(result.loc[dates[0], "close"]))
        self.assertAlmostEqual(result.loc[dates[1], "close"], 102.0)
        self.assertAlmostEqual(result.loc[dates[2], "close"], 102.0)
        self.assertTrue(
            np.isfinite(result.loc[dates[1] :, ["open", "high", "low", "close"]])
            .all()
            .all()
        )

    def test_industry_index_rejects_non_finite_current_ohlc(self) -> None:
        dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
        daily = pd.DataFrame(
            {
                "symbol": ["A.SH"] * 3,
                "datetime": dates,
                "open": [10.0, 10.0, 10.0],
                "high": [10.0, np.inf, 10.0],
                "low": [10.0, 10.0, 10.0],
                "close": [10.0, 10.0, 10.0],
                "volume": [1.0] * 3,
                "turnover": [100.0] * 3,
            }
        )
        metadata = pd.DataFrame({"symbol": ["A.SH"], "industry": ["electronics"]})

        result = build_industry_price_index(daily, metadata).set_index("datetime")

        self.assertTrue(
            result.loc[dates[1], ["open", "high", "low", "close"]].isna().all()
        )
        self.assertAlmostEqual(result.loc[dates[2], "close"], 100.0)

    def test_industry_index_excludes_new_etf_until_prior_turnover_exists(self) -> None:
        dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-03"])
        daily = pd.DataFrame(
            {
                "symbol": ["A.SH"] * 3 + ["NEW.SH"] * 2,
                "datetime": list(dates) + list(dates[1:]),
                "pre_close": [10.0, 10.0, 10.0, 5.0, 10.0],
                "open": [10.0, 10.0, 10.0, 10.0, 10.0],
                "high": [10.0, 10.0, 10.0, 10.0, 10.0],
                "low": [10.0, 10.0, 10.0, 10.0, 10.0],
                "close": [10.0, 10.0, 10.0, 10.0, 10.0],
                "volume": [1.0] * 5,
                "turnover": [100.0] * 5,
            }
        )
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH", "NEW.SH"],
                "industry": ["materials", "materials"],
            }
        )

        result = build_industry_price_index(daily, metadata).set_index("datetime")

        self.assertAlmostEqual(result.loc[dates[1], "close"], 100.0)
        self.assertAlmostEqual(result.loc[dates[2], "close"], 100.0)

    def test_industry_index_rejects_stale_turnover_after_trading_gap(self) -> None:
        dates = pd.to_datetime(["2026-01-01", "2026-01-02", "2026-01-05"])
        daily = pd.DataFrame(
            {
                "symbol": ["GAP.SH"] * 2 + ["LIVE.SH"] * 3,
                "datetime": [dates[0], dates[2], *dates],
                "pre_close": [10.0, 10.0, 10.0, 10.0, 10.0],
                "open": [10.0, 20.0, 10.0, 10.0, 10.0],
                "high": [10.0, 20.0, 10.0, 10.0, 10.0],
                "low": [10.0, 20.0, 10.0, 10.0, 10.0],
                "close": [10.0, 20.0, 10.0, 10.0, 10.0],
                "volume": [1.0] * 5,
                "turnover": [900.0, 900.0, 100.0, 100.0, 100.0],
            }
        )
        metadata = pd.DataFrame(
            {
                "symbol": ["GAP.SH", "LIVE.SH"],
                "industry": ["materials", "materials"],
            }
        )

        result = build_industry_price_index(daily, metadata).set_index("datetime")

        self.assertAlmostEqual(result.loc[dates[1], "close"], 100.0)
        self.assertAlmostEqual(result.loc[dates[2], "close"], 100.0)

    def test_build_industry_daily_combines_unique_totals_and_index(self) -> None:
        metadata = pd.DataFrame(
            {
                "symbol": ["A.SH", "B.SH"],
                "industry": ["materials", "materials"],
            }
        )

        result = build_industry_daily(self._daily_fixture(), metadata)

        self.assertFalse(result.duplicated(["datetime", "industry"]).any())
        self.assertEqual(result["datetime"].tolist(), sorted(result["datetime"]))
        for column in ("open", "high", "low", "close"):
            self.assertIn(column, result.columns)
        for column in (
            "market_value_etf_count",
            "share_coverage_ratio",
            "total_market_value",
        ):
            self.assertNotIn(column, result.columns)

    def test_industry_liquidity_windows_require_full_industry_history(self) -> None:
        source = self._industry_daily_fixture()

        result = add_industry_indicators(source)
        materials = result.loc[result["industry"] == "materials"].reset_index(drop=True)

        for period in (1, 3, 5, 10, 20, 60, 180):
            expected_turnover = (
                pd.Series(range(1, 201), dtype=float)
                .rolling(period, min_periods=period)
                .sum()
            )
            expected_volume = (
                (pd.Series(range(1, 201), dtype=float) * 10)
                .rolling(period, min_periods=period)
                .sum()
            )
            pd.testing.assert_series_equal(
                materials[f"turnover_sum_{period}"],
                expected_turnover,
                check_names=False,
            )
            pd.testing.assert_series_equal(
                materials[f"volume_sum_{period}"],
                expected_volume,
                check_names=False,
            )
        self.assertNotIn("daily_turnover", result.columns)
        self.assertNotIn("daily_volume", result.columns)

    def test_industry_indicators_match_reference_without_crossing_groups(
        self,
    ) -> None:
        source = self._industry_daily_fixture()

        result = add_industry_indicators(source)

        for industry in ("materials", "finance"):
            actual = result.loc[result["industry"] == industry].reset_index(drop=True)
            reference = source.loc[source["industry"] == industry].reset_index(
                drop=True
            )
            for period in (1, 3, 5, 10, 20, 60, 180):
                expected_ema = (
                    reference["close"]
                    .ewm(
                        span=period,
                        adjust=False,
                        min_periods=period,
                    )
                    .mean()
                )
                pd.testing.assert_series_equal(
                    actual[f"ema_{period}"], expected_ema, check_names=False
                )
                pd.testing.assert_series_equal(
                    actual[f"atr_{period}"],
                    calculate_atr(reference, period),
                    check_names=False,
                )
            for period in (3, 5, 10, 20, 60, 180):
                pd.testing.assert_series_equal(
                    actual[f"adx_{period}"],
                    calculate_adx(reference, period),
                    check_names=False,
                )
        self.assertNotIn("adx_1", result.columns)
        self.assertEqual(result.columns.tolist(), INDUSTRY_OUTPUT_COLUMNS)
        self.assertEqual(
            INDUSTRY_OUTPUT_COLUMNS[:8],
            [
                "datetime",
                "industry",
                "industry_name",
                "etf_count",
                "open",
                "high",
                "low",
                "close",
            ],
        )
        self.assertFalse(result.duplicated(["datetime", "industry"]).any())

    def test_industry_indicators_reset_after_missing_ohlc(self) -> None:
        source = self._industry_daily_fixture(days=12)
        source = source.loc[source["industry"] == "materials"].reset_index(drop=True)
        source.loc[4, ["open", "high", "low", "close"]] = np.nan

        result = add_industry_indicators(source)

        technical_columns = [
            column
            for column in result.columns
            if column.startswith(("ema_", "atr_", "adx_"))
        ]
        self.assertTrue(result.loc[4, technical_columns].isna().all())
        self.assertTrue(pd.isna(result.loc[5, "ema_3"]))
        self.assertTrue(pd.isna(result.loc[6, "ema_3"]))
        self.assertFalse(pd.isna(result.loc[7, "ema_3"]))
        self.assertTrue(pd.isna(result.loc[5, "atr_3"]))
        self.assertFalse(pd.isna(result.loc[7, "atr_3"]))
        self.assertFalse(pd.isna(result.loc[5, "ema_1"]))
        self.assertFalse(pd.isna(result.loc[5, "atr_1"]))
        self.assertEqual(result.loc[4, "turnover_sum_3"], 4.0 + 5.0 + 3.0)


if __name__ == "__main__":
    unittest.main()
