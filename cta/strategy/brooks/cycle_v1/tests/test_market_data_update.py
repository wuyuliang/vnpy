from __future__ import annotations

from datetime import date
import json
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.backtest import market_data_update
from cta.strategy.brooks.cycle_v1.backtest.market_data_update import (
    build_parser,
    prepare_minute_data,
    resolve_download_selection,
    run_from_args,
    update_minute_data,
)
from cta.strategy.brooks.cycle_v1.backtest.download_universe import SelectedSymbol


class _FakeDownloader:
    def __init__(self) -> None:
        self.fetches: list[tuple[str, str]] = []
        self.mapping_fetches: list[tuple[str, str]] = []
        self.reference_fetches = 0

    def fetch_contract_reference(self) -> pd.DataFrame:
        self.reference_fetches += 1
        return pd.DataFrame(
            {
                "root_symbol": ["LC"],
                "exchange": ["GFEX"],
                "list_date": ["2023-07-21"],
                "delist_date": [None],
            }
        )

    def fetch_fut_mapping(self, symbol: str, exchange: str):
        self.mapping_fetches.append((symbol, exchange))
        root = symbol.removesuffix("0")
        contract = {
            "RB": "RB2605.SHF",
            "CU": "CU2603.SHF",
            "LC": "LC2601.GFE",
        }[root]
        return (
            pd.DataFrame(
                {
                    "trade_date": ["2026-01-05", "2026-01-06"],
                    "mapping_ts_code": [contract, contract],
                }
            ),
            {"SHFE": "SHF", "GFEX": "GFE"}[exchange],
        )

    def fetch_1min_day(self, contract_code: str, trade_date: str) -> pd.DataFrame:
        self.fetches.append((contract_code, trade_date))
        return pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [f"{trade_date} 09:01:00", f"{trade_date} 09:02:00"]
                ),
                "open": [100.0, 101.0],
                "high": [102.0, 103.0],
                "low": [99.0, 100.0],
                "close": [101.0, 102.0],
                "volume": [10.0, 11.0],
                "open_interest": [1000.0, 1001.0],
                "turnover": [1010.0, 1122.0],
                "ts_code": [contract_code, contract_code],
            }
        )


@pytest.mark.parametrize(
    ("start", "end"),
    [
        (date(2025, 1, 1), date(2025, 1, 1)),
        (date(2026, 7, 28), date(2026, 7, 28)),
    ],
)
def test_update_accepts_dates_outside_former_frozen_window(
    tmp_path: Path,
    start: date,
    end: date,
) -> None:
    summary = update_minute_data(
        start=start,
        end=end,
        data_root=tmp_path,
        downloader=_FakeDownloader(),
        selected_symbols=(),
    )

    assert summary["start"] == start.isoformat()
    assert summary["end"] == end.isoformat()


def test_update_rejects_inverted_download_window(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="end must not precede start"):
        update_minute_data(
            start=date(2025, 1, 2),
            end=date(2025, 1, 1),
            data_root=tmp_path,
            downloader=_FakeDownloader(),
            selected_symbols=(),
        )


def test_update_fetches_only_mapping_dates_inside_requested_window(
    tmp_path: Path,
) -> None:
    class _WindowDownloader(_FakeDownloader):
        def fetch_fut_mapping(self, symbol: str, exchange: str):
            self.mapping_fetches.append((symbol, exchange))
            return (
                pd.DataFrame(
                    {
                        "trade_date": [
                            "2024-12-31",
                            "2025-01-01",
                            "2026-06-30",
                            "2026-07-01",
                        ],
                        "mapping_ts_code": ["AG2606.SHF"] * 4,
                    }
                ),
                "SHF",
            )

    downloader = _WindowDownloader()

    summary = update_minute_data(
        start=date(2025, 1, 1),
        end=date(2026, 6, 30),
        data_root=tmp_path,
        downloader=downloader,
        selected_symbols=(SelectedSymbol("AG", "SHFE", ("top_n",)),),
    )

    assert downloader.fetches == [
        ("AG2606.SHF", "2025-01-01"),
        ("AG2606.SHF", "2026-06-30"),
    ]
    assert summary["requested_dates"] == {"AG": 2}


