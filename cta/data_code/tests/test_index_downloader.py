"""Tests for index downloader."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.data_code.index_downloader import IndexDownloader


class _DummyRateLimiter:
    def acquire(self) -> None:
        return None


class _DummyPro:
    def index_daily(self, **kwargs) -> pd.DataFrame:
        ts_code = str(kwargs.get("ts_code", "000001.SH"))
        return pd.DataFrame(
            {
                "ts_code": [ts_code, ts_code],
                "trade_date": ["20240103", "20240102"],
                "open": [3000.0, 2990.0],
                "high": [3010.0, 3005.0],
                "low": [2980.0, 2985.0],
                "close": [3005.0, 2998.0],
                "vol": [100_000.0, 110_000.0],
                "amount": [1_000_000_000.0, 1_100_000_000.0],
                "pct_chg": [0.2, -0.1],
            }
        )


class TestIndexDownloader(unittest.TestCase):
    def test_fetch_index_day_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_idx_day_") as td:
            root = Path(td)
            dl = IndexDownloader(
                rate_limiter=_DummyRateLimiter(),
                data_root=root,
                pro=_DummyPro(),
            )
            out = dl.fetch_index_day("000001.SH", "2024-01-01", "2024-01-31")
            self.assertEqual(len(out), 2)
            self.assertEqual(list(out["symbol"].unique()), ["000001.SH"])
            p = root / "day" / "000001_SH.csv"
            self.assertTrue(p.exists())
            reloaded = pd.read_csv(p)
            self.assertEqual(len(reloaded), 2)

    def test_fetch_all_uses_default_symbols(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_idx_all_") as td:
            root = Path(td)
            dl = IndexDownloader(
                rate_limiter=_DummyRateLimiter(),
                data_root=root,
                pro=_DummyPro(),
            )
            dl.fetch_all(start="2024-01-01", end="2024-01-31")
            self.assertTrue((root / "day" / "000001_SH.csv").exists())
            self.assertTrue((root / "day" / "000852_SH.csv").exists())
            self.assertTrue((root / "day" / "000300_SH.csv").exists())

    def test_load_reference_symbols_supports_file_safe_name_column(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_idx_ref_") as td:
            csv_path = Path(td) / "index_reference_symbols.csv"
            pd.DataFrame(
                [
                    {
                        "ts_code": "000001.SH",
                        "file_safe_name": "000001_SH",
                        "name": "上证指数",
                        "role": "macro_reference",
                    },
                    {
                        "ts_code": "000300.SH",
                        "file_safe_name": "000300_SH",
                        "name": "沪深300",
                        "role": "macro_reference",
                    },
                ]
            ).to_csv(csv_path, index=False, encoding="utf-8-sig")
            out = IndexDownloader.load_reference_symbols(csv_path)
            self.assertEqual(out, [("000001.SH", "上证指数"), ("000300.SH", "沪深300")])


if __name__ == "__main__":
    unittest.main()
