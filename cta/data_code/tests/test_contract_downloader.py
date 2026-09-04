"""Tests for explicit-contract download and main/secondary resolver."""
from __future__ import annotations

import tempfile
import unittest
from unittest import mock
from pathlib import Path

import pandas as pd

from cta.data_code.futures_downloader import FuturesDownloader
from cta.data_code.main_secondary_resolver import resolve_main_secondary_by_mapping


class _DummyPro:
    def __init__(self) -> None:
        self.basic_exchanges: list[str] = []

    def fut_basic(self, **kwargs) -> pd.DataFrame:
        exchange = str(kwargs["exchange"])
        self.basic_exchanges.append(exchange)
        rows = {
            "GFEX": [
                {
                    "ts_code": "LC2601.GFE",
                    "symbol": "LC2601",
                    "exchange": "GFE",
                    "fut_code": "LC",
                    "list_date": "20250117",
                    "delist_date": "20260116",
                }
            ],
            "SHFE": [
                {
                    "ts_code": "RB2605.SHF",
                    "symbol": "RB2605",
                    "exchange": "SHF",
                    "fut_code": "RB",
                    "list_date": "20250516",
                    "delist_date": "20260515",
                }
            ],
        }
        return pd.DataFrame(rows.get(exchange, []))

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
    def test_mapping_api_failures_are_not_reported_as_empty_mapping(self) -> None:
        dl = FuturesDownloader(token="dummy", workers=1)
        mapping_pro = type(
            "MappingPro", (), {"fut_mapping": lambda self, **kwargs: None}
        )
        dl._pro = mapping_pro()  # noqa: SLF001

        with mock.patch(
            "cta.data_code.futures_downloader._safe_retry",
            side_effect=RuntimeError("api unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fut_mapping.*api unavailable"):
                dl.fetch_fut_mapping("LC0", "GFEX")

    def test_contract_reference_api_failures_are_not_reported_as_unknown_symbol(
        self,
    ) -> None:
        dl = FuturesDownloader(token="dummy", workers=1)
        basic_pro = type(
            "BasicPro", (), {"fut_basic": lambda self, **kwargs: None}
        )
        dl._pro = basic_pro()  # noqa: SLF001

        with mock.patch(
            "cta.data_code.futures_downloader._safe_retry",
            side_effect=RuntimeError("api unavailable"),
        ):
            with self.assertRaisesRegex(RuntimeError, "fut_basic.*GFEX.*api unavailable"):
                dl.fetch_contract_reference(("GFEX",))

    def test_contract_reference_rejects_response_from_wrong_exchange(self) -> None:
        class _WrongExchangePro:
            def fut_basic(self, **kwargs) -> pd.DataFrame:
                return pd.DataFrame(
                    {
                        "ts_code": ["LC2601.GFE"],
                        "symbol": ["LC2601"],
                        "exchange": ["SHF"],
                        "fut_code": ["LC"],
                        "list_date": ["20250117"],
                        "delist_date": ["20260116"],
                    }
                )

        dl = FuturesDownloader(token="dummy", workers=1)
        dl._pro = _WrongExchangePro()  # noqa: SLF001

        with self.assertRaisesRegex(ValueError, "response exchange SHFE.*GFEX"):
            dl.fetch_contract_reference(("GFEX",))

    def test_fetch_contract_reference_normalizes_roots_and_exchanges(self) -> None:
        dl = FuturesDownloader(token="dummy", workers=1)
        pro = _DummyPro()
        dl._pro = pro  # noqa: SLF001

        reference = dl.fetch_contract_reference(("GFEX", "SHFE"))

        self.assertEqual(pro.basic_exchanges, ["GFEX", "SHFE"])
        self.assertEqual(
            reference[
                ["contract_code", "root_symbol", "exchange", "list_date", "delist_date"]
            ].to_dict("records"),
            [
                {
                    "contract_code": "LC2601.GFE",
                    "root_symbol": "LC",
                    "exchange": "GFEX",
                    "list_date": "2025-01-17",
                    "delist_date": "2026-01-16",
                },
                {
                    "contract_code": "RB2605.SHF",
                    "root_symbol": "RB",
                    "exchange": "SHFE",
                    "list_date": "2025-05-16",
                    "delist_date": "2026-05-15",
                },
            ],
        )

    def test_fetch_contract_reference_covers_all_supported_exchanges(self) -> None:
        class _AllExchangePro:
            def __init__(self) -> None:
                self.exchanges: list[str] = []

            def fut_basic(self, **kwargs) -> pd.DataFrame:
                exchange = str(kwargs["exchange"])
                self.exchanges.append(exchange)
                contract, root, response_exchange = {
                    "SHFE": ("RB2605.SHF", "RB", "SHF"),
                    "DCE": ("I2605.DCE", "I", "DCE"),
                    "CZCE": ("MA605.ZCE", "MA", "CZC"),
                    "INE": ("SC2606.INE", "SC", "INE"),
                    "GFEX": ("LC2601.GFE", "LC", "GFE"),
                    "CFFEX": ("IF2603.CFX", "IF", "CFX"),
                }[exchange]
                return pd.DataFrame(
                    {
                        "ts_code": [contract],
                        "symbol": [contract.split(".", maxsplit=1)[0]],
                        "exchange": [response_exchange],
                        "fut_code": [root],
                        "list_date": ["20250101"],
                        "delist_date": ["20261231"],
                    }
                )

        dl = FuturesDownloader(token="dummy", workers=1)
        pro = _AllExchangePro()
        dl._pro = pro  # noqa: SLF001

        reference = dl.fetch_contract_reference()

        self.assertEqual(
            pro.exchanges,
            ["SHFE", "DCE", "CZCE", "INE", "GFEX", "CFFEX"],
        )
        self.assertEqual(
            dict(zip(reference["root_symbol"], reference["exchange"], strict=True)),
            {
                "RB": "SHFE",
                "I": "DCE",
                "MA": "CZCE",
                "SC": "INE",
                "LC": "GFEX",
                "IF": "CFFEX",
            },
        )

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

    def test_download_explicit_contract_appends_missing_requested_dates(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_contract_dl_") as td:
            root = Path(td)
            dl = FuturesDownloader(token="dummy", workers=1)
            dl._pro = _DummyPro()  # noqa: SLF001
            dl.download_explicit_contract(
                symbol="RB",
                exchange="SHFE",
                contract_code="RB2401.SHF",
                start_date="2024-01-02",
                end_date="2024-01-02",
                intervals=("minute",),
                out_root=root,
                overwrite=False,
            )

            result = dl.download_explicit_contract(
                symbol="RB",
                exchange="SHFE",
                contract_code="RB2401.SHF",
                start_date="2024-01-03",
                end_date="2024-01-03",
                intervals=("minute",),
                out_root=root,
                overwrite=False,
            )

            path = root / "contract" / "RB" / "minute" / "RB2401_SHF.parquet"
            persisted = pd.read_parquet(path)
            self.assertEqual(result["minute"].status, "success")
            self.assertEqual(
                sorted(pd.to_datetime(persisted["datetime"]).dt.date.unique()),
                [pd.Timestamp("2024-01-02").date(), pd.Timestamp("2024-01-03").date()],
            )
            self.assertEqual(len(persisted), 4)

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
