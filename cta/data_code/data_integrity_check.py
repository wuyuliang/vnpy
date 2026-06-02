"""Data integrity gate for ``cta/data/origin``.

目标：
1. 校验 day / minute 多频率数据是否可用于次日 sim/live；
2. 拦截缺失、重复、未来时间、最新日期滞后等硬问题；
3. 以脚本方式可被 ``daily_update`` 串联调用（失败即返回非 0 退出码）。
"""
from __future__ import annotations

import argparse
import logging
import re
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Iterable

import pandas as pd

logger = logging.getLogger(__name__)

_ALPHA_RE = re.compile(r"^([A-Za-z]+)")
_INTERVAL_MAP: dict[str, str] = {
    "day": "day",
    "d": "day",
    "60min": "minute60",
    "minute60": "minute60",
    "30min": "minute30",
    "minute30": "minute30",
    "15min": "minute15",
    "minute15": "minute15",
    "5min": "minute5",
    "minute5": "minute5",
    "min": "minute",
    "minute": "minute",
}


def _alpha_prefix(symbol: str) -> str:
    m = _ALPHA_RE.match(str(symbol).strip())
    return m.group(1).upper() if m else str(symbol).strip().upper()


def normalize_origin_interval(value: str) -> str:
    key = str(value).strip().lower()
    if key not in _INTERVAL_MAP:
        raise ValueError(f"unsupported interval token: {value!r}")
    return _INTERVAL_MAP[key]


def build_expected_last_trading_date(now: pd.Timestamp | None = None) -> date:
    ts = pd.Timestamp(now if now is not None else pd.Timestamp.now())
    return (ts.normalize() - pd.offsets.BDay(1)).date()


@dataclass(frozen=True)
class IntegrityIssue:
    code: str
    scope: str
    path: str
    detail: str


@dataclass
class IntegrityReport:
    expected_last_date: date
    checked_files: int = 0
    issues: list[IntegrityIssue] = field(default_factory=list)

    @property
    def passed(self) -> bool:
        return len(self.issues) == 0

    def add_issue(self, code: str, scope: str, path: Path, detail: str) -> None:
        self.issues.append(
            IntegrityIssue(
                code=str(code),
                scope=str(scope),
                path=str(path),
                detail=str(detail),
            )
        )


def _validate_datetime_series(
    *,
    rep: IntegrityReport,
    scope: str,
    path: Path,
    datetime_series: pd.Series,
    now: pd.Timestamp,
) -> date | None:
    dt = pd.to_datetime(datetime_series, errors="coerce")
    if dt.isna().all():
        rep.add_issue("datetime_parse_failed", scope, path, "all datetime values are invalid")
        return None
    if bool(dt.isna().any()):
        rep.add_issue("datetime_parse_partial_failed", scope, path, "contains invalid datetime values")
    if not bool(dt.is_monotonic_increasing):
        rep.add_issue("non_monotonic_datetime", scope, path, "datetime is not monotonic increasing")
    dup_cnt = int(dt.duplicated().sum())
    if dup_cnt > 0:
        rep.add_issue("duplicate_datetime", scope, path, f"duplicate rows={dup_cnt}")
    future_cnt = int((dt > now).sum())
    if future_cnt > 0:
        rep.add_issue("future_datetime", scope, path, f"future rows={future_cnt} now={now}")
    if dt.notna().any():
        return pd.Timestamp(dt.max()).date()
    return None


def _check_day_file(
    *,
    rep: IntegrityReport,
    file_path: Path,
    symbol: str,
    now: pd.Timestamp,
) -> None:
    scope = f"day:{symbol}"
    try:
        df = pd.read_csv(file_path, encoding="utf-8-sig")
    except Exception as exc:  # noqa: BLE001
        rep.add_issue("read_failed", scope, file_path, str(exc))
        return
    rep.checked_files += 1
    if df.empty:
        rep.add_issue("empty_file", scope, file_path, "day csv is empty")
        return
    if "datetime" not in df.columns:
        rep.add_issue("missing_datetime_column", scope, file_path, "missing datetime column")
        return
    latest = _validate_datetime_series(
        rep=rep,
        scope=scope,
        path=file_path,
        datetime_series=df["datetime"],
        now=now,
    )
    if latest is None:
        return
    if latest != rep.expected_last_date:
        rep.add_issue(
            "stale_latest_date",
            scope,
            file_path,
            f"latest={latest} expected={rep.expected_last_date}",
        )


def _check_minute_file(
    *,
    rep: IntegrityReport,
    file_path: Path,
    scope: str,
    now: pd.Timestamp,
) -> date | None:
    try:
        df = pd.read_parquet(file_path, columns=["datetime"])
    except Exception as exc:  # noqa: BLE001
        rep.add_issue("read_failed", scope, file_path, str(exc))
        return None
    rep.checked_files += 1
    if df.empty:
        rep.add_issue("empty_file", scope, file_path, "minute parquet is empty")
        return None
    return _validate_datetime_series(
        rep=rep,
        scope=scope,
        path=file_path,
        datetime_series=df["datetime"],
        now=now,
    )


