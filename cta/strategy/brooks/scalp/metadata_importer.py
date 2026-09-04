"""Import already-downloaded authoritative metadata into canonical CSV files."""

from __future__ import annotations

import argparse
from collections.abc import Sequence
import hashlib
import json
import shutil
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .metadata import BlockedMetadataError
from .session import ensure_shanghai


CANONICAL_SCHEMAS: dict[str, tuple[str, ...]] = {
    "exchange_calendar.csv": (
        "exchange",
        "exchange_trade_date",
        "is_open",
        "prior_open_date",
        "next_open_date",
        "night_session_start",
        "source",
        "known_at",
    ),
    "contract_specs.csv": (
        "contract_code",
        "root_symbol",
        "exchange",
        "contract_size",
        "price_tick",
        "lot_step",
        "slippage_ticks_base",
        "last_trade_date",
        "session_template_id",
        "source",
        "known_at",
    ),
    "contract_daily.csv": (
        "contract_code",
        "exchange_trade_date",
        "pre_settlement",
        "settlement",
        "limit_rate",
        "limit_up",
        "limit_down",
        "limit_rounding_rule",
        "source",
        "source_url_or_file",
        "known_at",
        "fee_margin_schedule_id",
    ),
    "fee_margin_schedule.csv": (
        "schedule_id",
        "root_symbol",
        "contract_code",
        "effective_from",
        "effective_to",
        "margin_rate_long",
        "margin_rate_short",
        "open_fee_rate",
        "close_fee_rate",
        "close_today_fee_rate",
        "fee_per_lot_open",
        "fee_per_lot_close",
        "fee_per_lot_close_today",
        "source",
        "source_url_or_file",
        "known_at",
    ),
}
CANONICAL_FILENAMES = tuple(CANONICAL_SCHEMAS)

_COVERAGE_COLUMN = {
    "exchange_calendar.csv": "exchange_trade_date",
    "contract_specs.csv": "last_trade_date",
    "contract_daily.csv": "exchange_trade_date",
    "fee_margin_schedule.csv": "effective_from",
}

_PRIMARY_KEYS: dict[str, tuple[str, ...]] = {
    "exchange_calendar.csv": ("exchange", "exchange_trade_date"),
    "contract_specs.csv": ("contract_code",),
    "contract_daily.csv": ("contract_code", "exchange_trade_date"),
    "fee_margin_schedule.csv": (
        "schedule_id",
        "contract_code",
        "effective_from",
    ),
}


@dataclass(frozen=True)
class StagingSource:
    path: Path
    source_url_or_file: str
    downloaded_at: datetime
    coverage_start: str
    coverage_end: str
    sha256: str


