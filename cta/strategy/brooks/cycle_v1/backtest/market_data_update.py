"""Download ranked and causally EMA-eligible one-minute futures data."""

from __future__ import annotations

import argparse
from collections.abc import Mapping, Sequence
from datetime import date
import hashlib
import json
from pathlib import Path
import re
from typing import Any
from uuid import uuid4

import pandas as pd

from cta.data_code.futures_downloader import FuturesDownloader

from .download_universe import (
    SelectedSymbol,
    SelectionRejection,
    UniverseSelection,
    select_ranked_universe,
)
from .data_loader import discover_symbols


DEFAULT_START = date(2025, 8, 30)
DEFAULT_END = date(2026, 7, 27)
SYMBOL_DIRECTORIES: Mapping[str, str] = {"CU": "CU0.SHF", "RB": "RB"}
DEFAULT_SELECTED_SYMBOLS = (
    SelectedSymbol("CU", "SHFE", ("legacy_default",)),
    SelectedSymbol("RB", "SHFE", ("legacy_default",)),
)
REQUIRED_COLUMNS = (
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "turnover",
    "ts_code",
)
DEFAULT_RANKING_CSV = Path("cta/feature/symbols_research_ranking.csv")
DEFAULT_DAY_ROOT = Path("cta/data/origin/day")
_CONTRACT_PATTERN = re.compile(
    r"^(?P<root>[A-Z]+)\d+\.(?P<exchange>[A-Z]+)$"
)
_EXCHANGE_ALIASES = {
    "CFX": "CFFEX",
    "CZC": "CZCE",
    "GFE": "GFEX",
    "SHF": "SHFE",
    "ZCE": "CZCE",
}


