"""Tests for cta.config.symbol_disable manifest loader & filters."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.config.symbol_disable import (
    filter_out_disabled_pairs,
    filter_out_disabled_symbols,
    load_disabled_symbols,
    mask_disabled_rows,
)


def _write_manifest(tmpdir: Path, rows: list[dict]) -> Path:
    path = tmpdir / "manifest.csv"
    pd.DataFrame(rows).to_csv(path, index=False, encoding="utf-8-sig")
    return path


class TestSymbolDisable(unittest.TestCase):
    def test_missing_manifest_returns_empty(self) -> None:
        # M2 fail-open：manifest 缺失时不应误杀
        self.assertEqual(load_disabled_symbols(manifest_path=Path("/nonexistent/path.csv")), set())

    def test_load_disabled_symbols_uppercases(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_manifest(
                Path(td),
                [
                    {"symbol": "al0", "reason": "persistent_loss"},
                    {"symbol": "TA0", "reason": "persistent_loss"},
                ],
            )
            self.assertEqual(load_disabled_symbols(manifest_path=p), {"AL0", "TA0"})

    def test_load_disabled_symbols_filters_by_reason(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_manifest(
                Path(td),
                [
                    {"symbol": "AL0", "reason": "persistent_loss"},
                    {"symbol": "I0", "reason": "cross_source_mismatch"},
                ],
            )
            self.assertEqual(
                load_disabled_symbols(manifest_path=p, reasons=["persistent_loss"]),
                {"AL0"},
            )

    def test_load_disabled_symbols_with_reasons_and_missing_reason_column_ignores_filter(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_manifest(
                Path(td),
                [
                    {"symbol": "AL0"},
                    {"symbol": "RB0"},
                ],
            )
            # reason 列缺失时不应异常；按 fail-open 保留所有 symbol
            self.assertEqual(
                load_disabled_symbols(manifest_path=p, reasons=["persistent_loss"]),
                {"AL0", "RB0"},
            )

    def test_load_disabled_symbols_parser_error_raises(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = Path(td) / "manifest_bad.csv"
            p.write_text('symbol,reason\n"AL0,persistent_loss\n', encoding="utf-8")
            with self.assertRaises(pd.errors.ParserError):
                _ = load_disabled_symbols(manifest_path=p)

    def test_filter_out_disabled_symbols_preserves_order(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_manifest(Path(td), [{"symbol": "AL0", "reason": "persistent_loss"}])
            out = filter_out_disabled_symbols(["RB0", "AL0", "CU0", "ag0"], manifest_path=p)
            self.assertEqual(out, ["RB0", "CU0", "AG0"])

    def test_filter_out_disabled_pairs_preserves_exchange(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_manifest(Path(td), [{"symbol": "AL0", "reason": "persistent_loss"}])
            pairs = [("RB0", "SHFE"), ("AL0", "SHFE"), ("CU0", None)]
            out = filter_out_disabled_pairs(pairs, manifest_path=p)
            self.assertEqual(out, [("RB0", "SHFE"), ("CU0", None)])

    def test_mask_disabled_rows_drops_matching(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_manifest(Path(td), [{"symbol": "AL0", "reason": "persistent_loss"}])
            df = pd.DataFrame({"symbol": ["RB0", "AL0", "CU0", "al0"], "x": [1, 2, 3, 4]})
            out = mask_disabled_rows(df, manifest_path=p)
            self.assertEqual(len(out), 2)
            self.assertEqual(set(out["symbol"].str.upper()), {"RB0", "CU0"})

    def test_mask_disabled_rows_no_symbol_column_passthrough(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            p = _write_manifest(Path(td), [{"symbol": "AL0", "reason": "persistent_loss"}])
            df = pd.DataFrame({"foo": [1, 2, 3]})
            out = mask_disabled_rows(df, manifest_path=p)
            self.assertEqual(len(out), 3)

    def test_real_manifest_contains_seed_entries(self) -> None:
        """20260514 复盘把 AL0/TA0/L0 加入 manifest，保证修复后这些品种被剔除。"""
        from cta.config.symbol_disable import SYMBOL_DISABLE_MANIFEST_PATH

        if SYMBOL_DISABLE_MANIFEST_PATH.exists():
            disabled = load_disabled_symbols()
            for sym in ("AL0", "TA0", "L0"):
                self.assertIn(
                    sym,
                    disabled,
                    f"{sym} should be in symbol_disable_manifest.csv (M2 seed entries)",
                )


if __name__ == "__main__":
    unittest.main()
