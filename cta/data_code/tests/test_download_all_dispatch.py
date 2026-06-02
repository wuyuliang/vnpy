"""Dispatch tests for download_all."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

from cta.data_code.download_all import (
    _covered_pairs_by_target,
    _is_financial_symbol,
    _normalize_interval_tokens,
    _resolve_macro_build_symbols,
)


class TestDownloadAllDispatch(unittest.TestCase):
    def test_is_financial_symbol(self) -> None:
        self.assertTrue(_is_financial_symbol("IF0"))
        self.assertTrue(_is_financial_symbol("T0"))
        self.assertFalse(_is_financial_symbol("RB0"))

    def test_normalize_interval_tokens(self) -> None:
        got = _normalize_interval_tokens(["day", "60min", "minute30", "5min", "min", "30min"])
        self.assertEqual(got, ["day", "minute60", "minute30", "minute5", "minute"])

    def test_resolve_macro_build_symbols_empty_when_index_dir_missing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_macro_missing_") as td:
            day_dir = Path(td) / "origin_index" / "day"
            out = _resolve_macro_build_symbols(
                index_day_dir=day_dir,
                references=[("000001.SH", "上证指数"), ("000300.SH", "沪深300")],
            )
            self.assertEqual(out, [])

    def test_resolve_macro_build_symbols_only_returns_existing(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_macro_existing_") as td:
            day_dir = Path(td) / "origin_index" / "day"
            day_dir.mkdir(parents=True, exist_ok=True)
            (day_dir / "000001_SH.csv").write_text("datetime,close\n2024-01-02,3000\n", encoding="utf-8")
            out = _resolve_macro_build_symbols(
                index_day_dir=day_dir,
                references=[("000001.SH", "上证指数"), ("000300.SH", "沪深300")],
            )
            self.assertEqual(out, ["000001.SH"])

    def test_covered_pairs_by_target_uses_date_end_for_incremental_judgement(self) -> None:
        import pandas as pd

        finished = pd.DataFrame(
            [
                {"symbol": "RB0", "interval": "day", "status": "success", "date_end": "2026-06-01"},
                {"symbol": "RB0", "interval": "minute60", "status": "success", "date_end": "2026-05-30"},
                {"symbol": "IF0", "interval": "minute30", "status": "skip", "date_end": "2026-06-01"},
                {"symbol": "CU0", "interval": "minute30", "status": "empty", "date_end": "2026-06-01"},
            ]
        )
        covered = _covered_pairs_by_target(finished, target_end="2026-06-01")
        self.assertIn(("RB0", "day"), covered)
        self.assertIn(("IF0", "minute30"), covered)
        self.assertNotIn(("RB0", "minute60"), covered)
        self.assertNotIn(("CU0", "minute30"), covered)


if __name__ == "__main__":
    unittest.main()