def update_minute_data(
    *,
    start: date,
    end: date,
    data_root: str | Path,
    downloader: Any,
    selected_symbols: Sequence[SelectedSymbol] | None = None,
) -> dict[str, object]:
    """Download missing natural-date files without overwriting local history."""
    if end < start:
        raise ValueError("download end must not precede start")
    root = Path(data_root)
    selected = (
        DEFAULT_SELECTED_SYMBOLS
        if selected_symbols is None
        else tuple(selected_symbols)
    )
    selected_roots = [item.root_symbol for item in selected]
    if len(selected_roots) != len(set(selected_roots)):
        raise ValueError("selected_symbols contains duplicate roots")
    summary: dict[str, object] = {
        "start": start.isoformat(),
        "end": end.isoformat(),
        "requested_dates": {},
        "skipped": {},
        "downloaded": {},
        "empty": {},
        "converted_schema": {},
        "roll_execution_downloaded": {},
        "roll_execution_skipped": {},
        "roll_execution_empty": {},
        "files": [],
        "download_errors": [],
    }
    existing_directories: dict[tuple[str, str], Path] = {}
    directory_discovery_error = ""
    if root.is_dir():
        try:
            existing_directories = {
                (item.root_symbol, item.exchange): item.source_directory
                for item in discover_symbols(root)
            }
        except (OSError, ValueError) as exc:
            if not str(exc).startswith("no recognizable futures parquet data under"):
                directory_discovery_error = str(exc)
    for item in selected:
        root_symbol = item.root_symbol
        exchange = item.exchange
        requested = skipped = downloaded = empty = converted_schema = 0
        roll_downloaded = roll_skipped = roll_empty = 0
        if directory_discovery_error:
            summary["download_errors"].append(
                {
                    "root_symbol": root_symbol,
                    "exchange": exchange,
                    "stage": "directory_discovery",
                    "reason": directory_discovery_error,
                }
            )
            summary["requested_dates"][root_symbol] = requested
            summary["skipped"][root_symbol] = skipped
            summary["downloaded"][root_symbol] = downloaded
            summary["empty"][root_symbol] = empty
            summary["converted_schema"][root_symbol] = converted_schema
            summary["roll_execution_downloaded"][root_symbol] = roll_downloaded
            summary["roll_execution_skipped"][root_symbol] = roll_skipped
            summary["roll_execution_empty"][root_symbol] = roll_empty
            continue
        target_dir = existing_directories.get(
            (root_symbol, exchange),
            root / SYMBOL_DIRECTORIES.get(root_symbol, root_symbol),
        )
        try:
            target_dir.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            summary["download_errors"].append(
                {
                    "root_symbol": root_symbol,
                    "exchange": exchange,
                    "stage": "directory_create",
                    "reason": str(exc),
                }
            )
            summary["requested_dates"][root_symbol] = requested
            summary["skipped"][root_symbol] = skipped
            summary["downloaded"][root_symbol] = downloaded
            summary["empty"][root_symbol] = empty
            summary["converted_schema"][root_symbol] = converted_schema
            summary["roll_execution_downloaded"][root_symbol] = roll_downloaded
            summary["roll_execution_skipped"][root_symbol] = roll_skipped
            summary["roll_execution_empty"][root_symbol] = roll_empty
            continue
        try:
            mapping, mapping_exchange = downloader.fetch_fut_mapping(
                f"{root_symbol}0", exchange
            )
            selected_mapping, ambiguous_dates = _select_mapping(
                mapping, start=start, end=end
            )
            selected_mapping, rejected_rows = _validate_mapping_identity(
                selected_mapping,
                root_symbol=root_symbol,
                exchange=exchange,
                mapping_exchange=str(mapping_exchange),
            )
            for reason in _mapping_degradations(ambiguous_dates, rejected_rows):
                summary["download_errors"].append(
                    {
                        "root_symbol": root_symbol,
                        "exchange": exchange,
                        "stage": "mapping_partial",
                        "reason": reason,
                    }
                )
        except (OSError, RuntimeError, ValueError) as exc:
            summary["download_errors"].append(
                {
                    "root_symbol": root_symbol,
                    "exchange": exchange,
                    "stage": "mapping",
                    "reason": str(exc),
                }
            )
            selected_mapping = pd.DataFrame(
                columns=("trade_date", "mapping_ts_code")
            )
        if selected_mapping.empty and not any(
            item["root_symbol"] == root_symbol and item["stage"] == "mapping"
            for item in summary["download_errors"]
        ):
            summary["download_errors"].append(
                {
                    "root_symbol": root_symbol,
                    "exchange": exchange,
                    "stage": "mapping",
                    "reason": "no main-contract mapping in requested interval",
                }
            )
        for row in selected_mapping.itertuples(index=False):
            trade_date = str(row.trade_date)
            contract_code = str(row.mapping_ts_code).upper()
            requested += 1
            target = target_dir / f"{trade_date}.parquet"
            if target.exists():
                converted_existing = False
                try:
                    if root_symbol == "CU" and _repair_existing_cu_schema(target):
                        converted_schema += 1
                        converted_existing = True
                except (OSError, RuntimeError, ValueError) as exc:
                    summary["download_errors"].append(
                        {
                            "root_symbol": root_symbol,
                            "exchange": exchange,
                            "stage": "existing_schema",
                            "trade_date": trade_date,
                            "contract_code": contract_code,
                            "reason": str(exc),
                        }
                    )
                    continue
                try:
                    existing_contract = _existing_contract_code(target)
                    if existing_contract != contract_code:
                        raise ValueError(
                            f"existing minute file contract {existing_contract} "
                            f"does not match mapped contract {contract_code}"
                        )
                except (OSError, RuntimeError, ValueError) as exc:
                    summary["download_errors"].append(
                        {
                            "root_symbol": root_symbol,
                            "exchange": exchange,
                            "stage": "existing_identity",
                            "trade_date": trade_date,
                            "contract_code": contract_code,
                            "reason": str(exc),
                        }
                    )
                    continue
                try:
                    digest = _sha256(target)
                except OSError as exc:
                    summary["download_errors"].append(
                        {
                            "root_symbol": root_symbol,
                            "exchange": exchange,
                            "stage": "existing_file_audit",
                            "trade_date": trade_date,
                            "contract_code": contract_code,
                            "reason": str(exc),
                        }
                    )
                    continue
                status = "converted_schema" if converted_existing else "skipped"
                if not converted_existing:
                    skipped += 1
                summary["files"].append(
                    {
                        "status": status,
                        "root_symbol": root_symbol,
                        "trade_date": trade_date,
                        "contract_code": contract_code,
                        "mapping_exchange": str(mapping_exchange),
                        "path": str(target),
                        "sha256": digest,
                    }
                )
                continue
            try:
                frame = downloader.fetch_1min_day(contract_code, trade_date)
            except (OSError, RuntimeError, ValueError) as exc:
                summary["download_errors"].append(
                    {
                        "root_symbol": root_symbol,
                        "exchange": exchange,
                        "stage": "minute",
                        "trade_date": trade_date,
                        "contract_code": contract_code,
                        "reason": str(exc),
                    }
                )
                continue
            if frame is None or frame.empty:
                empty += 1
                continue
            try:
                normalized = _normalize_download(
                    frame,
                    root_symbol=root_symbol,
                    exchange=exchange,
                    contract_code=contract_code,
                )
                _write_parquet_atomic(normalized, target)
            except (OSError, RuntimeError, ValueError) as exc:
                summary["download_errors"].append(
                    {
                        "root_symbol": root_symbol,
                        "exchange": exchange,
                        "stage": "normalize_write",
                        "trade_date": trade_date,
                        "contract_code": contract_code,
                        "reason": str(exc),
                    }
                )
                continue
            downloaded += 1
            try:
                digest = _sha256(target)
            except OSError as exc:
                summary["download_errors"].append(
                    {
                        "root_symbol": root_symbol,
                        "exchange": exchange,
                        "stage": "downloaded_file_audit",
                        "trade_date": trade_date,
                        "contract_code": contract_code,
                        "reason": str(exc),
                    }
                )
                continue
            summary["files"].append(
                {
                    "status": "downloaded",
                    "root_symbol": root_symbol,
                    "trade_date": trade_date,
                    "contract_code": contract_code,
                    "mapping_exchange": str(mapping_exchange),
                    "rows": len(normalized),
                    "path": str(target),
                    "sha256": digest,
                }
            )
        previous_contract = ""
        for row in selected_mapping.itertuples(index=False):
            contract_code = str(row.mapping_ts_code).upper()
            trade_date = str(row.trade_date)
            if previous_contract and contract_code != previous_contract:
                try:
                    result = downloader.download_explicit_contract(
                        symbol=root_symbol,
                        exchange=exchange,
                        contract_code=previous_contract,
                        start_date=trade_date,
                        end_date=trade_date,
                        intervals=("minute",),
                        out_root=_contract_data_root(root),
                        overwrite=False,
                    )["minute"]
                except (OSError, RuntimeError, ValueError) as exc:
                    summary["download_errors"].append(
                        {
                            "root_symbol": root_symbol,
                            "exchange": exchange,
                            "stage": "roll_execution_minute",
                            "trade_date": trade_date,
                            "contract_code": previous_contract,
                            "reason": str(exc),
                        }
                    )
                else:
                    status = str(result.status)
                    if status == "success":
                        roll_downloaded += 1
                    elif status == "skip":
                        roll_skipped += 1
                    elif status == "empty":
                        roll_empty += 1
                    else:
                        summary["download_errors"].append(
                            {
                                "root_symbol": root_symbol,
                                "exchange": exchange,
                                "stage": "roll_execution_minute",
                                "trade_date": trade_date,
                                "contract_code": previous_contract,
                                "reason": str(result.detail),
                            }
                        )
            previous_contract = contract_code
        summary["requested_dates"][root_symbol] = requested
        summary["skipped"][root_symbol] = skipped
        summary["downloaded"][root_symbol] = downloaded
        summary["empty"][root_symbol] = empty
        summary["converted_schema"][root_symbol] = converted_schema
        summary["roll_execution_downloaded"][root_symbol] = roll_downloaded
        summary["roll_execution_skipped"][root_symbol] = roll_skipped
        summary["roll_execution_empty"][root_symbol] = roll_empty
    return summary