def test_update_downloads_old_contract_bar_for_main_contract_switch(
    tmp_path: Path,
) -> None:
    class _RollDownloader(_FakeDownloader):
        def __init__(self) -> None:
            super().__init__()
            self.roll_fetches: list[dict[str, object]] = []

        def fetch_fut_mapping(self, symbol: str, exchange: str):
            self.mapping_fetches.append((symbol, exchange))
            return (
                pd.DataFrame(
                    {
                        "trade_date": ["2026-01-05", "2026-01-06"],
                        "mapping_ts_code": ["AG2602.SHF", "AG2604.SHF"],
                    }
                ),
                "SHF",
            )

        def download_explicit_contract(self, **kwargs: object):
            self.roll_fetches.append(dict(kwargs))
            path = (
                Path(kwargs["out_root"])
                / "contract"
                / "AG"
                / "minute"
                / "AG2602_SHF.parquet"
            )
            path.parent.mkdir(parents=True, exist_ok=True)
            self.fetch_1min_day("AG2602.SHF", "2026-01-06").to_parquet(
                path,
                index=False,
            )
            return {
                "minute": SimpleNamespace(
                    status="success",
                    rows=2,
                    detail="",
                )
            }

    downloader = _RollDownloader()
    minute_root = tmp_path / "minute"

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        data_root=minute_root,
        downloader=downloader,
        selected_symbols=(SelectedSymbol("AG", "SHFE", ("top_n",)),),
    )

    assert downloader.roll_fetches == [
        {
            "symbol": "AG",
            "exchange": "SHFE",
            "contract_code": "AG2602.SHF",
            "start_date": "2026-01-06",
            "end_date": "2026-01-06",
            "intervals": ("minute",),
            "out_root": tmp_path,
            "overwrite": False,
        }
    ]
    assert summary["roll_execution_downloaded"] == {"AG": 1}
    assert (
        tmp_path / "contract" / "AG" / "minute" / "AG2602_SHF.parquet"
    ).is_file()


def test_update_accepts_cycle_warmup_start(tmp_path: Path) -> None:
    class _WarmupDownloader(_FakeDownloader):
        def fetch_fut_mapping(self, symbol: str, exchange: str):
            return (
                pd.DataFrame(
                    {
                        "trade_date": ["2025-08-30"],
                        "mapping_ts_code": ["AG2512.SHF"],
                    }
                ),
                "SHF",
            )

    summary = update_minute_data(
        start=date(2025, 8, 30),
        end=date(2025, 8, 30),
        data_root=tmp_path,
        downloader=_WarmupDownloader(),
        selected_symbols=(SelectedSymbol("AG", "SHFE", ("explicit",)),),
    )

    assert summary["downloaded"] == {"AG": 1}
    assert (tmp_path / "AG" / "2025-08-30.parquet").is_file()


def test_update_skips_existing_files_and_writes_mapped_contract_atomically(
    tmp_path: Path,
) -> None:
    existing = tmp_path / "RB" / "2026-01-05.parquet"
    existing.parent.mkdir(parents=True)
    pd.DataFrame(
        {"ts_code": ["RB2605.SHF"], "sentinel": [1]}
    ).to_parquet(existing, index=False)
    downloader = _FakeDownloader()

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        data_root=tmp_path,
        downloader=downloader,
    )

    assert summary["requested_dates"] == {"RB": 2, "CU": 2}
    assert summary["skipped"] == {"RB": 1, "CU": 0}
    assert summary["downloaded"] == {"RB": 1, "CU": 2}
    assert downloader.fetches == [
        ("CU2603.SHF", "2026-01-05"),
        ("CU2603.SHF", "2026-01-06"),
        ("RB2605.SHF", "2026-01-06"),
    ]
    assert pd.read_parquet(existing).to_dict("list") == {
        "ts_code": ["RB2605.SHF"],
        "sentinel": [1],
    }
    skipped_file = next(
        item for item in summary["files"] if item["status"] == "skipped"
    )
    assert skipped_file["path"] == str(existing)
    assert skipped_file["contract_code"] == "RB2605.SHF"
    assert len(skipped_file["sha256"]) == 64

    cu = pd.read_parquet(tmp_path / "CU0.SHF" / "2026-01-05.parquet")
    assert "datetime" not in cu
    assert cu["trade_time"].tolist() == list(
        pd.to_datetime(["2026-01-05 09:01", "2026-01-05 09:02"])
    )
    assert cu["contract_code"].tolist() == ["CU2603.SHF", "CU2603.SHF"]
    assert cu["ts_code"].tolist() == ["CU2603.SHF", "CU2603.SHF"]
    assert cu["trade_date"].tolist() == ["2026-01-05", "2026-01-05"]
    assert cu["raw_symbol"].tolist() == ["CU0.SHF", "CU0.SHF"]
    assert cu["mapping_symbol"].tolist() == ["CU.SHF", "CU.SHF"]
    assert cu["vol"].tolist() == [10.0, 11.0]
    assert cu["amount"].tolist() == [1010.0, 1122.0]
    assert cu["oi"].tolist() == [1000.0, 1001.0]
    assert not list(tmp_path.rglob("*.tmp"))


