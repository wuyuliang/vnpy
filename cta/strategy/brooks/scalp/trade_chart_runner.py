"""Generate one local day/5m/1m OHLCV chart for every completed RB trade."""
from __future__ import annotations

import argparse
from collections.abc import Sequence
from datetime import time
import json
from pathlib import Path
import re

import pandas as pd

from .metadata import DEFAULT_META_ROOT
from .report import plot_trade_timeframes


DEFAULT_REPORT_DIR = Path(
    "cta/strategy/brooks/report/scalp/20180103_20260416_rule_only"
)
DEFAULT_DATA_ROOT = Path("cta/data/origin/minute/RB")
DEFAULT_OUTPUT_DIR = Path("cta/strategy/brooks/report/scalp/charts")
DEFAULT_EXCHANGE_CALENDAR = DEFAULT_META_ROOT / "exchange_calendar.csv"

REQUIRED_TRADE_COLUMNS = {
    "trade_id",
    "candidate_id",
    "symbol",
    "contract_code",
    "direction",
    "rule_id",
    "entry_time",
    "entry_price",
    "exit_time",
    "exit_price",
    "exit_reason",
    "net_pnl",
    "net_r",
    "exchange_trade_date",
}

_SEGMENTS = (
    ("day_1", time(9, 0), time(10, 15), "day"),
    ("day_2", time(10, 30), time(11, 30), "day"),
    ("day_3", time(13, 30), time(15, 0), "day"),
    ("night_continuous", time(21, 0), time(23, 0), "night"),
)


def normalize_rb_chart_bars(
    raw: pd.DataFrame,
    exchange_calendar: pd.DataFrame,
) -> pd.DataFrame:
    """Normalize RB vendor bars for charting and drop segment-open snapshots."""
    required = {"datetime", "open", "high", "low", "close", "volume", "ts_code"}
    missing = sorted(required.difference(raw.columns))
    if missing:
        raise ValueError(f"RB chart source is missing columns: {missing}")
    source = raw.copy()
    parsed = pd.to_datetime(source["datetime"], errors="coerce")
    if parsed.isna().any():
        raise ValueError("RB chart source contains invalid timestamps")
    if parsed.dt.tz is None:
        parsed = parsed.dt.tz_localize("Asia/Shanghai")
    else:
        parsed = parsed.dt.tz_convert("Asia/Shanghai")
    source["bar_end"] = parsed
    trade_date_by_source, next_trade_date_by_source = _trade_date_maps(
        exchange_calendar
    )

    pieces: list[pd.DataFrame] = []
    clock = source["bar_end"].dt.time
    for segment_id, start, end, session_kind in _SEGMENTS:
        mask = clock.gt(start) & clock.le(end)
        if not mask.any():
            continue
        piece = source.loc[mask].copy()
        start_delta = pd.Timedelta(hours=start.hour, minutes=start.minute)
        piece["segment_id"] = segment_id
        piece["segment_start"] = piece["bar_end"].dt.normalize() + start_delta
        piece["session_id"] = (
            piece["bar_end"].dt.strftime("%Y%m%d") + f":{session_kind}"
        )
        source_dates = piece["bar_end"].dt.tz_localize(None).dt.normalize()
        mapping = (
            next_trade_date_by_source if session_kind == "night" else trade_date_by_source
        )
        piece["exchange_trade_date"] = source_dates.map(mapping)
        if piece["exchange_trade_date"].isna().any():
            missing_dates = sorted(
                source_dates.loc[piece["exchange_trade_date"].isna()]
                .dt.strftime("%Y-%m-%d")
                .unique()
            )
            raise ValueError(
                f"SHFE exchange calendar is missing chart dates: {missing_dates}"
            )
        piece["session_id"] = (
            piece["exchange_trade_date"].dt.strftime("%Y%m%d")
            + f":{session_kind}"
        )
        piece["contract_code"] = piece["ts_code"].astype(str).str.upper()
        pieces.append(piece)
    if not pieces:
        return pd.DataFrame(
            columns=[
                "bar_end",
                "open",
                "high",
                "low",
                "close",
                "volume",
                "exchange_trade_date",
                "session_id",
                "segment_id",
                "segment_start",
                "contract_code",
            ]
        )
    result = pd.concat(pieces, ignore_index=True).sort_values("bar_end")
    numeric = result.loc[:, ["open", "high", "low", "close", "volume"]].apply(
        pd.to_numeric,
        errors="coerce",
    )
    if numeric.isna().any().any():
        raise ValueError("RB chart source contains nonnumeric OHLCV values")
    result.loc[:, numeric.columns] = numeric
    if result["bar_end"].duplicated().any():
        raise ValueError("RB chart source contains duplicate completed minutes")
    return result.loc[
        :,
        [
            "bar_end",
            "open",
            "high",
            "low",
            "close",
            "volume",
            "exchange_trade_date",
            "session_id",
            "segment_id",
            "segment_start",
            "contract_code",
        ],
    ].reset_index(drop=True)


def _trade_date_maps(
    exchange_calendar: pd.DataFrame,
) -> tuple[dict[pd.Timestamp, pd.Timestamp], dict[pd.Timestamp, pd.Timestamp]]:
    required = {"exchange", "exchange_trade_date", "is_open", "next_open_date"}
    missing = sorted(required.difference(exchange_calendar.columns))
    if missing:
        raise ValueError(f"exchange calendar is missing columns: {missing}")
    calendar = exchange_calendar.loc[
        exchange_calendar["exchange"].astype(str).str.upper().eq("SHFE")
    ].copy()
    calendar["_date"] = pd.to_datetime(
        calendar["exchange_trade_date"], errors="coerce"
    ).dt.normalize()
    calendar["_next"] = pd.to_datetime(
        calendar["next_open_date"], errors="coerce"
    ).dt.normalize()
    open_mask = calendar["is_open"].astype(str).str.lower().isin(
        {"1", "true", "yes"}
    )
    calendar = calendar.loc[open_mask]
    if calendar["_date"].isna().any() or calendar["_next"].isna().any():
        raise ValueError("SHFE exchange calendar contains invalid open dates")
    if calendar["_date"].duplicated().any():
        raise ValueError("SHFE exchange calendar contains duplicate open dates")
    same_day = dict(zip(calendar["_date"], calendar["_date"], strict=True))
    next_day = dict(zip(calendar["_date"], calendar["_next"], strict=True))
    return same_day, next_day