def sha256_file(path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _coverage(frame: pd.DataFrame, filename: str) -> tuple[str | None, str | None]:
    if frame.empty:
        return None, None
    values = pd.to_datetime(frame[_COVERAGE_COLUMN[filename]], errors="coerce")
    if values.isna().any():
        raise BlockedMetadataError(f"invalid coverage dates in {filename}")
    return values.min().date().isoformat(), values.max().date().isoformat()


def _read_and_validate(path: Path, filename: str, *, allow_empty: bool) -> pd.DataFrame:
    if not path.is_file():
        raise BlockedMetadataError(f"required metadata file is missing: {path}")
    try:
        frame = pd.read_csv(path)
    except Exception as exc:
        raise BlockedMetadataError(f"cannot read {filename}: {exc}") from exc
    required = set(CANONICAL_SCHEMAS[filename])
    missing = sorted(required - set(frame.columns))
    if missing:
        raise BlockedMetadataError(f"{filename} missing columns: {missing}")
    if frame.empty and not allow_empty:
        raise BlockedMetadataError(f"{filename} has no rows")
    if not frame.empty:
        _validate_primary_key(frame, filename)
        for column in ("source", "known_at"):
            if frame[column].isna().any() or frame[column].astype(str).str.strip().eq("").any():
                raise BlockedMetadataError(f"{filename}.{column} contains empty values")
        for value in frame["known_at"]:
            parsed = pd.Timestamp(value)
            if parsed.tzinfo is None:
                raise BlockedMetadataError(f"{filename}.known_at must be timezone-aware")
        if "source_url_or_file" in required:
            values = frame["source_url_or_file"]
            if values.isna().any() or values.astype(str).str.strip().eq("").any():
                raise BlockedMetadataError(
                    f"{filename}.source_url_or_file contains empty values"
                )
    return frame


def _validate_primary_key(frame: pd.DataFrame, filename: str) -> None:
    columns = _PRIMARY_KEYS[filename]
    key = frame.loc[:, columns].copy()
    for column in columns:
        values = key[column]
        if filename == "fee_margin_schedule.csv" and column == "contract_code":
            values = values.fillna("")
        elif values.isna().any() or values.astype(str).str.strip().eq("").any():
            raise BlockedMetadataError(f"{filename} primary key contains empty {column}")
        if column in {"exchange_trade_date", "effective_from"}:
            parsed = pd.to_datetime(values, errors="coerce", utc=True)
            if parsed.isna().any():
                raise BlockedMetadataError(
                    f"{filename} primary key contains invalid {column}"
                )
            key[column] = parsed.astype(str)
        else:
            key[column] = values.astype(str).str.strip().str.upper()
    if key.duplicated(keep=False).any():
        duplicate = key.loc[key.duplicated(keep=False)].iloc[0].to_dict()
        raise BlockedMetadataError(
            f"{filename} duplicate primary key: {duplicate}"
        )


def _record_for(
    path: Path,
    filename: str,
    frame: pd.DataFrame,
    source: StagingSource | None,
) -> dict[str, Any]:
    minimum, maximum = _coverage(frame, filename)
    record: dict[str, Any] = {
        "sha256": sha256_file(path),
        "rows": int(len(frame)),
        "min_date": minimum,
        "max_date": maximum,
    }
    if source is not None:
        record.update(
            {
                "source_sha256": source.sha256,
                "source_url_or_file": source.source_url_or_file,
                "downloaded_at": ensure_shanghai(
                    source.downloaded_at, "downloaded_at"
                ).isoformat(),
            }
        )
    return record


def import_metadata_bundle(
    sources: dict[str, StagingSource],
    output_dir: str | Path,
) -> dict[str, Any]:
    """Validate and copy a complete canonical bundle; no data is fabricated."""
    if set(sources) != set(CANONICAL_FILENAMES):
        raise BlockedMetadataError(
            "staging bundle must contain exactly the four canonical CSV files"
        )

    validated: dict[str, pd.DataFrame] = {}
    for filename in CANONICAL_FILENAMES:
        source = sources[filename]
        if not source.source_url_or_file:
            raise BlockedMetadataError(f"source provenance is missing for {filename}")
        ensure_shanghai(source.downloaded_at, "downloaded_at")
        actual_hash = sha256_file(source.path)
        if actual_hash != source.sha256:
            raise BlockedMetadataError(f"staging sha256 mismatch for {filename}")
        frame = _read_and_validate(source.path, filename, allow_empty=False)
        minimum, maximum = _coverage(frame, filename)
        if (minimum, maximum) != (source.coverage_start, source.coverage_end):
            raise BlockedMetadataError(
                f"declared coverage mismatch for {filename}: "
                f"declared={(source.coverage_start, source.coverage_end)}, "
                f"actual={(minimum, maximum)}"
            )
        validated[filename] = frame

    output = Path(output_dir)
    output.mkdir(parents=True, exist_ok=True)
    for filename in CANONICAL_FILENAMES:
        shutil.copyfile(sources[filename].path, output / filename)

    manifest = {
        "schema_version": 1,
        "created_at": max(
            ensure_shanghai(source.downloaded_at, "downloaded_at")
            for source in sources.values()
        ).isoformat(),
        "files": {
            filename: _record_for(
                output / filename, filename, validated[filename], sources[filename]
            )
            for filename in CANONICAL_FILENAMES
        },
    }
    manifest_path = output / "manifest.json"
    manifest_path.write_text(
        json.dumps(manifest, ensure_ascii=True, sort_keys=True, indent=2) + "\n",
        encoding="utf-8",
    )
    return manifest


def verify_manifest(root: str | Path) -> dict[str, Any]:
    """Recompute hashes, rows, and coverage before any metadata is consumed."""
    directory = Path(root)
    manifest_path = directory / "manifest.json"
    if not manifest_path.is_file():
        raise BlockedMetadataError(f"manifest is missing: {manifest_path}")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise BlockedMetadataError(f"manifest cannot be read: {exc}") from exc
    records = manifest.get("files")
    if not isinstance(records, dict) or set(records) != set(CANONICAL_FILENAMES):
        raise BlockedMetadataError("manifest does not cover all four canonical CSV files")

    for filename in CANONICAL_FILENAMES:
        path = directory / filename
        frame = _read_and_validate(path, filename, allow_empty=True)
        expected = records[filename]
        actual_hash = sha256_file(path)
        if expected.get("sha256") != actual_hash:
            raise BlockedMetadataError(f"manifest sha256 mismatch for {filename}")
        minimum, maximum = _coverage(frame, filename)
        if int(expected.get("rows", -1)) != len(frame):
            raise BlockedMetadataError(f"manifest row count mismatch for {filename}")
        if expected.get("min_date") != minimum or expected.get("max_date") != maximum:
            raise BlockedMetadataError(f"manifest coverage mismatch for {filename}")
    return manifest


def validate_requested_coverage(
    root: str | Path,
    *,
    symbols: Sequence[str],
    start: str,
    end: str,
) -> dict[str, Any]:
    """Perform a fast table-level preflight before the bar-level exact audit."""
    from .metadata import MetadataBundle

    bundle = MetadataBundle.load(root)
    start_date = pd.Timestamp(start).date()
    end_date = pd.Timestamp(end).date()
    if end_date < start_date:
        raise BlockedMetadataError("end precedes start")
    roots = {_root_symbol(symbol) for symbol in symbols}
    specs = bundle.frames["contract_specs.csv"]
    available_roots = set(specs["root_symbol"].astype(str).str.upper())
    missing_roots = sorted(roots - available_roots)
    if missing_roots:
        raise BlockedMetadataError(f"contract specs missing roots: {missing_roots}")
    calendar_dates = pd.to_datetime(
        bundle.frames["exchange_calendar.csv"]["exchange_trade_date"], errors="coerce"
    ).dt.date
    if calendar_dates.isna().any() or calendar_dates.min() > start_date or calendar_dates.max() < end_date:
        raise BlockedMetadataError("exchange calendar does not span requested dates")
    daily = bundle.frames["contract_daily.csv"].copy()
    daily["_root"] = daily["contract_code"].astype(str).map(_root_symbol)
    daily["_date"] = pd.to_datetime(daily["exchange_trade_date"], errors="coerce").dt.date
    calendar = bundle.frames["exchange_calendar.csv"].copy()
    calendar["_date"] = pd.to_datetime(
        calendar["exchange_trade_date"], errors="coerce"
    ).dt.date
    calendar["_exchange"] = calendar["exchange"].astype(str).str.upper()
    calendar["_is_open"] = calendar["is_open"].map(_as_bool)
    for root_symbol in sorted(roots):
        dates = daily.loc[daily["_root"].eq(root_symbol), "_date"].dropna()
        if dates.empty or dates.min() > start_date or dates.max() < end_date:
            raise BlockedMetadataError(
                f"contract_daily does not span requested dates for {root_symbol}"
            )
        exchanges = set(
            specs.loc[
                specs["root_symbol"].astype(str).str.upper().eq(root_symbol),
                "exchange",
            ]
            .astype(str)
            .str.upper()
        )
        if len(exchanges) != 1:
            raise BlockedMetadataError(
                f"contract specs have ambiguous exchange for {root_symbol}: {sorted(exchanges)}"
            )
        exchange = next(iter(exchanges))
        open_dates = set(
            calendar.loc[
                calendar["_exchange"].eq(exchange)
                & calendar["_is_open"]
                & calendar["_date"].between(start_date, end_date),
                "_date",
            ]
        )
        covered_dates = set(dates.loc[dates.map(lambda value: start_date <= value <= end_date)])
        missing_dates = sorted(open_dates - covered_dates)
        if missing_dates:
            rendered = [value.isoformat() for value in missing_dates[:10]]
            raise BlockedMetadataError(
                f"contract_daily missing open dates for {root_symbol}: {rendered}"
            )
    return {
        "status": "READY_EXACT_BAR_AUDIT",
        "symbols": list(symbols),
        "start": start_date.isoformat(),
        "end": end_date.isoformat(),
        "manifest": bundle.manifest,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--meta-root", required=True)
    parser.add_argument("--symbols", nargs="+", required=True)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--validate-only", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        result = validate_requested_coverage(
            args.meta_root,
            symbols=args.symbols,
            start=args.start,
            end=args.end,
        )
    except (BlockedMetadataError, ValueError, OSError) as exc:
        print(json.dumps({"status": "BLOCKED_METADATA", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(json.dumps(result, ensure_ascii=False, sort_keys=True))
    return 0


def _root_symbol(symbol: str) -> str:
    head = str(symbol).split(".", 1)[0].upper()
    root = "".join(character for character in head if character.isalpha())
    if not root:
        raise ValueError(f"cannot infer root symbol: {symbol}")
    return root


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


__all__ = [
    "CANONICAL_FILENAMES",
    "CANONICAL_SCHEMAS",
    "StagingSource",
    "import_metadata_bundle",
    "main",
    "sha256_file",
    "validate_requested_coverage",
    "verify_manifest",
]


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
