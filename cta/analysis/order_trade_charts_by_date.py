"""Order rendered CTA trade chart PNGs by trade day and signal type.

This utility creates a run directory shaped as YYYYMMDD/signal_type folders
under the requested output directory. It reuses already-rendered chart PNGs
through hard links, so creating a date-organized review set is fast and does
not duplicate the large image payload on the same filesystem.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_CHARTS_ROOT = Path("cta/analysis/20260628_bigger_01_allowlist_trade_charts")
DEFAULT_TRADE_CSV = Path(
    "cta/backtest/20260627_ab_bigger_01_allowlist/"
    "oot_20260628_164308_bigger_01_allowlist/raw/all_trade_details.csv"
)
DEFAULT_OUTPUT_DIR = Path("cta/analysis/20260713")


def order_charts_by_trade_date(
    *,
    index_csv: Path,
    trade_csv: Path,
    charts_root: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create trade-day/signal_type folders with oldest-first PNG hard links."""
    index_rows = _read_csv(index_csv)
    trade_rows = _read_csv(trade_csv)
    trade_by_source_row = {str(i): row for i, row in enumerate(trade_rows, start=1)}

    ordered: list[dict[str, Any]] = []
    for index_row in index_rows:
        source_row = str(index_row.get("source_row", ""))
        trade_row = trade_by_source_row.get(source_row, {})
        trade_time = _trade_time(trade_row)
        if trade_time is None:
            continue
        source_path = charts_root / str(index_row.get("image_path", ""))
        if not source_path.exists():
            continue
        merged = dict(trade_row)
        merged.update(index_row)
        merged["trade_time"] = trade_time
        merged["trade_day"] = trade_time.strftime("%Y%m%d")
        merged["source_path"] = source_path
        ordered.append(merged)

    ordered.sort(
        key=lambda row: (
            str(row.get("trade_day") or ""),
            _safe_slug(row.get("signal_type") or "unknown_signal_type"),
            pd.Timestamp(row["trade_time"]),
            int(row.get("source_row") or 0),
        )
    )

    if output_dir.exists() and overwrite:
        _clear_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    linked_images = 0
    existing_images = 0
    trade_day_counts: dict[str, int] = {}
    signal_type_counts: dict[str, int] = {}
    daily_signal_type_counts: dict[str, dict[str, int]] = defaultdict(dict)
    ranks: dict[tuple[str, str], int] = {}
    index_rows_by_day: dict[str, list[dict[str, str]]] = defaultdict(list)

    for row in ordered:
        trade_day = str(row["trade_day"])
        signal_type = _safe_slug(row.get("signal_type") or "unknown_signal_type")
        trade_day_counts[trade_day] = trade_day_counts.get(trade_day, 0) + 1
        signal_type_counts[signal_type] = signal_type_counts.get(signal_type, 0) + 1
        daily_counts = daily_signal_type_counts[trade_day]
        daily_counts[signal_type] = daily_counts.get(signal_type, 0) + 1

        rank_key = (trade_day, signal_type)
        rank = ranks.get(rank_key, 0) + 1
        ranks[rank_key] = rank

        source_path = Path(row["source_path"])
        signal_dir = output_dir / trade_day / signal_type
        signal_dir.mkdir(parents=True, exist_ok=True)
        target_path = signal_dir / _ordered_filename(rank, row, source_path.name)
        if target_path.exists():
            existing_images += 1
        else:
            _link_or_copy(source_path, target_path)
            linked_images += 1

        index_rows_by_day[trade_day].append(
            {
                "rank": str(rank),
                "source_row": str(row.get("source_row", "")),
                "trade_day": trade_day,
                "trade_time": pd.Timestamp(row["trade_time"]).strftime(
                    "%Y-%m-%d %H:%M:%S"
                ),
                "symbol": str(row.get("symbol", "")),
                "exchange": str(row.get("exchange", "")),
                "side": str(row.get("side", "")),
                "signal_type": str(row.get("signal_type", "")),
                "execution_status": str(row.get("execution_status", "")),
                "source_path": str(source_path),
                "image_path": str(target_path.relative_to(output_dir)),
            }
        )

    for trade_day, rows in index_rows_by_day.items():
        _write_index(output_dir / trade_day / "index.csv", rows)

    summary = {
        "index_csv": str(index_csv),
        "trade_csv": str(trade_csv),
        "charts_root": str(charts_root),
        "output_dir": str(output_dir),
        "input_index_rows": len(index_rows),
        "orderable_rows": len(ordered),
        "linked_images": linked_images,
        "existing_images": existing_images,
        "trade_day_counts": trade_day_counts,
        "signal_type_counts": signal_type_counts,
        "daily_signal_type_counts": dict(daily_signal_type_counts),
    }
    (output_dir / "order_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--charts-root", type=Path, default=DEFAULT_CHARTS_ROOT)
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_CHARTS_ROOT / "index.csv")
    parser.add_argument("--trade-csv", type=Path, default=DEFAULT_TRADE_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    summary = order_charts_by_trade_date(
        index_csv=args.index_csv,
        trade_csv=args.trade_csv,
        charts_root=args.charts_root,
        output_dir=args.output_dir,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _read_csv(path: Path) -> list[dict[str, str]]:
    with path.open(newline="", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def _write_index(path: Path, rows: list[dict[str, str]]) -> None:
    if not rows:
        return
    with path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=list(rows[0]))
        writer.writeheader()
        writer.writerows(rows)


def _trade_time(row: Mapping[str, object]) -> pd.Timestamp | None:
    for field in ["entry_fill_datetime", "entry_datetime"]:
        value = str(row.get(field, "")).strip()
        if not value:
            continue
        parsed = pd.to_datetime(value, errors="coerce")
        if not pd.isna(parsed):
            return pd.Timestamp(parsed)
    return None


def _ordered_filename(rank: int, row: Mapping[str, object], source_name: str) -> str:
    trade_time = pd.Timestamp(row["trade_time"]).strftime("%Y%m%d_%H%M%S")
    symbol = _safe_slug(row.get("symbol") or "UNKNOWN")
    side = _safe_slug(row.get("side") or "na")
    status = _safe_slug(row.get("execution_status") or "unknown")
    return f"{rank:06d}_{trade_time}_{symbol}_{side}_{status}_{source_name}"


def _safe_slug(value: object) -> str:
    text = str(value).strip() or "na"
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_") or "na"


def _link_or_copy(source_path: Path, target_path: Path) -> None:
    try:
        os.link(source_path, target_path)
    except OSError:
        shutil.copy2(source_path, target_path)


def _clear_output_dir(output_dir: Path) -> None:
    for path in output_dir.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
