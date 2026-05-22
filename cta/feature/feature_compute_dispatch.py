"""Shared compute/IO helpers for feature batch generation."""
from __future__ import annotations

import gc
import logging
import os
import platform
import resource
import traceback
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from cta.feature.loader import load_day_data, load_intraday_data, normalize_interval

logger = logging.getLogger("feature.run_all")

CTA_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = CTA_ROOT / "data"
FEATURE_DIR = DATA_DIR / "feature"
FINISHED_CSV = FEATURE_DIR / "finished.csv"
FAIL_CSV = FEATURE_DIR / "fail.csv"
RANKING_CSV = CTA_ROOT / "feature" / "symbols_research_ranking.csv"

SIZE_THRESHOLDS = {
    "day": 1 * 1024 * 1024,
    "minute": 50 * 1024 * 1024,
    "minute5": 10 * 1024 * 1024,
    "minute15": 5 * 1024 * 1024,
    "minute30": 3 * 1024 * 1024,
    "minute60": 2 * 1024 * 1024,
}
DEFAULT_SIZE_THRESHOLD = 512 * 1024

INTERVAL_WORKER_CAP = {
    "day": 4,
    "minute": 1,
    "minute5": 4,
    "minute15": 4,
    "minute30": 4,
    "minute60": 4,
}
INTERVAL_RUN_ORDER: List[str] = ["day", "minute60", "minute30", "minute15", "minute5", "minute"]
ALL_INTERVALS: List[str] = list(INTERVAL_RUN_ORDER)

FINISHED_COLS = [
    "symbol",
    "exchange",
    "interval",
    "status",
    "days_written",
    "rows",
    "cols",
    "date_start",
    "date_end",
    "output_dir",
    "completed_at",
    "detail",
]
FAIL_COLS = ["symbol", "exchange", "interval", "error_type", "error", "failed_at"]

_write_lock = Lock()


def _rss_mb() -> float:
    try:
        rss = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    except Exception:
        return 0.0
    if platform.system() == "Darwin":
        return rss / 1024.0 / 1024.0
    return rss / 1024.0


def _load_csv(path: Path, cols: List[str]) -> pd.DataFrame:
    if not path.exists():
        return pd.DataFrame(columns=cols)
    try:
        df = pd.read_csv(path, engine="python", on_bad_lines="skip")
    except Exception:
        df = _load_mixed_schema_csv(path)
    for c in cols:
        if c not in df.columns:
            df[c] = pd.NA
    return df[cols]


def _load_mixed_schema_csv(path: Path) -> pd.DataFrame:
    with open(path, "r", encoding="utf-8-sig") as fh:
        lines = fh.readlines()
    if not lines:
        return pd.DataFrame()
    headers: List[int] = [i for i, l in enumerate(lines) if l.split(",", 1)[0].strip() == "symbol"]
    if not headers:
        return pd.DataFrame()
    headers.append(len(lines))
    import io

    frames: List[pd.DataFrame] = []
    for i in range(len(headers) - 1):
        block = "".join(lines[headers[i] : headers[i + 1]])
        try:
            frames.append(pd.read_csv(io.StringIO(block)))
        except Exception:
            continue
    if not frames:
        return pd.DataFrame()
    return pd.concat(frames, ignore_index=True)


def load_finished() -> pd.DataFrame:
    return _load_csv(FINISHED_CSV, FINISHED_COLS)


def load_fail() -> pd.DataFrame:
    return _load_csv(FAIL_CSV, FAIL_COLS)


def finished_success_pairs(df: pd.DataFrame) -> Set[Tuple[str, str]]:
    if df.empty:
        return set()
    done = df[df["status"] == "success"]
    return {(str(s), normalize_interval(str(i))) for s, i in zip(done["symbol"], done["interval"])}


def _ensure_schema(path: Path, cols: List[str]) -> None:
    if not path.exists():
        return
    try:
        with open(path, "r", encoding="utf-8-sig") as fh:
            first = fh.readline().strip()
    except OSError:
        return
    expected = ",".join(cols)
    if first == expected:
        return
    archive = path.with_suffix(path.suffix + ".legacy")
    try:
        path.rename(archive)
        logger.info("schema 变更，已归档旧跟踪文件 -> %s", archive)
    except OSError:
        pass


