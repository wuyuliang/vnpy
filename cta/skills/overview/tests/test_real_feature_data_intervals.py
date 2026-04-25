"""Real data interval compatibility tests (day/minute*/minute)."""
from __future__ import annotations

from functools import lru_cache
import unittest

import pandas as pd

from cta.feature.feature_loader import load_symbol_features
from cta.skills.filtering_scoring.setup_quality import score_setup
from cta.skills.price_action.tight_range_breakout import detect_tight_range
from cta.skills.trend_strategies.donchian_breakout import compute_donchian


INTERVALS = ("day", "minute60", "minute30", "minute15", "minute5", "minute")


@lru_cache(maxsize=16)
def _load_interval(interval: str) -> pd.DataFrame:
    if interval == "day":
        return load_symbol_features(
            "RB0",
            interval=interval,
            start_date="2020-01-01",
            end_date="2020-04-30",
        ).reset_index(drop=True)
    return load_symbol_features(
        "RB0",
        interval=interval,
        start_date="2010-01-04",
        end_date="2010-01-29",
    ).reset_index(drop=True)


class TestRealFeatureDataIntervals(unittest.TestCase):
    def test_tight_range_interval_compatibility_real_data(self) -> None:
        for interval in INTERVALS:
            with self.subTest(interval=interval):
                try:
                    df = _load_interval(interval)
                except Exception as exc:
                    self.skipTest(f"missing local interval data {interval}: {exc}")
                if len(df) < 30:
                    self.skipTest(f"interval {interval} history too short")
                out = detect_tight_range(df, interval=interval)
                self.assertEqual(len(out), len(df))
                self.assertIn("tr_valid", out.columns)
                self.assertGreaterEqual(int(out["tr_valid"].sum()), 0)

    def test_donchian_and_setup_score_interval_compatibility_real_data(self) -> None:
        for interval in INTERVALS:
            with self.subTest(interval=interval):
                try:
                    df = _load_interval(interval)
                except Exception as exc:
                    self.skipTest(f"missing local interval data {interval}: {exc}")
                if len(df) < 30:
                    self.skipTest(f"interval {interval} history too short")

                don = compute_donchian(df, n_entry=20, n_exit=10, interval=interval)
                self.assertEqual(len(don), len(df))
                self.assertIn("don_upper_entry", don.columns)

                i = len(df) - 1
                sq = score_setup(df, i, setup_type="tight_range", interval=interval)
                self.assertGreaterEqual(sq.score, 0.0)
                self.assertLessEqual(sq.score, 1.0)


if __name__ == "__main__":
    unittest.main()