def test_existing_file_contract_must_match_daily_mapping(tmp_path: Path) -> None:
    existing = tmp_path / "RB" / "2026-01-05.parquet"
    existing.parent.mkdir(parents=True)
    pd.DataFrame({"ts_code": ["RB2601.SHF"]}).to_parquet(existing, index=False)

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=tmp_path,
        downloader=_FakeDownloader(),
        selected_symbols=(SelectedSymbol("RB", "SHFE", ("top_n",)),),
    )

    assert summary["skipped"] == {"RB": 0}
    assert summary["downloaded"] == {"RB": 0}
    assert summary["files"] == []
    assert summary["download_errors"] == [
        {
            "root_symbol": "RB",
            "exchange": "SHFE",
            "stage": "existing_identity",
            "trade_date": "2026-01-05",
            "contract_code": "RB2605.SHF",
            "reason": (
                "existing minute file contract RB2601.SHF does not match "
                "mapped contract RB2605.SHF"
            ),
        }
    ]
    assert pd.read_parquet(existing)["ts_code"].tolist() == ["RB2601.SHF"]


def test_update_repairs_only_existing_cu_generic_schema(tmp_path: Path) -> None:
    downloader = _FakeDownloader()
    generic = downloader.fetch_1min_day("CU2603.SHF", "2026-01-05")
    downloader.fetches.clear()
    cu_path = tmp_path / "CU0.SHF" / "2026-01-05.parquet"
    rb_path = tmp_path / "RB" / "2026-01-05.parquet"
    cu_path.parent.mkdir(parents=True)
    rb_path.parent.mkdir(parents=True)
    generic.to_parquet(cu_path, index=False)
    pd.DataFrame(
        {"ts_code": ["RB2605.SHF"], "sentinel": [1]}
    ).to_parquet(rb_path, index=False)

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=tmp_path,
        downloader=downloader,
    )

    assert downloader.fetches == []
    assert summary["converted_schema"] == {"CU": 1, "RB": 0}
    assert summary["skipped"] == {"CU": 0, "RB": 1}
    assert [item["status"] for item in summary["files"]] == [
        "converted_schema",
        "skipped",
    ]
    repaired = pd.read_parquet(cu_path)
    assert "trade_time" in repaired
    assert "datetime" not in repaired
    assert pd.read_parquet(rb_path).to_dict("list") == {
        "ts_code": ["RB2605.SHF"],
        "sentinel": [1],
    }


def test_bad_existing_cu_schema_is_audited_and_rb_still_downloads(
    tmp_path: Path,
) -> None:
    cu_path = tmp_path / "CU0.SHF" / "2026-01-05.parquet"
    cu_path.parent.mkdir(parents=True)
    pd.DataFrame({"sentinel": [1]}).to_parquet(cu_path, index=False)

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=tmp_path,
        downloader=_FakeDownloader(),
    )

    assert summary["downloaded"] == {"CU": 0, "RB": 1}
    assert summary["skipped"] == {"CU": 0, "RB": 0}
    assert summary["download_errors"] == [
        {
            "root_symbol": "CU",
            "exchange": "SHFE",
            "stage": "existing_schema",
            "trade_date": "2026-01-05",
            "contract_code": "CU2603.SHF",
            "reason": f"existing CU minute file has unsupported schema: {cu_path}",
        }
    ]
    assert (tmp_path / "RB" / "2026-01-05.parquet").is_file()


