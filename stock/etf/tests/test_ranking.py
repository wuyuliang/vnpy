import unittest

import pandas as pd

from stock.etf.config import StrategyConfig
from stock.etf.ranking import audit_daily_candidates, build_daily_ranking, is_risk_on


class RankingTests(unittest.TestCase):
    def setUp(self) -> None:
        self.config = StrategyConfig(warmup_bars=3)

    def test_risk_on_uses_previous_ema5_for_open_filter(self) -> None:
        row = pd.Series(
            {
                "open": 11.0,
                "close": 12.5,
                "ema5": 12.0,
                "ema10": 10.0,
                "ema20": 9.0,
                "trend_adx": 25.0,
            }
        )

        self.assertTrue(is_risk_on(row, previous_ema5=10.5, config=self.config))
        self.assertFalse(is_risk_on(row, previous_ema5=11.0, config=self.config))

    def test_risk_on_requires_close_above_ema10(self) -> None:
        row = pd.Series(
            {
                "open": 11.0,
                "close": 9.5,
                "ema5": 12.0,
                "ema10": 10.0,
                "ema20": 9.0,
                "trend_adx": 25.0,
            }
        )

        self.assertFalse(is_risk_on(row, previous_ema5=10.5, config=self.config))

    def test_ranking_uses_approved_weights_and_penalizes_normalized_atr5(self) -> None:
        date = pd.Timestamp("2026-07-01")
        frame = pd.DataFrame(
            [
                self._candidate("A.SH", date, 0.20, 0.01, 300_000_000),
                self._candidate("B.SH", date, 0.10, 0.02, 400_000_000),
                self._candidate("C.SH", date, 0.00, 0.03, 500_000_000),
            ]
        )

        result = build_daily_ranking(frame, date, self.config)

        self.assertEqual(result["symbol"].tolist(), ["A.SH", "B.SH", "C.SH"])
        self.assertEqual(result["rs_rank"].tolist(), [1, 2, 3])
        self.assertGreater(result.iloc[0]["rs_score"], result.iloc[1]["rs_score"])
        self.assertAlmostEqual(result.iloc[0]["rs_score"], 1.2 - 0.2 / 3)

    def test_ranking_uses_return5_to_break_equal_scores(self) -> None:
        date = pd.Timestamp("2026-07-01")
        high_return5 = self._candidate("Z.SH", date, 0.10, 0.01, 300_000_000)
        high_return3 = self._candidate("A.SH", date, 0.10, 0.01, 300_000_000)
        high_return5["return_5"] = 0.20
        high_return3["return_3"] = 0.20

        result = build_daily_ranking(
            pd.DataFrame([high_return3, high_return5]),
            date,
            self.config,
        )

        self.assertAlmostEqual(result.loc[0, "rs_score"], result.loc[1, "rs_score"])
        self.assertEqual(result["symbol"].tolist(), ["Z.SH", "A.SH"])

    def test_entry_liquidity_rejects_equal_turnover_without_dropping_score(
        self,
    ) -> None:
        date = pd.Timestamp("2026-07-01")
        equal_turnover = self._candidate("A.SH", date, 0.1, 0.01, 200_000_000)
        valid = self._candidate("B.SH", date, 0.1, 0.01, 200_000_001)
        valid["list_date"] = pd.Timestamp("2026-01-01")

        result = build_daily_ranking(
            pd.DataFrame([equal_turnover, valid]), date, self.config
        )

        ranked = result.set_index("symbol")
        self.assertEqual(set(ranked.index), {"A.SH", "B.SH"})
        self.assertFalse(bool(ranked.loc["A.SH", "entry_liquidity_eligible"]))
        self.assertTrue(pd.isna(ranked.loc["A.SH", "entry_rank"]))
        self.assertTrue(bool(ranked.loc["B.SH", "entry_liquidity_eligible"]))
        self.assertEqual(ranked.loc["B.SH", "entry_rank"], 1)
        self.assertEqual(ranked.loc["A.SH", "holding_rank"], 2)

    def test_entry_and_holding_ranks_share_the_liquid_reference_pool(self) -> None:
        date = pd.Timestamp("2026-07-01")
        strongest_illiquid = self._candidate(
            "A.SH",
            date,
            0.30,
            0.01,
            100_000_000,
        )
        strongest_illiquid["turnover_median20"] = 100_000_000
        liquid_high = self._candidate("B.SH", date, 0.20, 0.01, 400_000_000)
        liquid_low = self._candidate("C.SH", date, 0.10, 0.01, 300_000_000)

        result = build_daily_ranking(
            pd.DataFrame([strongest_illiquid, liquid_high, liquid_low]),
            date,
            self.config,
        ).set_index("symbol")

        self.assertEqual(result.loc["B.SH", "entry_rank"], 1)
        self.assertEqual(result.loc["C.SH", "entry_rank"], 2)
        self.assertTrue(pd.isna(result.loc["A.SH", "entry_rank"]))
        self.assertEqual(result.loc["A.SH", "holding_rank"], 1)
        self.assertEqual(result.loc["B.SH", "holding_rank"], 1)
        self.assertEqual(result.loc["C.SH", "holding_rank"], 2)

    def test_delisted_candidate_is_ranked_only_through_delist_date(self) -> None:
        before = pd.Timestamp("2026-06-30")
        after = pd.Timestamp("2026-07-01")
        before_row = self._candidate("A.SH", before, 0.20, 0.01, 300_000_000)
        after_row = self._candidate("A.SH", after, 0.20, 0.01, 300_000_000)
        before_row["delist_date"] = before
        after_row["delist_date"] = before
        frame = pd.DataFrame([before_row, after_row])

        before_result = build_daily_ranking(frame, before, self.config)
        after_result = build_daily_ranking(frame, after, self.config)

        self.assertEqual(before_result["symbol"].tolist(), ["A.SH"])
        self.assertTrue(after_result.empty)

    def test_only_most_liquid_benchmark_clone_receives_entry_rank(self) -> None:
        date = pd.Timestamp("2026-07-01")
        stronger_clone = self._candidate("A.SH", date, 0.30, 0.01, 300_000_000)
        liquid_clone = self._candidate("B.SH", date, 0.20, 0.01, 400_000_000)
        independent = self._candidate("C.SH", date, 0.10, 0.01, 350_000_000)
        stronger_clone["benchmark_key"] = "same-index"
        liquid_clone["benchmark_key"] = "same-index"
        independent["benchmark_key"] = "other-index"

        result = build_daily_ranking(
            pd.DataFrame([stronger_clone, liquid_clone, independent]),
            date,
            self.config,
        ).set_index("symbol")

        self.assertFalse(bool(result.loc["A.SH", "entry_representative"]))
        self.assertTrue(pd.isna(result.loc["A.SH", "entry_rank"]))
        self.assertTrue(bool(result.loc["B.SH", "entry_representative"]))
        self.assertEqual(result.loc["B.SH", "entry_rank"], 1)
        self.assertEqual(result.loc["C.SH", "entry_rank"], 2)

    def test_candidate_audit_keeps_excluded_symbol_and_reason(self) -> None:
        date = pd.Timestamp("2026-07-01")
        rejected = self._candidate("A.SH", date, 0.1, 0.01, 200_000_000)
        valid = self._candidate("B.SH", date, 0.2, 0.01, 300_000_000)

        audit = audit_daily_candidates(
            pd.DataFrame([rejected, valid]), date, self.config
        )

        rejected_row = audit.set_index("symbol").loc["A.SH"]
        valid_row = audit.set_index("symbol").loc["B.SH"]
        self.assertEqual(rejected_row["exclusion_reason"], "turnover_not_above_minimum")
        self.assertEqual(valid_row["exclusion_reason"], "")
        self.assertEqual(valid_row["rs_rank"], 1)

    def test_candidate_audit_explains_missing_turnover_and_trading_state(self) -> None:
        date = pd.Timestamp("2026-07-01")
        candidate = self._candidate("A.SH", date, 0.1, 0.01, float("nan"))
        candidate["is_trading"] = None

        audit = audit_daily_candidates(pd.DataFrame([candidate]), date, self.config)

        reasons = audit.loc[0, "exclusion_reason"].split("|")
        self.assertIn("missing_turnover", reasons)
        self.assertIn("not_trading", reasons)

    def test_candidate_audit_rejects_infinite_indicator(self) -> None:
        date = pd.Timestamp("2026-07-01")
        candidate = self._candidate("A.SH", date, 0.1, 0.01, 300_000_000)
        candidate["normalized_atr5"] = float("inf")

        audit = audit_daily_candidates(pd.DataFrame([candidate]), date, self.config)

        self.assertIn("invalid_indicator", audit.loc[0, "exclusion_reason"].split("|"))

    def _candidate(
        self,
        symbol: str,
        date: pd.Timestamp,
        strength: float,
        volatility: float,
        turnover: float,
    ) -> dict[str, object]:
        return {
            "symbol": symbol,
            "datetime": date,
            "list_date": pd.Timestamp("2020-01-01"),
            "is_trading": True,
            "turnover": turnover,
            "turnover_median20": turnover,
            "bar_count": 100,
            "close": 11.0,
            "ema5": 10.8,
            "ema10": 10.5,
            "ema20": 10.0,
            "trend_adx": 25.0,
            "return_3": strength,
            "return_5": strength,
            "return_10": strength,
            "return_20": strength,
            "ema5_slope": strength,
            "normalized_atr5": volatility,
            "atr5": volatility * 11.0,
            "risk_atr": volatility * 11.0,
            "benchmark_key": symbol,
        }


if __name__ == "__main__":
    unittest.main()
