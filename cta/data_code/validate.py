"""数据校验脚本：检测缺失日、重复、OHLC 不一致、零成交、乱序。

用法
----
# 单品种日线
python3 -m cta.data_code.validate day --symbol RB0

# 全品种日线（按 ranking）
python3 -m cta.data_code.validate day --max-rank 20

# 单品种分钟级
python3 -m cta.data_code.validate minute60 --symbol RB0

输出
----
- 控制台汇总
- 可选 --out CSV 落盘逐品种报告
"""
from __future__ import annotations

import argparse
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Iterable

import pandas as pd

from cta.data_code.futures_downloader import (
    DATA_DIR,
    DAY_DIR,
    MINUTE_INTERVALS,
    alpha_prefix,
)

logger = logging.getLogger("cta.data_code.validate")


@dataclass
class ValidationReport:
    symbol: str
    interval: str
    rows: int = 0
    missing_business_days: int = 0
    duplicates: int = 0
    ohlc_bad: int = 0
    zero_volume: int = 0
    out_of_order: int = 0
    date_start: str = ""
    date_end: str = ""
    notes: list[str] = field(default_factory=list)

    def to_row(self) -> dict:
        return {
            "symbol": self.symbol,
            "interval": self.interval,
            "rows": self.rows,
            "missing_business_days": self.missing_business_days,
            "duplicates": self.duplicates,
            "ohlc_bad": self.ohlc_bad,
            "zero_volume": self.zero_volume,
            "out_of_order": self.out_of_order,
            "date_start": self.date_start,
            "date_end": self.date_end,
            "notes": " | ".join(self.notes),
        }


def _check_ohlc(df: pd.DataFrame) -> int:
    if not {"open", "high", "low", "close"}.issubset(df.columns):
        return 0
    high = df["high"].astype(float)
    low = df["low"].astype(float)
    open_ = df["open"].astype(float)
    close = df["close"].astype(float)
    bad = (high < low) | (high < open_) | (high < close) | (low > open_) | (low > close)
    return int(bad.sum())


def validate_day_csv(symbol: str, csv_path: Path) -> ValidationReport:
    rep = ValidationReport(symbol=symbol, interval="day")
    if not csv_path.exists():
        rep.notes.append(f"file missing: {csv_path}")
        return rep
    try:
        df = pd.read_csv(csv_path, encoding="utf-8-sig")
    except Exception as e:  # noqa: BLE001
        rep.notes.append(f"read error: {e}")
        return rep

    if df.empty or "datetime" not in df.columns:
        rep.notes.append("empty or no datetime column")
        return rep

    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    bad_dt = int(df["datetime"].isna().sum())
    if bad_dt:
        rep.notes.append(f"unparsable datetime rows: {bad_dt}")
    df = df.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    rep.rows = len(df)
    if rep.rows == 0:
        rep.notes.append("empty after datetime parse")
        return rep

    rep.duplicates = int(df.duplicated(subset=["datetime"]).sum())
    rep.ohlc_bad = _check_ohlc(df)
    if "volume" in df.columns:
        rep.zero_volume = int((df["volume"].astype(float) <= 0).sum())
    rep.out_of_order = int((df["datetime"].diff().dt.total_seconds() < 0).sum())

    rep.date_start = df["datetime"].min().strftime("%Y-%m-%d")
    rep.date_end = df["datetime"].max().strftime("%Y-%m-%d")
    expected = pd.bdate_range(df["datetime"].min(), df["datetime"].max())
    actual = pd.DatetimeIndex(df["datetime"].dt.normalize().unique())
    rep.missing_business_days = int(len(expected.difference(actual)))
    return rep