def test_directory_discovery_failure_does_not_guess_a_new_alias_directory(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_discovery(data_root: str | Path):
        raise ValueError(f"source directory has mixed futures roots: {data_root}")

    monkeypatch.setattr(market_data_update, "discover_symbols", fail_discovery)
    downloader = _FakeDownloader()

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=tmp_path,
        downloader=downloader,
        selected_symbols=(SelectedSymbol("LC", "GFEX", ("explicit",)),),
    )

    assert summary["downloaded"] == {"LC": 0}
    assert summary["download_errors"][0]["stage"] == "directory_discovery"
    assert downloader.mapping_fetches == []
    assert not (tmp_path / "LC").exists()


def test_target_directory_failure_is_audited_without_mapping_call(
    tmp_path: Path,
) -> None:
    blocked_root = tmp_path / "minute"
    blocked_root.write_text("not a directory", encoding="utf-8")
    downloader = _FakeDownloader()

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=blocked_root,
        downloader=downloader,
        selected_symbols=(SelectedSymbol("RB", "SHFE", ("top_n",)),),
    )

    assert summary["downloaded"] == {"RB": 0}
    assert summary["download_errors"][0]["stage"] == "directory_create"
    assert downloader.mapping_fetches == []


def test_downloaded_file_hash_failure_is_audited_without_aborting_batch(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fail_hash(_path: Path) -> str:
        raise OSError("hash unavailable")

    monkeypatch.setattr(market_data_update, "_sha256", fail_hash)

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=tmp_path,
        downloader=_FakeDownloader(),
        selected_symbols=(SelectedSymbol("RB", "SHFE", ("top_n",)),),
    )

    assert summary["downloaded"] == {"RB": 1}
    assert summary["download_errors"] == [
        {
            "root_symbol": "RB",
            "exchange": "SHFE",
            "stage": "downloaded_file_audit",
            "trade_date": "2026-01-05",
            "contract_code": "RB2605.SHF",
            "reason": "hash unavailable",
        }
    ]
    assert (tmp_path / "RB" / "2026-01-05.parquet").is_file()


def test_update_downloads_selected_symbols_with_their_real_exchanges(
    tmp_path: Path,
) -> None:
    downloader = _FakeDownloader()

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=tmp_path,
        downloader=downloader,
        selected_symbols=(
            SelectedSymbol("LC", "GFEX", ("explicit",)),
            SelectedSymbol("RB", "SHFE", ("top_n",)),
        ),
    )

    assert downloader.mapping_fetches == [("LC0", "GFEX"), ("RB0", "SHFE")]
    assert summary["downloaded"] == {"LC": 1, "RB": 1}
    lc = pd.read_parquet(tmp_path / "LC" / "2026-01-05.parquet")
    assert lc["contract_code"].tolist() == ["LC2601.GFE", "LC2601.GFE"]
    assert lc["symbol"].tolist() == ["LC0", "LC0"]
    assert lc["exchange"].tolist() == ["GFEX", "GFEX"]


def test_parser_supports_ranked_ema_union_options() -> None:
    args = build_parser().parse_args(
        [
            "--symbols",
            "LC",
            "RB0.SHFE",
            "--top-n",
            "20",
            "--include-ema-eligible",
            "--ranking-csv",
            "ranking.csv",
            "--day-root",
            "day",
        ]
    )

    assert args.symbols == ["LC", "RB0.SHFE"]
    assert args.top_n == 20
    assert args.include_ema_eligible
    assert args.ranking_csv == "ranking.csv"
    assert args.day_root == "day"


def test_no_selection_flags_preserve_legacy_cu_rb_default() -> None:
    args = build_parser().parse_args([])
    downloader = _FakeDownloader()

    selection = resolve_download_selection(args, downloader=downloader)

    assert [item.root_symbol for item in selection.selected] == ["CU", "RB"]
    assert downloader.reference_fetches == 0


