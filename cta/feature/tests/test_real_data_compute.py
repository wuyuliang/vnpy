"""Run compute pipeline on local raw day/minute data (real-world smoke)."""
from __future__ import annotations

import unittest

from cta.feature.compute import compute_single_symbol_features
from cta.feature.loader import (
    list_intraday_symbols,
    load_day_data,
    load_intraday_data,
    load_symbols_ranked,
)


class TestRealDataCompute(unittest.TestCase):
    def test_real_day_compute_top_symbol(self) -> None:
        ranked = load_symbols_ranked(max_rank=5)
        self.assertGreater(len(ranked), 0)
        sym = ranked.iloc[0]["symbol"]
        df = load_day_data(sym).tail(1200)
        out = compute_single_symbol_features(df, interval="day")
        self.assertEqual(len(out), len(df))
        self.assertIn("trend_score", out.columns)
        self.assertIn("regime_label", out.columns)
        self.assertIn("context_score", out.columns)

    def test_real_intraday_compute_when_available(self) -> None:
        items = list_intraday_symbols("minute")
        if not items:
            self.skipTest("no local minute parquet data found")
        sym = items[0]["symbol"]
        exch = items[0]["exchange"]
        df = load_intraday_data(sym, exch, interval="minute").tail(3000)
        out = compute_single_symbol_features(df, interval="minute")
        self.assertEqual(len(out), len(df))
        self.assertIn("tod_ret_vs_1d_1min", out.columns)
        self.assertIn("rr_score", out.columns)


if __name__ == "__main__":
    unittest.main()