def validate_minute_dir(symbol: str, interval: str) -> ValidationReport:
    rep = ValidationReport(symbol=symbol, interval=interval)
    prefix = alpha_prefix(symbol)
    base = DATA_DIR / "origin" / interval / prefix
    if not base.exists():
        rep.notes.append(f"dir missing: {base}")
        return rep

    files = sorted(base.glob("*.parquet"))
    if not files:
        rep.notes.append("no parquet files")
        return rep

    rep.date_start = files[0].stem
    rep.date_end = files[-1].stem
    total_rows = 0
    dup = 0
    ohlc_bad = 0
    zero_vol = 0
    ooo = 0
    for fp in files:
        try:
            df = pd.read_parquet(fp)
        except Exception as e:  # noqa: BLE001
            rep.notes.append(f"read error {fp.name}: {e}")
            continue
        if df.empty:
            continue
        total_rows += len(df)
        if "datetime" in df.columns:
            df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
            df = df.dropna(subset=["datetime"]).sort_values("datetime")
            dup += int(df.duplicated(subset=["datetime"]).sum())
            ooo += int((df["datetime"].diff().dt.total_seconds() < 0).sum())
        ohlc_bad += _check_ohlc(df)
        if "volume" in df.columns:
            zero_vol += int((df["volume"].astype(float) <= 0).sum())
    rep.rows = total_rows
    rep.duplicates = dup
    rep.ohlc_bad = ohlc_bad
    rep.zero_volume = zero_vol
    rep.out_of_order = ooo

    expected = pd.bdate_range(rep.date_start, rep.date_end)
    actual = pd.DatetimeIndex(pd.to_datetime([f.stem for f in files]))
    rep.missing_business_days = int(len(expected.difference(actual)))
    return rep


def load_ranking(max_rank: int | None, only: list[str] | None) -> list[tuple[str, str]]:
    csv = DATA_DIR.parent / "feature" / "symbols_research_ranking.csv"
    df = pd.read_csv(csv, encoding="utf-8-sig")
    df = df[["symbol", "exchange", "research_rank"]].copy()
    df["symbol"] = df["symbol"].str.strip().str.upper()
    df["exchange"] = df["exchange"].str.strip().str.upper()
    df = df.sort_values("research_rank")
    if only:
        want = {s.upper() for s in only}
        df = df[df["symbol"].isin(want)]
    if max_rank:
        df = df[df["research_rank"] <= max_rank]
    return list(zip(df["symbol"], df["exchange"]))


def validate_symbols(
    interval: str,
    symbols: Iterable[tuple[str, str]],
) -> list[ValidationReport]:
    out: list[ValidationReport] = []
    for sym, _ in symbols:
        if interval == "day":
            rep = validate_day_csv(sym, DAY_DIR / f"{sym}.csv")
        elif interval in MINUTE_INTERVALS:
            rep = validate_minute_dir(sym, interval)
        else:
            raise ValueError(f"unsupported interval: {interval}")
        out.append(rep)
    return out


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    parser = argparse.ArgumentParser(description="校验 cta/data/origin 下的数据完整性")
    parser.add_argument("interval", choices=["day", *MINUTE_INTERVALS])
    parser.add_argument("--symbol", action="append", default=None, help="限定品种（可多次）")
    parser.add_argument("--max-rank", type=int, default=None)
    parser.add_argument("--out", type=str, default=None, help="可选：输出 CSV 报告路径")
    args = parser.parse_args()

    pairs = load_ranking(args.max_rank, args.symbol)
    if not pairs:
        logger.warning("no symbols matched")
        return
    reports = validate_symbols(args.interval, pairs)
    df = pd.DataFrame([r.to_row() for r in reports])
    print(df.to_string(index=False))
    if args.out:
        Path(args.out).parent.mkdir(parents=True, exist_ok=True)
        df.to_csv(args.out, index=False, encoding="utf-8-sig")
        logger.info(f"wrote {args.out}")


if __name__ == "__main__":
    main()


__all__ = [
    "ValidationReport",
    "load_ranking",
    "validate_day_csv",
    "validate_minute_dir",
    "validate_symbols",
]
