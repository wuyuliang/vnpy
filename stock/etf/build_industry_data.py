from __future__ import annotations

import argparse
import logging
from collections.abc import Sequence
from pathlib import Path
from uuid import uuid4

import pandas as pd

from .industry import (
    add_industry_indicators,
    build_industry_daily,
)

LOGGER = logging.getLogger(__name__)
ETF_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = ETF_ROOT / "data" / "20260717_2018_20260717_point_in_time_live"
DEFAULT_DAILY_PATH = DEFAULT_SOURCE_ROOT / "etfs_lifecycle_clean.csv"
DEFAULT_METADATA_PATH = DEFAULT_SOURCE_ROOT / "metadata.csv"
DEFAULT_OUTPUT_PATH = ETF_ROOT / "data" / "industry.csv"


def atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    """Write a CSV through a sibling temporary file before replacing its target."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary_path = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        frame.to_csv(temporary_path, index=False)
        temporary_path.replace(path)
    finally:
        temporary_path.unlink(missing_ok=True)


def _load_daily(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {
        "symbol",
        "datetime",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "turnover",
    }
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"ETF daily data missing columns: {sorted(missing)}")
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    for column in required - {"symbol", "datetime"}:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame.duplicated(["symbol", "datetime"]).any():
        raise ValueError("ETF daily data contains duplicate symbol/date keys")
    return frame.sort_values(["symbol", "datetime"], ignore_index=True)


def _load_metadata(path: Path) -> pd.DataFrame:
    frame = pd.read_csv(path)
    required = {"symbol", "industry"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"ETF metadata missing columns: {sorted(missing)}")
    if "list_date" in frame:
        frame["list_date"] = pd.to_datetime(frame["list_date"], errors="coerce")
    return frame


def build_industry_csv(
    *,
    daily_path: Path,
    metadata_path: Path,
    output_path: Path,
    start: str | None = None,
    end: str | None = None,
) -> pd.DataFrame:
    """Build and atomically persist the daily ETF industry dataset."""
    daily = _load_daily(daily_path)
    metadata = _load_metadata(metadata_path)
    requested_start = pd.Timestamp(start) if start else daily["datetime"].min()
    requested_end = pd.Timestamp(end) if end else daily["datetime"].max()
    if requested_start > requested_end:
        raise ValueError("start date must not be after end date")
    requested_rows = daily["datetime"].between(requested_start, requested_end)
    if not requested_rows.any():
        raise ValueError("ETF daily data is empty for the requested date range")
    daily = daily.loc[daily["datetime"] <= requested_end].copy()

    symbols = sorted(daily["symbol"].unique())
    missing_symbols = sorted(set(symbols) - set(metadata["symbol"]))
    if missing_symbols:
        raise ValueError(
            "missing industry metadata for symbols: " + ", ".join(missing_symbols)
        )
    metadata = metadata.loc[metadata["symbol"].isin(symbols)].copy()
    industry_daily = build_industry_daily(daily, metadata)
    result = add_industry_indicators(industry_daily)
    result = result.loc[result["datetime"] >= requested_start].reset_index(drop=True)
    atomic_write_csv(result, output_path)
    LOGGER.info(
        "Wrote %s industry rows across %s industries from %s to %s",
        len(result),
        result["industry"].nunique(),
        result["datetime"].min().date(),
        result["datetime"].max().date(),
    )
    return result


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily", type=Path, default=DEFAULT_DAILY_PATH)
    parser.add_argument("--metadata", type=Path, default=DEFAULT_METADATA_PATH)
    parser.add_argument("--output", type=Path, default=DEFAULT_OUTPUT_PATH)
    parser.add_argument("--start")
    parser.add_argument("--end")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = _build_parser().parse_args(argv)
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    build_industry_csv(
        daily_path=args.daily,
        metadata_path=args.metadata,
        output_path=args.output,
        start=args.start,
        end=args.end,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
