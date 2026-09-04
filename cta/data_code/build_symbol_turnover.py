"""Build a per-root daily turnover table used for the turnover-share universe.

Turnover for one root symbol on one day is the sum over **every contract of that
root** trading that day, so a root is measured by the whole complex rather than
by whichever contract happens to be dominant.

Two sources, in priority order:

1. ``data/origin/minute/<ROOT>/<date>.parquet`` — the vendor's own ``turnover``
   column, summed across contracts. Exact.
2. ``data/origin/day/<ROOT>0.csv`` — ``volume * close * contract_size``. The day
   files carry a ``turnover`` column but it is all zeros, so it cannot be used;
   this approximation needs the contract multiplier, and a root with no known
   multiplier is reported rather than silently ranked on an unscaled number.

Note on the current local data: the minute partitions hold the dominant contract
only (one ``contract_code`` per day), so today the per-root sum equals that one
contract. The summation is written for the general case so the table stays
correct once all-contract data is available.

    python3 -m cta.data_code.build_symbol_turnover --start 2025-01-01 --end 2026-07-01
"""
from __future__ import annotations

import argparse
import csv
from datetime import date, datetime
import glob
import json
import os
from pathlib import Path

import pandas as pd

DEFAULT_MINUTE_ROOT = Path("cta/data/origin/minute")
DEFAULT_DAY_ROOT = Path("cta/data/origin/day")
DEFAULT_META_CACHE = Path("cta/strategy/brooks/cycle_v1/meta_cache")
DEFAULT_OUTPUT = Path("cta/data/origin/symbol_turnover_daily.parquet")

TURNOVER_COLUMNS = (
    "root_symbol",
    "trade_date",
    "turnover",
    "contracts",
    "source",
    "date_semantics",
)
DATE_SEMANTICS = "exchange_trade_date"
_EXCHANGE_ALIASES = {"CFX": "CFFEX", "CZC": "CZCE", "SHF": "SHFE", "ZCE": "CZCE"}


def _parse_date(value: str, label: str) -> date:
    try:
        return datetime.strptime(value, "%Y-%m-%d").date()
    except ValueError as exc:
        raise ValueError(f"{label} must be YYYY-MM-DD, got {value!r}") from exc


def _load_contract_size_history(meta_cache_root: str | Path) -> pd.DataFrame:
    """Load dated multiplier records from every valid metadata bundle."""
    records: list[pd.DataFrame] = []
    for path in sorted(Path(meta_cache_root).glob("*/contract_specs.csv")):
        try:
            frame = pd.read_csv(path)
        except (OSError, ValueError):
            continue
        required = {"root_symbol", "contract_size"}
        if not required.issubset(frame.columns):
            continue
        if "known_at" not in frame.columns and "effective_from" not in frame.columns:
            continue
        known = pd.to_datetime(
            frame.get("known_at", frame.get("effective_from")),
            errors="coerce",
            utc=True,
        )
        effective = pd.to_datetime(
            frame.get("effective_from", frame.get("known_at")),
            errors="coerce",
            utc=True,
        )
        part = pd.DataFrame(
            {
                "root_symbol": frame["root_symbol"].astype(str).str.upper(),
                "contract_size": pd.to_numeric(
                    frame["contract_size"], errors="coerce"
                ),
                "known_date": known.dt.date,
                "effective_date": effective.dt.date,
                "source_path": str(path),
            }
        ).dropna(
            subset=[
                "root_symbol",
                "contract_size",
                "known_date",
                "effective_date",
            ]
        )
        part = part.loc[part["contract_size"] > 0]
        if not part.empty:
            records.append(part)
    if not records:
        return pd.DataFrame(
            columns=[
                "root_symbol",
                "contract_size",
                "known_date",
                "effective_date",
                "source_path",
            ]
        )
    return pd.concat(records, ignore_index=True)


def _contract_sizes_asof(
    history: pd.DataFrame,
    as_of: date,
) -> dict[str, float]:
    """Resolve the latest known and effective multiplier for each root."""
    if history.empty:
        return {}
    eligible = history.loc[
        history["known_date"].le(as_of)
        & history["effective_date"].le(as_of)
    ].sort_values(
        ["root_symbol", "effective_date", "known_date", "source_path"],
        kind="stable",
    )
    if eligible.empty:
        return {}
    latest = eligible.drop_duplicates("root_symbol", keep="last")
    return dict(
        zip(
            latest["root_symbol"],
            latest["contract_size"].astype(float),
        )
    )


def load_contract_sizes(
    meta_cache_root: str | Path,
    *,
    as_of: date,
) -> dict[str, float]:
    """Map roots to multipliers known and effective by the target date."""
    return _contract_sizes_asof(
        _load_contract_size_history(meta_cache_root),
        as_of,
    )


