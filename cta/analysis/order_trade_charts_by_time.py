"""Order rendered trade chart PNGs by signal type and trade time.

This utility creates signal-type folders with oldest-to-newest chart images
without redrawing them. It uses hard links by default so the ordered directory
does not duplicate the 1.5GB PNG payload on the same filesystem.
"""

from __future__ import annotations

import argparse
import csv
import json
import os
import re
import shutil
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_ROOT = Path("cta/analysis/20260628_bigger_01_allowlist_trade_charts")
DEFAULT_TRADE_CSV = Path(
    "cta/backtest/20260627_ab_bigger_01_allowlist/"
    "oot_20260628_164308_bigger_01_allowlist/raw/all_trade_details.csv"
)


def order_charts_by_trade_time(
    *,
    index_csv: Path,
    trade_csv: Path,
    charts_root: Path,
    output_dir: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Create signal_type folders with oldest-first PNG hard links."""
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
        merged["source_path"] = source_path
        ordered.append(merged)

    ordered.sort(
        key=lambda row: (
            str(row.get("signal_type") or ""),
            pd.Timestamp(row["trade_time"]),
            int(row.get("source_row") or 0),
        )
    )

    if output_dir.exists() and overwrite:
        _clear_output_dir(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)

    linked_images = 0
    existing_images = 0
    signal_type_counts: dict[str, int] = {}
    signal_type_ranks: dict[str, int] = {}
    for row in ordered:
        signal_type = _safe_slug(row.get("signal_type") or "unknown_signal_type")
        signal_type_counts[signal_type] = signal_type_counts.get(signal_type, 0) + 1
        rank = signal_type_ranks.get(signal_type, 0) + 1
        signal_type_ranks[signal_type] = rank

        source_path = Path(row["source_path"])
        signal_dir = output_dir / signal_type
        signal_dir.mkdir(parents=True, exist_ok=True)
        target_path = signal_dir / _ordered_filename(rank, row, source_path.name)
        if target_path.exists():
            existing_images += 1
            continue
        _link_or_copy(source_path, target_path)
        linked_images += 1

    summary = {
        "index_csv": str(index_csv),
        "trade_csv": str(trade_csv),
        "charts_root": str(charts_root),
        "output_dir": str(output_dir),
        "input_index_rows": len(index_rows),
        "orderable_rows": len(ordered),
        "linked_images": linked_images,
        "existing_images": existing_images,
        "signal_type_counts": signal_type_counts,
    }
    (output_dir / "order_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--charts-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--index-csv", type=Path, default=DEFAULT_ROOT / "index.csv")
    parser.add_argument("--trade-csv", type=Path, default=DEFAULT_TRADE_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_ROOT / "charts_order_time")
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    summary = order_charts_by_trade_time(
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
    """Remove previously generated ordered links and folders."""
    for path in output_dir.iterdir():
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()


if __name__ == "__main__":
    raise SystemExit(main())