def _contract_data_root(minute_root: Path) -> Path:
    return minute_root.parent if minute_root.name == "minute" else minute_root


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--symbols", nargs="+", default=[])
    parser.add_argument("--top-n", type=int, default=0)
    parser.add_argument("--include-ema-eligible", action="store_true")
    parser.add_argument("--ranking-csv", default=str(DEFAULT_RANKING_CSV))
    parser.add_argument("--day-root", default=str(DEFAULT_DAY_ROOT))
    parser.add_argument("--start", default=DEFAULT_START.isoformat())
    parser.add_argument("--end", default=DEFAULT_END.isoformat())
    parser.add_argument("--data-root", default="cta/data/origin/minute")
    parser.add_argument("--audit-output", default="")
    parser.add_argument("--rate-limit", type=int, default=450)
    return parser


def resolve_download_selection(
    args: argparse.Namespace,
    *,
    downloader: Any,
) -> UniverseSelection:
    return _resolve_download_selection_values(
        explicit=tuple(args.symbols or ()),
        top_n=args.top_n,
        include_ema_eligible=args.include_ema_eligible,
        ranking_csv=args.ranking_csv,
        day_root=args.day_root,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        downloader=downloader,
    )


def _resolve_download_selection_values(
    *,
    explicit: Sequence[str],
    top_n: int,
    include_ema_eligible: bool,
    ranking_csv: str | Path,
    day_root: str | Path,
    start: date,
    end: date,
    downloader: Any,
) -> UniverseSelection:
    if not explicit and top_n == 0 and not include_ema_eligible:
        return UniverseSelection(
            explicit=(),
            top_n=(),
            ema_eligible=(),
            selected=DEFAULT_SELECTED_SYMBOLS,
            rejections=(),
        )
    parameters = {
        "explicit": explicit,
        "top_n": top_n,
        "include_ema_eligible": include_ema_eligible,
        "ranking_csv": ranking_csv,
        "day_root": day_root,
        "start": start,
        "end": end,
    }
    selection = select_ranked_universe(
        **parameters,
        contract_reference=pd.DataFrame(),
    )
    unresolved = any(
        item.stage == "explicit" and item.reason_code == "UNRESOLVED_EXCHANGE"
        for item in selection.rejections
    )
    if unresolved:
        try:
            contract_reference = downloader.fetch_contract_reference()
        except (OSError, RuntimeError, ValueError) as exc:
            reference_rejections = tuple(
                SelectionRejection(
                    root_symbol=item.root_symbol,
                    stage="contract_reference",
                    reason_code="CONTRACT_REFERENCE_ERROR",
                    detail=str(exc),
                )
                for item in selection.rejections
                if item.stage == "explicit"
                and item.reason_code == "UNRESOLVED_EXCHANGE"
            )
            return UniverseSelection(
                explicit=selection.explicit,
                top_n=selection.top_n,
                ema_eligible=selection.ema_eligible,
                selected=selection.selected,
                rejections=selection.rejections + reference_rejections,
            )
        selection = select_ranked_universe(
            **parameters,
            contract_reference=contract_reference,
        )
    return selection


