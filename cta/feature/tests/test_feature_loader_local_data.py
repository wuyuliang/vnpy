"""Tests feature_loader.py against local cta/data/feature parquet data."""
from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from cta.feature.feature_loader import (
    FEATURE_DIR,
    list_feature_intervals,
    list_feature_symbols,
    list_symbol_dates,
    load_symbol_feature_at,
    load_symbol_features,
)


def _pick_symbol(interval: str) -> str | None:
    root = FEATURE_DIR / interval
    if not root.exists():
        return None
    dirs = sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and not d.name.startswith("_") and any(d.glob("*.parquet"))
    )
    return dirs[0] if dirs else None


class TestFeatureLoaderLocalData(unittest.TestCase):
    def test_intervals_discovery(self) -> None:
        vals = list_feature_intervals()
        self.assertIsInstance(vals, list)
        self.assertIn("day", vals)

    def test_load_symbol_features_day(self) -> None:
        sym = _pick_symbol("day")
        if sym is None:
            self.skipTest("no local day feature parquet found")
        df = load_symbol_features(sym, interval="day")
        self.assertGreater(len(df), 0)
        self.assertIn("datetime", df.columns)
        self.assertTrue(pd.api.types.is_datetime64_any_dtype(df["datetime"]))

    def test_load_symbol_feature_at(self) -> None:
        sym = _pick_symbol("day")
        if sym is None:
            self.skipTest("no local day feature parquet found")
        dates = list_symbol_dates(sym, "day")
        if not dates:
            self.skipTest("symbol has no day shards")
        ts = f"{dates[-1]} 23:59:59"
        row = load_symbol_feature_at(sym, ts, interval="day", lookback="1D")
        self.assertIsNotNone(row)

    def test_list_feature_symbols(self) -> None:
        syms = list_feature_symbols("day")
        self.assertIsInstance(syms, list)
        self.assertGreater(len(syms), 0)


if __name__ == "__main__":
    unittest.main()

