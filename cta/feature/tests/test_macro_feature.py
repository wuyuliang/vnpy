"""Tests for macro feature builder."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.feature.macro_feature import MacroFeatureBuilder


def _write_index_csv(path: Path, ts_code: str, closes: list[float]) -> None:
    dates = pd.bdate_range("2024-01-02", periods=len(closes))
    df = pd.DataFrame(
        {
            "symbol": [ts_code] * len(closes),
            "exchange": ["SSE"] * len(closes),
            "interval": ["day"] * len(closes),
            "datetime": [d.strftime("%Y-%m-%d 00:00:00") for d in dates],
            "open": closes,
            "high": closes,
            "low": closes,
            "close": closes,
            "volume": [1.0] * len(closes),
            "turnover": [1.0] * len(closes),
            "open_interest": [0.0] * len(closes),
        }
    )
    df.to_csv(path, index=False, encoding="utf-8-sig")


class TestMacroFeatureBuilder(unittest.TestCase):
    def test_build_and_save(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_macro_") as td:
            root = Path(td)
            day_dir = root / "origin_index" / "day"
            day_dir.mkdir(parents=True, exist_ok=True)

            _write_index_csv(day_dir / "000001_SH.csv", "000001.SH", [3000, 3010, 3020, 3030, 3040, 3050])
            _write_index_csv(day_dir / "000852_SH.csv", "000852.SH", [5000, 4990, 5010, 5030, 5020, 5040])
            _write_index_csv(day_dir / "000300_SH.csv", "000300.SH", [4000, 4010, 4020, 4015, 4025, 4035])

            builder = MacroFeatureBuilder(index_root=day_dir)
            macro = builder.build()
            self.assertFalse(macro.empty)
            for c in (
                "macro_sse_close",
                "macro_sse_ret_1d",
                "macro_csi1000_ret_5d",
                "macro_csi300_vol_20d",
                "macro_spread_csi300_csi1000_ret_5d",
            ):
                self.assertIn(c, macro.columns)

            out_path = root / "feature" / "macro" / "macro_daily.parquet"
            builder.save(macro, out_path=out_path)
            self.assertTrue(out_path.exists())

            loaded = MacroFeatureBuilder.load(out_path)
            self.assertEqual(len(loaded), len(macro))


if __name__ == "__main__":
    unittest.main()