def _load_next_open_dates(meta_cache_root: Path) -> dict[tuple[str, date], date]:
    values: dict[tuple[str, date], date] = {}
    ambiguous: set[tuple[str, date]] = set()
    for path in sorted(meta_cache_root.glob("*/exchange_calendar.csv")):
        try:
            frame = pd.read_csv(path)
        except (OSError, ValueError):
            continue
        required = {"exchange", "exchange_trade_date", "next_open_date"}
        if not required.issubset(frame.columns):
            continue
        for row in frame.itertuples(index=False):
            exchange = _EXCHANGE_ALIASES.get(
                str(getattr(row, "exchange")).strip().upper(),
                str(getattr(row, "exchange")).strip().upper(),
            )
            current = pd.to_datetime(
                getattr(row, "exchange_trade_date"), errors="coerce"
            )
            following = pd.to_datetime(getattr(row, "next_open_date"), errors="coerce")
            if pd.isna(current) or pd.isna(following):
                continue
            key = (exchange, current.date())
            next_open = following.date()
            if key in values and values[key] != next_open:
                ambiguous.add(key)
            else:
                values[key] = next_open
    for key in ambiguous:
        values.pop(key, None)
    return values


def _minute_rows(
    minute_root: Path,
    start: date,
    end: date,
    next_open_dates: dict[tuple[str, date], date],
) -> tuple[list[dict[str, object]], set[str], set[str]]:
    rows: list[dict[str, object]] = []
    covered: set[str] = set()
    missing_calendar: set[str] = set()
    if not minute_root.is_dir():
        return rows, covered, missing_calendar
    prior_natural_dates = {
        natural_date
        for (_exchange, natural_date), next_open in next_open_dates.items()
        if start <= next_open <= end
    }
    for directory in sorted(minute_root.iterdir()):
        if not directory.is_dir():
            continue
        root = directory.name.upper()
        if "." in root or root.endswith("_SMALL"):
            continue
        root_rows: list[dict[str, object]] = []
        calendar_missing = False
        for path in sorted(directory.glob("*.parquet")):
            stem = path.stem
            try:
                day = datetime.strptime(stem, "%Y-%m-%d").date()
            except ValueError:
                continue
            if not (start <= day <= end or day in prior_natural_dates):
                continue
            try:
                frame = pd.read_parquet(
                    path,
                    columns=["datetime", "contract_code", "exchange", "turnover"],
                )
            except (OSError, ValueError, KeyError):
                continue
            timestamps = pd.to_datetime(frame["datetime"], errors="coerce")
            turnover = pd.to_numeric(frame["turnover"], errors="coerce").fillna(0.0)
            exchanges = frame["exchange"].astype(str).str.strip().str.upper().map(
                lambda value: _EXCHANGE_ALIASES.get(value, value)
            )
            trade_dates: list[date | None] = []
            for timestamp, exchange, amount in zip(
                timestamps, exchanges, turnover, strict=True
            ):
                if pd.isna(timestamp) or float(amount) <= 0:
                    trade_dates.append(None)
                    continue
                natural_date = timestamp.date()
                if timestamp.hour >= 18:
                    trade_date = next_open_dates.get((exchange, natural_date))
                    if trade_date is None:
                        calendar_missing = True
                        break
                else:
                    trade_date = natural_date
                trade_dates.append(trade_date if start <= trade_date <= end else None)
            if calendar_missing:
                break
            work = pd.DataFrame(
                {
                    "trade_date": trade_dates,
                    "turnover": turnover,
                    "contract_code": frame["contract_code"].astype(str),
                }
            ).dropna(subset=["trade_date"])
            if work.empty:
                continue
            for trade_date, group in work.groupby("trade_date", sort=True):
                root_rows.append(
                    {
                        "root_symbol": root,
                        "trade_date": trade_date,
                        "turnover": float(group["turnover"].sum()),
                        "contract_codes": frozenset(group["contract_code"]),
                    }
                )
        if calendar_missing:
            missing_calendar.add(root)
            continue
        if root_rows:
            parts = pd.DataFrame(root_rows)
            for trade_date, group in parts.groupby("trade_date", sort=True):
                rows.append(
                    {
                        "root_symbol": root,
                        "trade_date": trade_date,
                        "turnover": float(group["turnover"].sum()),
                        "contracts": len(
                            set().union(*group["contract_codes"].tolist())
                        ),
                        "source": "minute",
                        "date_semantics": DATE_SEMANTICS,
                    }
                )
            covered.add(root)
    return rows, covered, missing_calendar


