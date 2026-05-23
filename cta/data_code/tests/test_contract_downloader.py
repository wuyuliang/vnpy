"""Tests for explicit-contract download and main/secondary resolver."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.data_code.futures_downloader import FuturesDownloader
from cta.data_code.main_secondary_resolver import resolve_main_secondary_by_mapping


class _DummyPro:
    def ft_mins(self, **kwargs) -> pd.DataFrame:
        start = str(kwargs.get("start_date", "2024-01-02 00:00:00"))
        end = str(kwargs.get("end_date", start))
        start_day = pd.Timestamp(start[:10])
        end_day = pd.Timestamp(end[:10])
        rows: list[dict[str, object]] = []
        for day in pd.date_range(start_day, end_day, freq="D"):
            d = day.strftime("%Y-%m-%d")
            rows.append(
                {
                    "trade_time": f"{d} 09:00:00",
                    "open": 100.0,
                    "high": 101.0,
                    "low": 99.0,
                    "close": 100.5,
                    "vol": 10.0,
                    "amount": 10_000.0,
                    "oi": 500.0,
                }
            )
            rows.append(
                {
                    "trade_time": f"{d} 10:00:00",
                    "open": 101.0,
                    "high": 102.0,
                    "low": 100.0,
                    "close": 101.5,
                    "vol": 12.0,
                    "amount": 12_000.0,
                    "oi": 501.0,
                }
            )
        return pd.DataFrame(rows)


class TestContractDownloader(unittest.TestCase):
    def test_download_explicit_contract_basic(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_contract_dl_") as td:
            root = Path(td)
            dl = FuturesDownloader(token="dummy", workers=1)
            dl._pro = _DummyPro()  # noqa: SLF001
            out = dl.download_explicit_contract(
                symbol="RB",
                exchange="SHFE",
                contract_code="RB2401.SHF",
                start_date="2024-01-02",
                end_date="2024-01-03",
                intervals=("minute", "minute60"),
                out_root=root,
                overwrite=True,
            )
            self.assertEqual(set(out.keys()), {"minute", "minute60"})
            self.assertEqual(out["minute"].status, "success")
            self.assertEqual(out["minute60"].status, "success")
            minute_path = root / "contract" / "RB" / "minute" / "RB2401_SHF.parquet"
            minute60_path = root / "contract" / "RB" / "minute60" / "RB2401_SHF.parquet"
            self.assertTrue(minute_path.exists())
            self.assertTrue(minute60_path.exists())
            minute_df = pd.read_parquet(minute_path)
            self.assertGreaterEqual(len(minute_df), 4)
            self.assertIn("ts_code", minute_df.columns)
            self.assertTrue((minute_df["ts_code"].astype(str) == "RB2401.SHF").all())

    def test_parse_main_and_secondary_contract(self) -> None:
        mapping = pd.DataFrame(
            {
                "trade_date": ["2024-01-02"],
                "mapping_ts_code": ["RB2401.SHF"],
            }
        )
        out = resolve_main_secondary_by_mapping(mapping, months_ahead=2)
        self.assertEqual(len(out), 1)
        self.assertEqual(str(out.iloc[0]["main_contract_code"]), "RB2401.SHF")
        self.assertEqual(str(out.iloc[0]["secondary_contract_code"]), "RB2403.SHF")

    def test_rollover_continuity(self) -> None:
        mapping = pd.DataFrame(
            {
                "trade_date": ["2024-01-10", "2024-01-11", "2024-01-12"],
                "mapping_ts_code": ["RB2401.SHF", "RB2401.SHF", "RB2405.SHF"],
            }
        )
        out = resolve_main_secondary_by_mapping(mapping, months_ahead=2)
        self.assertEqual(list(out["trade_date"]), ["2024-01-10", "2024-01-11", "2024-01-12"])
        self.assertEqual(list(out["secondary_contract_code"]), ["RB2403.SHF", "RB2403.SHF", "RB2407.SHF"])
        self.assertTrue(out["main_contract_code"].notna().all())


if __name__ == "__main__":
    unittest.main()
