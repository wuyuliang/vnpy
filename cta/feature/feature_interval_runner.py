"""Interval orchestration helpers for feature batch generation."""
from __future__ import annotations

import gc
import logging
import sys
import time
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from typing import Dict, List, Optional, Tuple

import pandas as pd

from cta.feature.feature_compute_dispatch import (
    ALL_INTERVALS,
    INTERVAL_RUN_ORDER,
    INTERVAL_WORKER_CAP,
    FEATURE_DIR,
    RANKING_CSV,
    _rss_mb,
    _worker_compute,
    append_fail,
    append_finished,
)
from cta.feature.loader import list_available_intervals, normalize_interval

logger = logging.getLogger("feature.run_all")


def run_interval(
    interval: str,
    ranking: pd.DataFrame,
    finished_pairs: set[tuple[str, str]],
    max_workers: int,
    overwrite: bool,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Tuple[int, int, int]:
    canon = normalize_interval(interval)
    ts_now = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cap = INTERVAL_WORKER_CAP.get(canon, 4)
    workers = max(1, min(max_workers, cap))
    use_global_skip = start_date is None and end_date is None and not overwrite

    todo = []
    for _, r in ranking.iterrows():
        sym, exch = str(r["symbol"]), str(r["exchange"])
        if use_global_skip and (sym, canon) in finished_pairs:
            continue
        todo.append((sym, exch))
    logger.info(
        "[%s] 总 %s 品种, 已完成 %s, 待处理 %s, workers=%s, date_range=[%s, %s]",
        canon,
        len(ranking),
        len(ranking) - len(todo),
        len(todo),
        workers,
        start_date or "-",
        end_date or "-",
    )
    if not todo:
        return 0, 0, len(ranking)

    n_ok = n_fail = 0
    t0 = time.time()
    executor_kwargs: Dict[str, object] = {"max_workers": workers}
    if canon == "minute" and sys.version_info >= (3, 11):
        executor_kwargs["max_tasks_per_child"] = 1
        logger.info("[%s] 启用 max_tasks_per_child=1（每品种后回收 worker）", canon)

    gc_every = 5
    with ProcessPoolExecutor(**executor_kwargs) as ex:
        fut_map = {
            ex.submit(_worker_compute, sym, exch, canon, overwrite, start_date, end_date): (sym, exch)
            for sym, exch in todo
        }
        for i, fut in enumerate(as_completed(fut_map), 1):
            sym, exch = fut_map[fut]
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001
                append_fail(
                    {
                        "symbol": sym,
                        "exchange": exch,
                        "interval": canon,
                        "error_type": "WorkerCrash",
                        "error": repr(e),
                        "failed_at": ts_now(),
                    }
                )
                n_fail += 1
                logger.error("  [%s/%s][%s] %s 进程崩溃: %s", i, len(todo), canon, sym, e)
                continue

            if res.get("kind") == "finished":
                append_finished(
                    {
                        "symbol": res["symbol"],
                        "exchange": res["exchange"],
                        "interval": res["interval"],
                        "status": res["status"],
                        "days_written": res.get("days_written", 0),
                        "rows": res.get("rows", 0),
                        "cols": res.get("cols", 0),
                        "date_start": res.get("date_start", ""),
                        "date_end": res.get("date_end", ""),
                        "output_dir": res.get("output_dir", ""),
                        "completed_at": ts_now(),
                        "detail": res.get("detail", ""),
                    }
                )
                n_ok += 1
                logger.info(
                    "  [%s/%s][%s] OK %s days=%s rows=%s cols=%s [%s~%s] %s",
                    i,
                    len(todo),
                    canon,
                    sym,
                    res.get("days_written", 0),
                    res.get("rows", 0),
                    res.get("cols", 0),
                    res.get("date_start", ""),
                    res.get("date_end", ""),
                    res.get("detail", ""),
                )
            else:
                append_fail(
                    {
                        "symbol": res["symbol"],
                        "exchange": res["exchange"],
                        "interval": res["interval"],
                        "error_type": res.get("error_type", "UnknownError"),
                        "error": res.get("error", ""),
                        "failed_at": ts_now(),
                    }
                )
                n_fail += 1
                logger.warning(
                    "  [%s/%s][%s] FAIL %s: %s: %s",
                    i,
                    len(todo),
                    canon,
                    sym,
                    res.get("error_type"),
                    str(res.get("error", ""))[:200],
                )

            if i % gc_every == 0 or i == len(todo):
                gc.collect()
                logger.info(
                    "  [%s] progress %s/%s ok=%s fail=%s main_rss=%.0fMB",
                    canon,
                    i,
                    len(todo),
                    n_ok,
                    n_fail,
                    _rss_mb(),
                )

    elapsed = time.time() - t0
    logger.info("[%s] 完成: success=%s, fail=%s, 耗时 %.1fs", canon, n_ok, n_fail, elapsed)
    return n_ok, n_fail, len(ranking) - len(todo)


def run_cross_section(
    intervals: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> None:
    from cta.feature.cross_section import compute_cross_section_features

    for interval in intervals:
        canon = normalize_interval(interval)
        sub_dir = FEATURE_DIR / canon
        if not sub_dir.exists():
            logger.warning("[%s] 特征目录不存在，跳过截面", canon)
            continue

        symbol_dirs = sorted(d for d in sub_dir.iterdir() if d.is_dir() and not d.name.startswith("_"))
        if not symbol_dirs:
            logger.warning("[%s] 没有品种子目录, 跳过截面", canon)
            continue

        files = []
        for sd in symbol_dirs:
            for f in sorted(sd.glob("*.parquet")):
                date = f.stem
                if start_date and date < start_date:
                    continue
                if end_date and date > end_date:
                    continue
                files.append(f)
        if not files:
            logger.warning("[%s] 无匹配按日 parquet (range=[%s,%s]), 跳过", canon, start_date, end_date)
            continue

        logger.info("[%s] 截面加载 %s 个按日 parquet (共 %s 品种) ...", canon, len(files), len(symbol_dirs))
        t0 = time.time()
        dfs = [pd.read_parquet(f) for f in files]
        all_data = pd.concat(dfs, ignore_index=True)
        del dfs
        gc.collect()

        logger.info("[%s] 计算截面特征 (%s 行) ...", canon, len(all_data))
        all_data = compute_cross_section_features(all_data)
        merged_path = sub_dir / "_all_symbols.parquet"
        all_data.to_parquet(merged_path, index=False)
        logger.info("[%s] 截面完成: %s 行, %s 列, 耗时 %.1fs -> %s", canon, len(all_data), len(all_data.columns), time.time() - t0, merged_path)
        del all_data
        gc.collect()


def _order_by_run_priority(intervals: List[str]) -> List[str]:
    rank = {k: i for i, k in enumerate(INTERVAL_RUN_ORDER)}
    unknown = [i for i in intervals if i not in rank]
    known = [i for i in intervals if i in rank]
    known.sort(key=lambda x: rank[x])
    return known + unknown


def resolve_intervals(arg_intervals: List[str]) -> List[str]:
    if not arg_intervals or "all" in arg_intervals:
        return _order_by_run_priority(["day"] + list_available_intervals())
    if "intraday" in arg_intervals:
        return _order_by_run_priority(list_available_intervals())
    canon = [normalize_interval(i) for i in arg_intervals]
    seen: set[str] = set()
    out: List[str] = []
    for i in canon:
        if i in ALL_INTERVALS and i not in seen:
            out.append(i)
            seen.add(i)
    return _order_by_run_priority(out)


def load_ranking(symbols_filter: Optional[List[str]] = None, max_rank: Optional[int] = None) -> pd.DataFrame:
    if not RANKING_CSV.exists():
        raise FileNotFoundError(f"排名文件不存在: {RANKING_CSV}")
    df = pd.read_csv(RANKING_CSV, encoding="utf-8-sig")
    df = df[["symbol", "exchange", "research_rank"]].copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["exchange"] = df["exchange"].astype(str).str.strip().str.upper()
    df = df.dropna(subset=["symbol", "exchange", "research_rank"])
    df = df.sort_values("research_rank").reset_index(drop=True)
    if max_rank is not None:
        df = df[df["research_rank"] <= max_rank].reset_index(drop=True)
    if symbols_filter:
        want = {s.upper() for s in symbols_filter}
        df = df[df["symbol"].isin(want)].reset_index(drop=True)
    return df


__all__ = [
    "run_interval",
    "run_cross_section",
    "resolve_intervals",
    "load_ranking",
]