def _daily_rows(
    day_root: Path,
    start: date,
    end: date,
    contract_size_history: pd.DataFrame,
    skip_roots: set[str],
) -> tuple[list[dict[str, object]], list[str]]:
    rows: list[dict[str, object]] = []
    missing_multiplier: list[str] = []
    sizes_by_date: dict[date, dict[str, float]] = {}
    if not day_root.is_dir():
        return rows, missing_multiplier
    for path in sorted(day_root.glob("*.csv")):
        symbol = path.stem.upper()
        root = symbol[:-1] if symbol.endswith("0") else symbol
        if root in skip_roots:
            continue
        try:
            frame = pd.read_csv(path, encoding="utf-8-sig")
        except (OSError, ValueError, UnicodeError):
            continue
        if not {"datetime", "close", "volume"}.issubset(frame.columns):
            continue
        stamps = pd.to_datetime(frame["datetime"], errors="coerce")
        close = pd.to_numeric(frame["close"], errors="coerce")
        volume = pd.to_numeric(frame["volume"], errors="coerce")
        for stamp, price, lots in zip(stamps, close, volume):
            if pd.isna(stamp) or pd.isna(price) or pd.isna(lots):
                continue
            day = stamp.date()
            if not start <= day <= end or lots <= 0 or price <= 0:
                continue
            sizes = sizes_by_date.get(day)
            if sizes is None:
                sizes = _contract_sizes_asof(contract_size_history, day)
                sizes_by_date[day] = sizes
            size = sizes.get(root)
            if not size or size <= 0:
                missing_multiplier.append(root)
                continue
            rows.append(
                {
                    "root_symbol": root,
                    "trade_date": day,
                    "turnover": float(lots) * float(price) * float(size),
                    "contracts": 1,
                    "source": "daily_approx",
                    "date_semantics": DATE_SEMANTICS,
                }
            )
    return rows, missing_multiplier


def build_turnover_table(
    *,
    start: date,
    end: date,
    minute_root: str | Path = DEFAULT_MINUTE_ROOT,
    day_root: str | Path = DEFAULT_DAY_ROOT,
    meta_cache_root: str | Path = DEFAULT_META_CACHE,
) -> tuple[pd.DataFrame, dict[str, object]]:
    meta_root = Path(meta_cache_root)
    contract_size_history = _load_contract_size_history(meta_root)
    contract_sizes = _contract_sizes_asof(contract_size_history, end)
    next_open_dates = _load_next_open_dates(meta_root)
    minute_rows, covered, missing_calendar = _minute_rows(
        Path(minute_root), start, end, next_open_dates
    )
    daily_rows, missing = _daily_rows(
        Path(day_root), start, end, contract_size_history, covered
    )
    frame = pd.DataFrame(minute_rows + daily_rows, columns=list(TURNOVER_COLUMNS))
    if not frame.empty:
        frame = (
            frame.sort_values(["root_symbol", "trade_date"])
            .drop_duplicates(["root_symbol", "trade_date"], keep="first")
            .reset_index(drop=True)
        )
    audit = {
        "rows": int(len(frame)),
        "roots": int(frame["root_symbol"].nunique()) if not frame.empty else 0,
        "roots_from_minute": sorted(covered),
        "roots_missing_multiplier": sorted(set(missing)),
        "roots_missing_trade_calendar": sorted(missing_calendar),
        "contract_sizes_known": len(contract_sizes),
    }
    return frame, audit


def load_turnover_table(path: str | Path) -> pd.DataFrame:
    """Read a previously built table, returning an empty frame when absent."""
    target = Path(path)
    if not target.is_file():
        return pd.DataFrame(columns=list(TURNOVER_COLUMNS))
    try:
        frame = pd.read_parquet(target)
    except (OSError, ValueError, ImportError):
        return pd.DataFrame(columns=list(TURNOVER_COLUMNS))
    if "date_semantics" not in frame.columns or not frame[
        "date_semantics"
    ].astype(str).eq(DATE_SEMANTICS).all():
        return pd.DataFrame(columns=list(TURNOVER_COLUMNS))
    if "trade_date" in frame.columns:
        frame["trade_date"] = pd.to_datetime(frame["trade_date"]).dt.date
    return frame


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--start", required=True)
    parser.add_argument("--end", required=True)
    parser.add_argument("--minute-root", default=str(DEFAULT_MINUTE_ROOT))
    parser.add_argument("--day-root", default=str(DEFAULT_DAY_ROOT))
    parser.add_argument("--meta-cache-root", default=str(DEFAULT_META_CACHE))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    frame, audit = build_turnover_table(
        start=_parse_date(args.start, "start"),
        end=_parse_date(args.end, "end"),
        minute_root=args.minute_root,
        day_root=args.day_root,
        meta_cache_root=args.meta_cache_root,
    )
    target = Path(args.output)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.to_parquet(target, index=False)
    print(json.dumps({"output": str(target), **audit}, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
