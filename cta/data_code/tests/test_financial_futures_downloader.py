"""Tests for financial futures downloader."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.data_code.financial_futures_downloader import FinancialFuturesDownloader


class _DummyRateLimiter:
    def acquire(self) -> None:
        return None


class _DummyPro:
    def fut_mapping(self, **kwargs) -> pd.DataFrame:
        _ = kwargs
        return pd.DataFrame(
            {
                "trade_date": ["20240102", "20240103"],
                "mapping_ts_code": ["IF2401.CFX", "IF2402.CFX"],
            }
        )

    def fut_daily(self, ts_code: str, **kwargs) -> pd.DataFrame:
        _ = kwargs
        if ts_code == "IF2401.CFX":
            return pd.DataFrame(
                {
                    "trade_date": ["20240102"],
                    "open": [3500.0],
                    "high": [3520.0],
                    "low": [3490.0],
                    "close": [3510.0],
                    "vol": [1000.0],
                    "amount": [2_000_000.0],
                    "oi": [50000.0],
                }
            )
        return pd.DataFrame(
            {
                "trade_date": ["20240103"],
                "open": [3515.0],
                "high": [3530.0],
                "low": [3500.0],
                "close": [3525.0],
                "vol": [1100.0],
                "amount": [2_100_000.0],
                "oi": [51000.0],
            }
        )

    def ft_mins(self, **kwargs) -> pd.DataFrame:
        _ = kwargs
        start = str(kwargs.get("start_date", "2024-01-02"))
        day = start[:10]
        return pd.DataFrame(
            {
                "trade_time": [f"{day} 09:30:00", f"{day} 10:30:00"],
                "open": [3500.0, 3510.0],
                "high": [3512.0, 3520.0],
                "low": [3498.0, 3508.0],
                "close": [3510.0, 3518.0],
                "vol": [100.0, 120.0],
                "amount": [100_000.0, 120_000.0],
                "oi": [50_000.0, 50_100.0],
            }
        )


class TestFinancialFuturesDownloader(unittest.TestCase):
    def test_fetch_mapping(self) -> None:
        dl = FinancialFuturesDownloader(
            rate_limiter=_DummyRateLimiter(),
            pro=_DummyPro(),
        )
        mapping = dl.fetch_mapping("IF", "2024-01-01", "2024-01-05")
        self.assertEqual(list(mapping.columns), ["trade_date", "mapping_ts_code"])
        self.assertEqual(len(mapping), 2)
        self.assertEqual(mapping.iloc[0]["trade_date"], "2024-01-02")
        self.assertEqual(mapping.iloc[0]["mapping_ts_code"], "IF2401.CFX")

    def test_fetch_continuous_day_writes_csv(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_fin_day_") as td:
            root = Path(td)
            dl = FinancialFuturesDownloader(
                rate_limiter=_DummyRateLimiter(),
                data_root=root,
                pro=_DummyPro(),
            )
            out = dl.fetch_continuous_day("IF", "2024-01-01", "2024-01-05")
            self.assertEqual(len(out), 2)
            self.assertEqual(list(out["symbol"].unique()), ["IF0"])
            p = root / "day" / "IF0.csv"
            self.assertTrue(p.exists())
            reloaded = pd.read_csv(p)
            self.assertEqual(len(reloaded), 2)

    def test_fetch_continuous_minute_writes_parquet(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_fin_min_") as td:
            root = Path(td)
            dl = FinancialFuturesDownloader(
                rate_limiter=_DummyRateLimiter(),
                data_root=root,
                pro=_DummyPro(),
            )
            dl.fetch_continuous_minute("IF", "minute60", "2024-01-02", "2024-01-03")
            day1 = root / "minute60" / "IF" / "2024-01-02.parquet"
            day2 = root / "minute60" / "IF" / "2024-01-03.parquet"
            self.assertTrue(day1.exists())
            self.assertTrue(day2.exists())


if __name__ == "__main__":
    unittest.main()