def test_run_from_args_writes_union_selection_audit(tmp_path: Path) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    minute_root = tmp_path / "minute"
    audit = tmp_path / "audit.json"
    day_root.mkdir()
    pd.DataFrame(
        {
            "symbol": ["RB0", "AL0"],
            "exchange": ["SHFE", "SHFE"],
            "research_rank": [1, 2],
        }
    ).to_csv(ranking, index=False)
    pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=6, freq="D"),
            "close": [1, 2, 3, 4, 5, 6],
        }
    ).to_csv(day_root / "RB0.csv", index=False)
    pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=6, freq="D"),
            "close": [6, 5, 4, 3, 2, 1],
        }
    ).to_csv(day_root / "AL0.csv", index=False)
    args = build_parser().parse_args(
        [
            "--symbols",
            "LC",
            "--top-n",
            "1",
            "--include-ema-eligible",
            "--ranking-csv",
            str(ranking),
            "--day-root",
            str(day_root),
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-05",
            "--data-root",
            str(minute_root),
            "--audit-output",
            str(audit),
        ]
    )

    summary = run_from_args(args, downloader=_FakeDownloader())
    persisted = json.loads(audit.read_text(encoding="utf-8"))

    assert summary["selection"]["explicit"] == ["LC"]
    assert summary["selection"]["top_n"] == ["RB"]
    assert summary["selection"]["ema_eligible"] == ["RB"]
    assert [item["root_symbol"] for item in summary["selection"]["selected"]] == [
        "LC",
        "RB",
    ]
    assert summary["ranking_csv"] == str(ranking)
    assert summary["day_root"] == str(day_root)
    assert persisted == summary


def test_prepare_minute_data_exposes_structured_api(tmp_path: Path) -> None:
    ranking = tmp_path / "ranking.csv"
    day_root = tmp_path / "day"
    minute_root = tmp_path / "minute"
    audit = tmp_path / "audit.json"
    day_root.mkdir()
    pd.DataFrame(
        {"symbol": ["RB0"], "exchange": ["SHFE"], "research_rank": [1]}
    ).to_csv(ranking, index=False)
    pd.DataFrame(
        {
            "datetime": pd.date_range("2026-01-01", periods=6, freq="D"),
            "close": [1, 2, 3, 4, 5, 6],
        }
    ).to_csv(day_root / "RB0.csv", index=False)

    summary = prepare_minute_data(
        explicit=("LC",),
        top_n=1,
        include_ema_eligible=True,
        ranking_csv=ranking,
        day_root=day_root,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=minute_root,
        audit_output=audit,
        rate_limit=450,
        downloader=_FakeDownloader(),
    )

    assert [item["root_symbol"] for item in summary["selection"]["selected"]] == [
        "LC",
        "RB",
    ]
    assert summary["downloaded"] == {"LC": 1, "RB": 1}
    assert summary["ranking_csv"] == str(ranking)
    assert summary["day_root"] == str(day_root)
    assert json.loads(audit.read_text(encoding="utf-8")) == summary


def test_one_symbol_mapping_failure_is_audited_and_batch_continues(
    tmp_path: Path,
) -> None:
    class _PartiallyFailingDownloader(_FakeDownloader):
        def fetch_fut_mapping(self, symbol: str, exchange: str):
            if symbol == "LC0":
                raise RuntimeError("mapping unavailable")
            return super().fetch_fut_mapping(symbol, exchange)

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=tmp_path,
        downloader=_PartiallyFailingDownloader(),
        selected_symbols=(
            SelectedSymbol("LC", "GFEX", ("explicit",)),
            SelectedSymbol("RB", "SHFE", ("top_n",)),
        ),
    )

    assert summary["downloaded"] == {"LC": 0, "RB": 1}
    assert summary["download_errors"] == [
        {
            "root_symbol": "LC",
            "exchange": "GFEX",
            "stage": "mapping",
            "reason": "mapping unavailable",
        }
    ]


def test_update_reuses_existing_recognizable_alias_directory(tmp_path: Path) -> None:
    alias = tmp_path / "LC0.GFE"
    alias.mkdir()
    pd.DataFrame(
        {
            "contract_code": ["LC2511.GFE"],
            "datetime": pd.to_datetime(["2026-01-04 09:01"]),
        }
    ).to_parquet(alias / "2026-01-04.parquet", index=False)

    update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=tmp_path,
        downloader=_FakeDownloader(),
        selected_symbols=(SelectedSymbol("LC", "GFEX", ("explicit",)),),
    )

    assert (alias / "2026-01-05.parquet").is_file()
    assert not (tmp_path / "LC").exists()


