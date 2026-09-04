"""Warm the durable vendor metadata cache for a wide universe and date range.

The per-run bundle cache is keyed by the exact ``(symbols, start, end)`` window,
so every new backtest window rebuilds it and re-hits the vendor. This script
fills the *fetch-level* cache once, on disk, so later runs of any window only
download days that have never been seen.

Run it once per machine (and again occasionally to pick up new days):

    python3 -m cta.data_code.prefetch_execution_metadata \
        --start 2023-01-01 --end 2026-07-01

By default it covers every symbol in the research ranking that has local minute
data. Pass ``--symbols`` to narrow it. The run is resumable: interrupt it and
run it again, and everything already cached is skipped.
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime, timedelta
import json
from pathlib import Path
import sys

from cta.strategy.brooks.cycle_v1.backtest.data_loader import discover_symbols
from cta.strategy.brooks.cycle_v1.backtest.runner import (
    DEFAULT_DATA_ROOT,
    DEFAULT_METADATA_CACHE_ROOT,
    DEFAULT_VENDOR_CACHE_ROOT,
)
from cta.strategy.brooks.cycle_v1.backtest.market_data_update import (
    DEFAULT_RANKING_CSV,
)
from cta.strategy.brooks.cycle_v1.backtest.vendor_metadata_builder import (
    AuditedVendorMetadataClient,
    MetadataBuildError,
    prepare_execution_metadata,
)
from cta.strategy.brooks.cycle_v1.backtest.vendor_metadata_cache import (
    CachingMetadataSourceClient,
)
from cta.strategy.brooks.scalp.metadata import (
    DEFAULT_META_ROOT,
    BlockedMetadataError,
)


def _parse_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD, got {value!r}") from exc


def _ranking_roots(ranking_csv: Path) -> set[str]:
    roots: set[str] = set()
    try:
        with open(ranking_csv, encoding="utf-8-sig", newline="") as handle:
            for row in csv.DictReader(handle):
                symbol = str(row.get("symbol", "")).strip().upper()
                if not symbol:
                    continue
                roots.add(symbol[:-1] if symbol.endswith("0") else symbol)
    except OSError:
        return roots
    return roots


def _chunks(items: list, size: int):
    for index in range(0, len(items), size):
        yield items[index : index + size]


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--symbols", nargs="+", default=[])
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--meta-root", default=str(DEFAULT_META_ROOT))
    parser.add_argument(
        "--metadata-cache-root", default=str(DEFAULT_METADATA_CACHE_ROOT)
    )
    parser.add_argument("--vendor-cache-root", default=str(DEFAULT_VENDOR_CACHE_ROOT))
    parser.add_argument("--ranking-csv", default=str(DEFAULT_RANKING_CSV))
    parser.add_argument("--download-rate-limit", type=int, default=450)
    parser.add_argument(
        "--chunk-months",
        type=int,
        default=6,
        help=(
            "Split the range into windows of this many months. Smaller chunks "
            "checkpoint more often and keep each bundle build tractable."
        ),
    )
    parser.add_argument(
        "--include-unranked",
        action="store_true",
        help="Also cover discovered symbols that are absent from the ranking CSV.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    start = _parse_date(args.start, "start")
    end = _parse_date(args.end, "end")
    if end < start:
        raise ValueError("end precedes start")
    if args.chunk_months <= 0:
        raise ValueError("chunk-months must be positive")

    discovered = discover_symbols(args.data_root)
    wanted = {str(token).upper().rstrip("0") for token in args.symbols}
    if wanted:
        selected = [item for item in discovered if item.root_symbol.upper() in wanted]
        unknown = wanted - {item.root_symbol.upper() for item in selected}
        if unknown:
            raise ValueError(f"no local minute data for: {sorted(unknown)}")
    elif args.include_unranked:
        selected = list(discovered)
    else:
        ranked = _ranking_roots(Path(args.ranking_csv))
        selected = [item for item in discovered if item.root_symbol.upper() in ranked]
        skipped = sorted(
            item.root_symbol
            for item in discovered
            if item.root_symbol.upper() not in ranked
        )
        if skipped:
            print(
                json.dumps(
                    {"event": "skipped_unranked", "roots": skipped}, ensure_ascii=False
                ),
                file=sys.stderr,
            )
    if not selected:
        raise ValueError("no symbols selected; check --data-root and --ranking-csv")

    client = CachingMetadataSourceClient(
        AuditedVendorMetadataClient(rate_limit=args.download_rate_limit),
        args.vendor_cache_root,
    )

    windows: list[tuple[date, date]] = []
    cursor = start
    step = timedelta(days=31 * int(args.chunk_months))
    while cursor <= end:
        stop = min(cursor + step - timedelta(days=1), end)
        windows.append((cursor, stop))
        cursor = stop + timedelta(days=1)

    failures: list[dict[str, object]] = []
    for index, (window_start, window_end) in enumerate(windows, start=1):
        try:
            prepared = prepare_execution_metadata(
                symbols=tuple(selected),
                start=window_start,
                end=window_end,
                base_root=args.meta_root,
                cache_root=args.metadata_cache_root,
                rate_limit=args.download_rate_limit,
                allow_runtime_defaults=True,
                source_client=client,
            )
            status = {"cache_key": prepared.cache_key}
        except (BlockedMetadataError, MetadataBuildError, OSError, ValueError) as exc:
            status = {"error": str(exc)}
            failures.append(
                {
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                    "error": str(exc),
                }
            )
        print(
            json.dumps(
                {
                    "window": f"{index}/{len(windows)}",
                    "start": window_start.isoformat(),
                    "end": window_end.isoformat(),
                    "vendor_cache": client.cache_stats,
                    **status,
                },
                ensure_ascii=False,
            ),
            flush=True,
        )

    print(
        json.dumps(
            {
                "status": "COMPLETE" if not failures else "PARTIAL",
                "symbols": len(selected),
                "windows": len(windows),
                "vendor_cache": client.cache_stats,
                "vendor_cache_root": str(args.vendor_cache_root),
                "failures": failures,
            },
            ensure_ascii=False,
        )
    )
    return 0 if not failures else 1


if __name__ == "__main__":
    raise SystemExit(main())