def _check_day_interval(
    *,
    rep: IntegrityReport,
    origin_root: Path,
    required_symbols: list[str],
    now: pd.Timestamp,
) -> None:
    day_dir = origin_root / "day"
    if not day_dir.exists():
        rep.add_issue("missing_interval_dir", "day", day_dir, "day directory not found")
        return
    for symbol in required_symbols:
        fp = day_dir / f"{symbol}.csv"
        if not fp.exists():
            rep.add_issue("missing_symbol_file", f"day:{symbol}", fp, "required day file missing")
            continue
        _check_day_file(rep=rep, file_path=fp, symbol=symbol, now=now)


def _check_minute_interval(
    *,
    rep: IntegrityReport,
    origin_root: Path,
    interval_dir_name: str,
    required_symbols: list[str],
    now: pd.Timestamp,
) -> None:
    base = origin_root / interval_dir_name
    if not base.exists():
        rep.add_issue("missing_interval_dir", interval_dir_name, base, "interval directory not found")
        return
    for symbol in required_symbols:
        prefix = _alpha_prefix(symbol)
        pdir = base / prefix
        if not pdir.exists():
            rep.add_issue(
                "missing_symbol_dir",
                f"{interval_dir_name}:{symbol}",
                pdir,
                "required minute prefix dir missing",
            )
            continue
        files = sorted(pdir.glob("*.parquet"))
        if not files:
            rep.add_issue(
                "missing_symbol_files",
                f"{interval_dir_name}:{symbol}",
                pdir,
                "required minute parquet files missing",
            )
            continue
        latest: date | None = None
        scope = f"{interval_dir_name}:{symbol}"
        for fp in files:
            d = _check_minute_file(rep=rep, file_path=fp, scope=scope, now=now)
            if d is None:
                continue
            if latest is None or d > latest:
                latest = d
        if latest is None:
            continue
        if latest != rep.expected_last_date:
            rep.add_issue(
                "stale_latest_date",
                scope,
                pdir,
                f"latest={latest} expected={rep.expected_last_date}",
            )


def run_data_integrity_check(
    *,
    origin_root: Path,
    intervals: Iterable[str],
    required_symbols: Iterable[str] | None = None,
    now: pd.Timestamp | None = None,
    expected_last_date: date | None = None,
) -> IntegrityReport:
    ts_now = pd.Timestamp(now if now is not None else pd.Timestamp.now())
    exp_last = expected_last_date or build_expected_last_trading_date(ts_now)
    rep = IntegrityReport(expected_last_date=exp_last)
    symbols = [str(s).strip().upper() for s in (required_symbols or []) if str(s).strip()]

    normalized = [normalize_origin_interval(itv) for itv in intervals]
    if "day" in normalized:
        _check_day_interval(rep=rep, origin_root=origin_root, required_symbols=symbols, now=ts_now)
    for itv in normalized:
        if itv == "day":
            continue
        _check_minute_interval(
            rep=rep,
            origin_root=origin_root,
            interval_dir_name=itv,
            required_symbols=symbols,
            now=ts_now,
        )
    return rep


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="CTA origin data integrity check")
    p.add_argument("--origin-root", default="cta/data/origin")
    p.add_argument("--intervals", nargs="+", default=["day", "60min", "30min"])
    p.add_argument("--symbols", nargs="*", default=[])
    p.add_argument("--expected-last-date", default=None, help="YYYY-MM-DD; default previous business day")
    return p


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(name)s: %(message)s")
    args = _build_parser().parse_args()
    exp: date | None = None
    if args.expected_last_date:
        exp = pd.Timestamp(args.expected_last_date).date()
    rep = run_data_integrity_check(
        origin_root=Path(args.origin_root).resolve(),
        intervals=args.intervals,
        required_symbols=args.symbols,
        expected_last_date=exp,
    )
    logger.info(
        "integrity_check: passed=%s checked_files=%s expected_last_date=%s",
        rep.passed,
        rep.checked_files,
        rep.expected_last_date,
    )
    for issue in rep.issues:
        logger.error(
            "[%s] scope=%s path=%s detail=%s",
            issue.code,
            issue.scope,
            issue.path,
            issue.detail,
        )
    raise SystemExit(0 if rep.passed else 2)


if __name__ == "__main__":
    main()


__all__ = [
    "IntegrityIssue",
    "IntegrityReport",
    "build_expected_last_trading_date",
    "normalize_origin_interval",
    "run_data_integrity_check",
]