def test_empty_explicit_selection_never_falls_back_to_legacy_symbols(
    tmp_path: Path,
) -> None:
    ranking = tmp_path / "ranking.csv"
    pd.DataFrame(
        {"symbol": ["RB0"], "exchange": ["SHFE"], "research_rank": [1]}
    ).to_csv(ranking, index=False)
    args = build_parser().parse_args(
        [
            "--symbols",
            "UNKNOWN",
            "--ranking-csv",
            str(ranking),
            "--day-root",
            str(tmp_path / "day"),
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-05",
            "--data-root",
            str(tmp_path / "minute"),
        ]
    )
    downloader = _FakeDownloader()

    summary = run_from_args(args, downloader=downloader)

    assert summary["selection"]["selected"] == []
    assert summary["selection_rejections"][0]["reason_code"] == "UNRESOLVED_EXCHANGE"
    assert summary["requested_dates"] == {}
    assert downloader.mapping_fetches == []


def test_one_bad_daily_payload_is_audited_and_later_dates_continue(
    tmp_path: Path,
) -> None:
    class _BadFirstDayDownloader(_FakeDownloader):
        def fetch_1min_day(self, contract_code: str, trade_date: str) -> pd.DataFrame:
            frame = super().fetch_1min_day(contract_code, trade_date)
            if trade_date == "2026-01-05":
                frame["ts_code"] = "WRONG2601.SHF"
            return frame

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        data_root=tmp_path,
        downloader=_BadFirstDayDownloader(),
        selected_symbols=(SelectedSymbol("RB", "SHFE", ("top_n",)),),
    )

    assert summary["downloaded"] == {"RB": 1}
    assert summary["download_errors"] == [
        {
            "root_symbol": "RB",
            "exchange": "SHFE",
            "stage": "normalize_write",
            "trade_date": "2026-01-05",
            "contract_code": "RB2605.SHF",
            "reason": "downloaded minute bars do not match mapped contract",
        }
    ]
    assert (tmp_path / "RB" / "2026-01-06.parquet").is_file()


@pytest.mark.parametrize(
    ("contract_code", "mapping_exchange", "reason"),
    [
        ("CU2605.SHF", "SHF", "mapping contract root CU does not match LC"),
        ("LC2601.SHF", "SHF", "mapping contract exchange SHFE does not match GFEX"),
    ],
)
def test_mapping_contract_identity_must_match_selected_symbol(
    tmp_path: Path,
    contract_code: str,
    mapping_exchange: str,
    reason: str,
) -> None:
    class _WrongMappingDownloader(_FakeDownloader):
        def fetch_fut_mapping(self, symbol: str, exchange: str):
            return (
                pd.DataFrame(
                    {
                        "trade_date": ["2026-01-05"],
                        "mapping_ts_code": [contract_code],
                    }
                ),
                mapping_exchange,
            )

    summary = update_minute_data(
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        data_root=tmp_path,
        downloader=_WrongMappingDownloader(),
        selected_symbols=(SelectedSymbol("LC", "GFEX", ("explicit",)),),
    )

    assert summary["downloaded"] == {"LC": 0}
    assert summary["download_errors"][0]["stage"] == "mapping"
    assert summary["download_errors"][0]["reason"] == reason


def test_contract_reference_failure_is_audited_while_top_n_remains_selected(
    tmp_path: Path,
) -> None:
    class _ReferenceFailingDownloader(_FakeDownloader):
        def fetch_contract_reference(self) -> pd.DataFrame:
            raise RuntimeError("fut_basic GFEX unavailable")

    ranking = tmp_path / "ranking.csv"
    pd.DataFrame(
        {"symbol": ["RB0"], "exchange": ["SHFE"], "research_rank": [1]}
    ).to_csv(ranking, index=False)
    args = build_parser().parse_args(
        [
            "--symbols",
            "LC",
            "--top-n",
            "1",
            "--ranking-csv",
            str(ranking),
            "--day-root",
            str(tmp_path / "day"),
            "--start",
            "2026-01-05",
            "--end",
            "2026-01-05",
        ]
    )

    selection = resolve_download_selection(
        args,
        downloader=_ReferenceFailingDownloader(),
    )

    assert [item.root_symbol for item in selection.selected] == ["RB"]
    assert [item.reason_code for item in selection.rejections] == [
        "UNRESOLVED_EXCHANGE",
        "CONTRACT_REFERENCE_ERROR",
    ]
