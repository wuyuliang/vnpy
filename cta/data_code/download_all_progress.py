"""Tracking/progress helpers for ``download_all``."""
from __future__ import annotations

import logging
import shutil
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Dict, List, Set, Tuple

import pandas as pd

from cta.data_code.futures_downloader import DATA_DIR, DownloadResult

logger = logging.getLogger(__name__)

CTA_ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = Path(__file__).resolve().parent

TRACKING_DATE = datetime.now().strftime("%Y%m%d")
FINISHED_CSV = DATA_DIR / f"finished_{TRACKING_DATE}.csv"
EMPTY_CSV = DATA_DIR / f"empty_{TRACKING_DATE}.csv"

LEGACY_FINISHED_CSVS = (
    DATA_DIR / "data_finished.csv",
    DATA_DIR / "finished.csv",
    CODE_DIR / "finished.csv",
)
LEGACY_EMPTY_CSVS = (
    DATA_DIR / "data_empty.csv",
    DATA_DIR / "empty.csv",
    CODE_DIR / "empty.csv",
)

FINISHED_COLS = [
    "symbol",
    "exchange",
    "interval",
    "status",
    "rows",
    "date_start",
    "date_end",
    "completed_at",
    "detail",
]
EMPTY_COLS = [
    "symbol",
    "exchange",
    "interval",
    "date_start",
    "date_end",
    "count",
    "reason",
    "recorded_at",
]

MAX_EMPTY_DATE = "2026-04-17"
EMPTY_GAP_DAYS = 60

_finished_lock = Lock()
_empty_lock = Lock()


def _migrate_tracking_csv(target: Path, legacy_paths: Tuple[Path, ...]) -> None:
    if target.exists():
        return
    pattern = f"{target.stem.split('_')[0]}_*.csv"
    if any(DATA_DIR.glob(pattern)):
        return
    for legacy in legacy_paths:
        if legacy.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy, target)
                logger.info("migrate tracking csv: %s -> %s", legacy, target)
            except Exception as e:  # noqa: BLE001
                logger.warning("migrate tracking csv failed: %s -> %s: %s", legacy, target, e)
            return


def migrate_legacy_tracking_files() -> None:
    _migrate_tracking_csv(FINISHED_CSV, LEGACY_FINISHED_CSVS)
    _migrate_tracking_csv(EMPTY_CSV, LEGACY_EMPTY_CSVS)


def _load_csv(path: Path, cols: List[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=cols)
    df = pd.read_csv(path)
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    return df[cols]


def _tracking_files(prefix: str, legacy_paths: Tuple[Path, ...]) -> List[Path]:
    paths: List[Path] = []
    paths.extend(sorted(p for p in DATA_DIR.glob(f"{prefix}_*.csv") if p.is_file()))
    for p in legacy_paths:
        if p.exists():
            paths.append(p)
    out: List[Path] = []
    seen: Set[Path] = set()
    for p in paths:
        rp = p.resolve()
        if rp not in seen:
            seen.add(rp)
            out.append(p)
    return out


def _load_csv_many(paths: List[Path], cols: List[str]) -> pd.DataFrame:
    if not paths:
        return pd.DataFrame(columns=cols)
    dfs: List[pd.DataFrame] = []
    for p in paths:
        try:
            dfs.append(_load_csv(p, cols))
        except Exception as e:  # noqa: BLE001
            logger.warning("load tracking csv failed: %s: %s", p, e)
    if not dfs:
        return pd.DataFrame(columns=cols)
    return pd.concat(dfs, ignore_index=True)[cols]


def load_finished() -> pd.DataFrame:
    return _load_csv_many(_tracking_files("finished", LEGACY_FINISHED_CSVS), FINISHED_COLS)


def load_empty() -> pd.DataFrame:
    return _load_csv_many(_tracking_files("empty", LEGACY_EMPTY_CSVS), EMPTY_COLS)


def append_finished(result: DownloadResult) -> None:
    row = {
        "symbol": result.symbol,
        "exchange": result.exchange,
        "interval": result.interval,
        "status": result.status,
        "rows": result.rows,
        "date_start": result.date_start,
        "date_end": result.date_end,
        "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "detail": result.detail,
    }
    with _finished_lock:
        header = not FINISHED_CSV.exists()
        FINISHED_CSV.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([row], columns=FINISHED_COLS).to_csv(
            FINISHED_CSV,
            mode="a",
            header=header,
            index=False,
            encoding="utf-8-sig",
        )


def _aggregate_empty_rows(
    empties: List[Tuple[str, str, str, str, str]],
    max_date: str = MAX_EMPTY_DATE,
    gap_days: int = EMPTY_GAP_DAYS,
) -> List[Dict[str, object]]:
    if not empties:
        return []
    max_ts = pd.Timestamp(max_date)
    ts_now = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    groups: Dict[Tuple[str, str, str, str], List[pd.Timestamp]] = {}
    for sym, exch, itv, d, reason in empties:
        try:
            ts = pd.Timestamp(d)
        except Exception:
            continue
        if ts > max_ts:
            continue
        key = (sym, exch, itv, str(reason) if reason else "")
        groups.setdefault(key, []).append(ts)
    out_rows: List[Dict[str, object]] = []
    for (sym, exch, itv, reason), dates in groups.items():
        dates = sorted(set(dates))
        if not dates:
            continue
        run = [dates[0]]
        for d in dates[1:]:
            if (d - run[-1]).days > gap_days:
                out_rows.append(
                    {
                        "symbol": sym,
                        "exchange": exch,
                        "interval": itv,
                        "date_start": run[0].strftime("%Y-%m-%d"),
                        "date_end": run[-1].strftime("%Y-%m-%d"),
                        "count": len(run),
                        "reason": reason,
                        "recorded_at": ts_now,
                    }
                )
                run = [d]
            else:
                run.append(d)
        out_rows.append(
            {
                "symbol": sym,
                "exchange": exch,
                "interval": itv,
                "date_start": run[0].strftime("%Y-%m-%d"),
                "date_end": run[-1].strftime("%Y-%m-%d"),
                "count": len(run),
                "reason": reason,
                "recorded_at": ts_now,
            }
        )
    return out_rows


def flush_empty_aggregated(empties: List[Tuple[str, str, str, str, str]]) -> int:
    rows = _aggregate_empty_rows(empties)
    if not rows:
        return 0
    with _empty_lock:
        header = not EMPTY_CSV.exists()
        EMPTY_CSV.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=EMPTY_COLS).to_csv(
            EMPTY_CSV,
            mode="a",
            header=header,
            index=False,
            encoding="utf-8-sig",
        )
    return len(rows)


def finished_pairs(df: pd.DataFrame) -> Set[Tuple[str, str]]:
    if df.empty:
        return set()
    done = df[df["status"].isin(["success", "empty"])]
    return set(zip(done["symbol"].astype(str), done["interval"].astype(str)))


__all__ = [
    "TRACKING_DATE",
    "FINISHED_CSV",
    "EMPTY_CSV",
    "MAX_EMPTY_DATE",
    "EMPTY_GAP_DAYS",
    "load_finished",
    "load_empty",
    "append_finished",
    "flush_empty_aggregated",
    "finished_pairs",
    "migrate_legacy_tracking_files",
]