def append_finished(row: Dict[str, object]) -> None:
    with _write_lock:
        FINISHED_CSV.parent.mkdir(parents=True, exist_ok=True)
        _ensure_schema(FINISHED_CSV, FINISHED_COLS)
        header = not FINISHED_CSV.exists()
        pd.DataFrame([row], columns=FINISHED_COLS).to_csv(
            FINISHED_CSV, mode="a", header=header, index=False, encoding="utf-8-sig"
        )


def append_fail(row: Dict[str, object]) -> None:
    with _write_lock:
        FAIL_CSV.parent.mkdir(parents=True, exist_ok=True)
        _ensure_schema(FAIL_CSV, FAIL_COLS)
        header = not FAIL_CSV.exists()
        pd.DataFrame([row], columns=FAIL_COLS).to_csv(
            FAIL_CSV, mode="a", header=header, index=False, encoding="utf-8-sig"
        )


def _worker_compute(
    symbol: str,
    exchange: str,
    interval: str,
    overwrite: bool,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    voi_enabled_cells: tuple[str, ...] = (),
) -> Dict[str, object]:
    from cta.config.voi_momentum_config import VoiMomentumConfig
    from cta.feature.compute import compute_single_symbol_features

    canon = normalize_interval(interval)
    out_dir = FEATURE_DIR / canon / symbol
    out_dir.mkdir(parents=True, exist_ok=True)

    pid = os.getpid()
    w_logger = logging.getLogger(f"worker.{canon}")
    t_begin = datetime.now().timestamp()
    rss_start = _rss_mb()
    w_logger.info("[pid=%s][%s][%s] begin rss=%.0fMB", pid, canon, symbol, rss_start)

    t0 = datetime.now().timestamp()
    try:
        if canon == "day":
            df = load_day_data(symbol)
        else:
            df = load_intraday_data(symbol, exchange, interval=canon)
    except FileNotFoundError as e:
        return {"kind": "fail", "symbol": symbol, "exchange": exchange, "interval": canon, "error_type": "LoadError:FileNotFound", "error": str(e)}
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc(limit=5)
        return {
            "kind": "fail",
            "symbol": symbol,
            "exchange": exchange,
            "interval": canon,
            "error_type": f"LoadError:{type(e).__name__}",
            "error": f"{e} || {tb.splitlines()[-1] if tb else ''}",
        }

    if df is None or df.empty:
        return {"kind": "fail", "symbol": symbol, "exchange": exchange, "interval": canon, "error_type": "LoadError:Empty", "error": "loaded empty df"}

    load_rows = len(df)
    load_elapsed = datetime.now().timestamp() - t0
    w_logger.info("[pid=%s][%s][%s] loaded rows=%s cols=%s load_t=%.1fs rss=%.0fMB", pid, canon, symbol, load_rows, len(df.columns), load_elapsed, _rss_mb())

    t0 = datetime.now().timestamp()
    try:
        voi_cfg = VoiMomentumConfig(
            use_voi_regime_adaptive_momentum=bool(voi_enabled_cells),
            enabled_by_cluster_interval={str(cell): True for cell in voi_enabled_cells},
        )
        from cta.config.symbol_cluster_config import infer_symbol_cluster

        feat = compute_single_symbol_features(
            df,
            interval=canon,
            voi_cfg=voi_cfg,
            cluster=infer_symbol_cluster(symbol),
        )
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc(limit=8)
        del df
        gc.collect()
        return {
            "kind": "fail",
            "symbol": symbol,
            "exchange": exchange,
            "interval": canon,
            "error_type": f"ComputeError:{type(e).__name__}",
            "error": f"{e} || load_rows={load_rows} || {tb.splitlines()[-1] if tb else ''}",
        }

    if feat is None or feat.empty:
        return {"kind": "fail", "symbol": symbol, "exchange": exchange, "interval": canon, "error_type": "ComputeError:Empty", "error": "compute returned empty"}

    compute_elapsed = datetime.now().timestamp() - t0
    feat_cols = len(feat.columns)
    feat_rows = len(feat)
    w_logger.info("[pid=%s][%s][%s] features rows=%s cols=%s compute_t=%.1fs rss=%.0fMB", pid, canon, symbol, feat_rows, feat_cols, compute_elapsed, _rss_mb())

    del df
    gc.collect()

    try:
        if "datetime" not in feat.columns:
            return {"kind": "fail", "symbol": symbol, "exchange": exchange, "interval": canon, "error_type": "FilterError:MissingDatetime", "error": "feat has no 'datetime' column"}
        feat["_date"] = pd.to_datetime(feat["datetime"]).dt.strftime("%Y-%m-%d")
        if start_date:
            feat = feat[feat["_date"] >= start_date]
        if end_date:
            feat = feat[feat["_date"] <= end_date]
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc(limit=5)
        return {
            "kind": "fail",
            "symbol": symbol,
            "exchange": exchange,
            "interval": canon,
            "error_type": f"FilterError:{type(e).__name__}",
            "error": f"{e} || {tb.splitlines()[-1] if tb else ''}",
        }

    if feat.empty:
        del feat
        gc.collect()
        return {
            "kind": "finished",
            "symbol": symbol,
            "exchange": exchange,
            "interval": canon,
            "status": "success",
            "days_written": 0,
            "rows": 0,
            "cols": 0,
            "date_start": "",
            "date_end": "",
            "output_dir": str(out_dir),
            "detail": f"empty after date filter [{start_date},{end_date}]",
        }

    t0 = datetime.now().timestamp()
    days_written = 0
    days_skipped = 0
    total_rows = 0
    cols = len(feat.columns) - 1
    date_list: List[str] = []
    full_threshold = SIZE_THRESHOLDS.get(canon, DEFAULT_SIZE_THRESHOLD)
    day_threshold = 4096 if canon == "day" else max(int(full_threshold * 0.01), 4096)

    try:
        for date, group in feat.groupby("_date", sort=True):
            out_path = out_dir / f"{date}.parquet"
            if out_path.exists() and not overwrite and out_path.stat().st_size >= day_threshold:
                days_skipped += 1
                continue
            group.drop(columns=["_date"]).to_parquet(out_path, index=False)
            days_written += 1
            total_rows += len(group)
            date_list.append(date)
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc(limit=5)
        return {
            "kind": "fail",
            "symbol": symbol,
            "exchange": exchange,
            "interval": canon,
            "error_type": f"WriteError:{type(e).__name__}",
            "error": f"{e} || written_before_fail={days_written} || {tb.splitlines()[-1] if tb else ''}",
        }

    write_elapsed = datetime.now().timestamp() - t0
    elapsed = datetime.now().timestamp() - t_begin
    date_start = date_list[0] if date_list else ""
    date_end = date_list[-1] if date_list else ""

    del feat
    gc.collect()
    rss_end = _rss_mb()
    w_logger.info(
        "[pid=%s][%s][%s] done write_t=%.1fs total_t=%.1fs written=%s skipped=%s rss=%.0fMB (peak_delta=%+.0fMB)",
        pid,
        canon,
        symbol,
        write_elapsed,
        elapsed,
        days_written,
        days_skipped,
        rss_end,
        rss_end - rss_start,
    )

    return {
        "kind": "finished",
        "symbol": symbol,
        "exchange": exchange,
        "interval": canon,
        "status": "success",
        "days_written": int(days_written),
        "rows": int(total_rows),
        "cols": int(cols),
        "date_start": date_start,
        "date_end": date_end,
        "output_dir": str(out_dir),
        "detail": (
            f"elapsed={elapsed:.1f}s (load={load_elapsed:.1f}s compute={compute_elapsed:.1f}s "
            f"write={write_elapsed:.1f}s), written={days_written}, skipped={days_skipped}, rss_end={rss_end:.0f}MB"
        ),
    }


__all__ = [
    "ALL_INTERVALS",
    "INTERVAL_RUN_ORDER",
    "INTERVAL_WORKER_CAP",
    "FEATURE_DIR",
    "FINISHED_CSV",
    "FAIL_CSV",
    "RANKING_CSV",
    "append_finished",
    "append_fail",
    "load_finished",
    "load_fail",
    "finished_success_pairs",
    "_worker_compute",
    "_rss_mb",
]
