"""Tests for loader helpers and run_all_features interval resolver."""
from __future__ import annotations

import unittest

from cta.feature.loader import load_symbols_ranked, normalize_interval
from cta.feature.run_all_features import _build_parser, resolve_intervals


class TestLoaderAndScheduler(unittest.TestCase):
    def test_normalize_interval(self) -> None:
        self.assertEqual(normalize_interval("5min"), "minute5")
        self.assertEqual(normalize_interval("15min"), "minute15")
        self.assertEqual(normalize_interval("minute60"), "minute60")
        self.assertEqual(normalize_interval("day"), "day")

    def test_load_symbols_ranked_sorted(self) -> None:
        df = load_symbols_ranked(max_rank=10)
        self.assertGreater(len(df), 0)
        self.assertTrue((df["research_rank"].diff().fillna(0) >= 0).all())
        self.assertIn("symbol", df.columns)
        self.assertIn("exchange", df.columns)

    def test_resolve_intervals_ordering(self) -> None:
        vals = resolve_intervals(["minute", "day", "minute30", "minute5"])
        # long -> short ordering
        self.assertEqual(vals[0], "day")
        self.assertIn("minute30", vals)
        self.assertIn("minute5", vals)
        self.assertEqual(vals[-1], "minute")

    def test_resolve_intervals_intraday(self) -> None:
        vals = resolve_intervals(["intraday"])
        self.assertNotIn("day", vals)
        for v in vals:
            self.assertIn(v, {"minute", "minute5", "minute15", "minute30", "minute60"})

    def test_batch_feature_cli_does_not_publish_voi_opt_in_flag(self) -> None:
        self.assertNotIn("--voi-enabled-cells", _build_parser().format_help())


if __name__ == "__main__":
    unittest.main()