def prepare_minute_data(
    *,
    explicit: Sequence[str],
    top_n: int,
    include_ema_eligible: bool,
    ranking_csv: str | Path,
    day_root: str | Path,
    start: date,
    end: date,
    data_root: str | Path,
    audit_output: str | Path = "",
    rate_limit: int = 450,
    downloader: Any | None = None,
) -> dict[str, object]:
    active_downloader = downloader or FuturesDownloader(
        rate_limit=rate_limit,
        workers=1,
    )
    selection = _resolve_download_selection_values(
        explicit=tuple(explicit),
        top_n=top_n,
        include_ema_eligible=include_ema_eligible,
        ranking_csv=ranking_csv,
        day_root=day_root,
        start=start,
        end=end,
        downloader=active_downloader,
    )
    summary = update_minute_data(
        start=start,
        end=end,
        data_root=data_root,
        downloader=active_downloader,
        selected_symbols=selection.selected,
    )
    summary["selection"] = selection.to_audit_dict()
    summary["selection_rejections"] = [
        item.to_dict() for item in selection.rejections
    ]
    summary["ranking_csv"] = str(ranking_csv)
    summary["day_root"] = str(day_root)
    if audit_output:
        audit = Path(audit_output)
        audit.parent.mkdir(parents=True, exist_ok=True)
        audit.write_text(
            json.dumps(summary, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
            encoding="utf-8",
        )
    return summary


def run_from_args(
    args: argparse.Namespace,
    *,
    downloader: Any | None = None,
) -> dict[str, object]:
    return prepare_minute_data(
        explicit=tuple(args.symbols or ()),
        top_n=args.top_n,
        include_ema_eligible=args.include_ema_eligible,
        ranking_csv=args.ranking_csv,
        day_root=args.day_root,
        start=date.fromisoformat(args.start),
        end=date.fromisoformat(args.end),
        data_root=args.data_root,
        audit_output=args.audit_output,
        rate_limit=args.rate_limit,
        downloader=downloader,
    )


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        summary = run_from_args(args)
    except (OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"status": "ERROR", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps({"status": "READY", **summary}, ensure_ascii=False))
    return 0


def _mapping_degradations(
    ambiguous_dates: Sequence[str],
    rejected_rows: Sequence[str],
) -> list[str]:
    """Describe the mapping rows that were dropped, for the download audit."""
    reasons: list[str] = []
    if ambiguous_dates:
        shown = ",".join(ambiguous_dates[:6])
        more = "" if len(ambiguous_dates) <= 6 else f" (+{len(ambiguous_dates) - 6})"
        reasons.append(
            f"dropped {len(ambiguous_dates)} trade date(s) with more than one "
            f"mapped contract: {shown}{more}"
        )
    for reason in rejected_rows[:4]:
        reasons.append(f"dropped mapping row: {reason}")
    return reasons


def _select_mapping(
    mapping: pd.DataFrame,
    *,
    start: date,
    end: date,
) -> tuple[pd.DataFrame, list[str]]:
    """Select the in-window mapping, dropping rows we cannot trust.

    A single unusable row used to abort the whole symbol: one trade date with
    two mapped contracts (the vendor does emit these around rolls) discarded
    every other date too, and the run then failed with a bare "missing selected
    minute data". Dropping just the offending dates leaves a gap the downstream
    replay already tolerates, and the dropped dates are returned so the caller
    can record why.
    """
    required = {"trade_date", "mapping_ts_code"}
    missing = sorted(required.difference(mapping.columns))
    if missing:
        raise ValueError(f"futures mapping is missing columns: {','.join(missing)}")
    frame = mapping.loc[:, ["trade_date", "mapping_ts_code"]].copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce").dt.date
    frame = frame.loc[
        frame["trade_date"].map(lambda value: value is not pd.NaT and start <= value <= end)
    ].copy()
    ambiguous = frame.loc[frame.duplicated("trade_date", keep=False)]
    dropped: list[str] = []
    if not ambiguous.empty:
        dropped = sorted({str(value) for value in ambiguous["trade_date"]})
        frame = frame.loc[~frame.duplicated("trade_date", keep=False)].copy()
    frame["trade_date"] = frame["trade_date"].map(date.isoformat)
    frame = frame.sort_values("trade_date", kind="stable").reset_index(drop=True)
    return frame, dropped


def _validate_mapping_identity(
    mapping: pd.DataFrame,
    *,
    root_symbol: str,
    exchange: str,
    mapping_exchange: str,
) -> tuple[pd.DataFrame, list[str]]:
    """Keep only rows that provably belong to ``root_symbol``/``exchange``.

    A response-level exchange mismatch is still fatal: that means we asked for
    one symbol and the vendor answered about another, and downloading it would
    silently poison the local partitions. A single malformed or foreign row,
    on the other hand, is dropped with its reason recorded rather than taking
    the whole symbol down with it.
    """
    if mapping_exchange:
        observed_mapping_exchange = _EXCHANGE_ALIASES.get(
            mapping_exchange.upper(), mapping_exchange.upper()
        )
        if observed_mapping_exchange != exchange:
            raise ValueError(
                f"mapping response exchange {observed_mapping_exchange} does not match {exchange}"
            )
    keep: list[bool] = []
    rejected: list[str] = []
    for contract_code in mapping["mapping_ts_code"].astype(str).str.upper():
        match = _CONTRACT_PATTERN.fullmatch(contract_code)
        if match is None:
            rejected.append(f"invalid mapped contract code: {contract_code}")
            keep.append(False)
            continue
        observed_root = match.group("root")
        if observed_root != root_symbol:
            rejected.append(
                f"mapping contract root {observed_root} does not match {root_symbol}"
            )
            keep.append(False)
            continue
        observed_exchange = _EXCHANGE_ALIASES.get(
            match.group("exchange"), match.group("exchange")
        )
        if observed_exchange != exchange:
            rejected.append(
                f"mapping contract exchange {observed_exchange} does not match {exchange}"
            )
            keep.append(False)
            continue
        keep.append(True)
    if rejected:
        mapping = mapping.loc[keep].reset_index(drop=True)
    return mapping, sorted(set(rejected))


def _normalize_download(
    frame: pd.DataFrame,
    *,
    root_symbol: str,
    exchange: str,
    contract_code: str,
) -> pd.DataFrame:
    missing = sorted(set(REQUIRED_COLUMNS).difference(frame.columns))
    if missing:
        raise ValueError(f"downloaded minute bars are missing: {','.join(missing)}")
    result = frame.loc[:, REQUIRED_COLUMNS].copy()
    result["datetime"] = pd.to_datetime(result["datetime"], errors="coerce")
    if result["datetime"].isna().any():
        raise ValueError("downloaded minute bars contain invalid timestamps")
    if result["datetime"].duplicated().any():
        raise ValueError("downloaded minute bars contain duplicate timestamps")
    observed = set(result["ts_code"].astype(str).str.upper())
    if observed != {contract_code}:
        raise ValueError("downloaded minute bars do not match mapped contract")
    result["contract_code"] = contract_code
    if root_symbol == "CU":
        result = result.rename(
            columns={
                "datetime": "trade_time",
                "volume": "vol",
                "turnover": "amount",
                "open_interest": "oi",
            }
        )
        result["trade_date"] = result["trade_time"].dt.strftime("%Y-%m-%d")
        result["raw_symbol"] = "CU0.SHF"
        result["mapping_symbol"] = "CU.SHF"
        result["freq"] = "1min"
        return result.loc[
            :,
            [
                "ts_code",
                "trade_time",
                "open",
                "close",
                "high",
                "low",
                "vol",
                "amount",
                "oi",
                "trade_date",
                "raw_symbol",
                "mapping_symbol",
                "contract_code",
                "freq",
            ],
        ].reset_index(drop=True)
    result["symbol"] = f"{root_symbol}0"
    result["exchange"] = exchange
    return result.sort_values("datetime", kind="stable").reset_index(drop=True)


def _write_parquet_atomic(frame: pd.DataFrame, target: Path) -> None:
    temporary = target.with_name(f".{target.name}.{uuid4().hex}.tmp")
    try:
        frame.to_parquet(temporary, index=False)
        temporary.replace(target)
    finally:
        temporary.unlink(missing_ok=True)


def _repair_existing_cu_schema(target: Path) -> bool:
    frame = pd.read_parquet(target)
    if "trade_time" in frame:
        return False
    if "datetime" not in frame:
        raise ValueError(f"existing CU minute file has unsupported schema: {target}")
    contracts = set(frame["ts_code"].astype(str).str.upper())
    if len(contracts) != 1:
        raise ValueError(f"existing CU minute file has ambiguous contract: {target}")
    repaired = _normalize_download(
        frame,
        root_symbol="CU",
        exchange="SHFE",
        contract_code=next(iter(contracts)),
    )
    _write_parquet_atomic(repaired, target)
    return True


def _existing_contract_code(target: Path) -> str:
    frame = pd.read_parquet(target)
    contract_column = "contract_code" if "contract_code" in frame else "ts_code"
    if contract_column not in frame:
        raise ValueError(f"existing minute file has no contract identity: {target}")
    contracts = {
        value
        for value in frame[contract_column].dropna().astype(str).str.upper().str.strip()
        if value
    }
    if len(contracts) != 1:
        raise ValueError(f"existing minute file has ambiguous contract: {target}")
    return next(iter(contracts))


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "DEFAULT_END",
    "DEFAULT_START",
    "build_parser",
    "main",
    "prepare_minute_data",
    "resolve_download_selection",
    "run_from_args",
    "update_minute_data",
]