def generate_rb_trade_charts(
    report_dir: str | Path = DEFAULT_REPORT_DIR,
    data_root: str | Path = DEFAULT_DATA_ROOT,
    output_dir: str | Path = DEFAULT_OUTPUT_DIR,
) -> pd.DataFrame:
    """Generate deterministic per-trade PNG files and return their index."""
    report = Path(report_dir)
    source = Path(data_root)
    output = Path(output_dir)
    trades_path = report / "trades.csv"
    if not trades_path.is_file():
        raise FileNotFoundError(f"trade report is missing: {trades_path}")
    if not source.is_dir():
        raise FileNotFoundError(f"RB minute directory is missing: {source}")

    trades = pd.read_csv(trades_path)
    missing = sorted(REQUIRED_TRADE_COLUMNS.difference(trades.columns))
    if missing:
        raise ValueError(f"trade report is missing columns: {missing}")
    trades = trades.loc[trades["symbol"].astype(str).eq("RB0.SHFE")].copy()
    if trades.empty:
        raise ValueError("trade report contains no completed RB trades")
    trades["entry_time"] = pd.to_datetime(trades["entry_time"], utc=True).dt.tz_convert(
        "Asia/Shanghai"
    )
    trades["exit_time"] = pd.to_datetime(trades["exit_time"], utc=True).dt.tz_convert(
        "Asia/Shanghai"
    )
    trades = trades.sort_values(["entry_time", "trade_id"], kind="stable").reset_index(
        drop=True
    )

    files = _dated_source_files(source)
    selected = _context_files(files, trades["entry_time"])
    raw = pd.concat((pd.read_parquet(path) for path in selected), ignore_index=True)
    exchange_calendar = pd.read_csv(DEFAULT_EXCHANGE_CALENDAR)
    bars = normalize_rb_chart_bars(raw, exchange_calendar)
    output.mkdir(parents=True, exist_ok=True)

    rows: list[dict[str, object]] = []
    for position, trade in trades.iterrows():
        sequence = position + 1
        filename = _chart_filename(sequence, trade)
        plot_trade_timeframes(bars, trade, output / filename, sequence=sequence)
        rows.append(
            {
                "sequence": sequence,
                "trade_id": trade["trade_id"],
                "candidate_id": trade["candidate_id"],
                "contract_code": trade["contract_code"],
                "direction": trade["direction"],
                "entry_time": trade["entry_time"].isoformat(),
                "exit_time": trade["exit_time"].isoformat(),
                "exit_reason": trade["exit_reason"],
                "net_pnl": trade["net_pnl"],
                "net_r": trade["net_r"],
                "chart": filename,
            }
        )
    index = pd.DataFrame(rows)
    index.to_csv(output / "index.csv", index=False)
    return index


def _dated_source_files(directory: Path) -> tuple[tuple[pd.Timestamp, Path], ...]:
    dated: list[tuple[pd.Timestamp, Path]] = []
    for path in directory.glob("*.parquet"):
        try:
            dated.append((pd.Timestamp(path.stem), path))
        except ValueError:
            continue
    if not dated:
        raise FileNotFoundError(f"no dated parquet files under {directory}")
    return tuple(sorted(dated, key=lambda item: item[0]))


def _context_files(
    files: tuple[tuple[pd.Timestamp, Path], ...],
    entry_times: pd.Series,
) -> tuple[Path, ...]:
    dates = pd.DatetimeIndex([item[0] for item in files])
    selected: set[Path] = set()
    for timestamp in entry_times:
        day = pd.Timestamp(timestamp).tz_localize(None).normalize()
        insertion = int(dates.searchsorted(day, side="left"))
        if insertion >= len(files) or dates[insertion] != day:
            raise FileNotFoundError(f"RB source file is missing for trade date {day.date()}")
        start = max(0, insertion - 24)
        stop = min(len(files), insertion + 9)
        selected.update(path for _, path in files[start:stop])
    return tuple(sorted(selected))


def _chart_filename(sequence: int, trade: pd.Series) -> str:
    timestamp = pd.Timestamp(trade["entry_time"])
    contract = re.sub(r"[^A-Za-z0-9]+", "_", str(trade["contract_code"])).strip("_")
    direction = str(trade["direction"]).upper()
    return f"{sequence:03d}_{timestamp:%Y%m%d_%H%M}_{contract}_{direction}.png"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", default=str(DEFAULT_REPORT_DIR))
    parser.add_argument("--data-root", default=str(DEFAULT_DATA_ROOT))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        index = generate_rb_trade_charts(
            report_dir=args.report_dir,
            data_root=args.data_root,
            output_dir=args.output_dir,
        )
    except (FileNotFoundError, ValueError, OSError) as exc:
        print(json.dumps({"status": "FAILED", "reason": str(exc)}, ensure_ascii=False))
        return 2
    print(
        json.dumps(
            {
                "status": "OK",
                "trade_count": int(len(index)),
                "output": str(Path(args.output_dir)),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["generate_rb_trade_charts", "normalize_rb_chart_bars"]
