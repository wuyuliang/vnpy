"""
中国商品期货统一批量下载（代码目录: cta/data_code/）

从 cta/feature/symbols_research_ranking0.csv 按 research_rank 顺序逐品种下载，
支持日线 + 多档分钟级（1/5/15/30/60min），断点续跑、交易所兜底、并发可配。

目录产物（原始行情写入 cta/data/origin/）
--------------------------------
cta/data/origin/day/{SYMBOL}.csv
cta/data/origin/minute/{PREFIX}/{YYYY-MM-DD}.parquet     # 1min
cta/data/origin/minute5/{PREFIX}/{YYYY-MM-DD}.parquet    # 由 1min 本地重采样
cta/data/origin/minute15/{PREFIX}/{YYYY-MM-DD}.parquet
cta/data/origin/minute30/{PREFIX}/{YYYY-MM-DD}.parquet
cta/data/origin/minute60/{PREFIX}/{YYYY-MM-DD}.parquet

跟踪文件（统一放 cta/data/）
---------------------------
cta/data/finished_YYYYMMDD.csv
    列: symbol, exchange, interval, status, rows, date_start, date_end, completed_at, detail

cta/data/empty_YYYYMMDD.csv
    按时间区间汇总（非逐日）
    列: symbol, exchange, interval, date_start, date_end, count, reason, recorded_at
    举例: (ZS0, DCE, minute, 2009-03-30, 2009-12-31, 185, tushare_empty, ...)

    聚合规则:
      1) 按 (symbol, exchange, interval, reason) 分组
      2) 同组内日期排序，若相邻两个空日相差 > EMPTY_GAP_DAYS 则拆成新区间
      3) 晚于 MAX_EMPTY_DATE 的日期忽略（视为未来/未落地，不算空）

断点续跑
--------
- day: 若 {SYMBOL}.csv 存在即跳过
- minute*: 历史所有 finished_*.csv（含兼容旧文件）里 status ∈ {success, empty}
           即跳过该 (symbol, interval)
- 单日文件：per-date parquet 存在即跳过

在线复用
--------
在线单日下载直接使用:
    cta.data_code.futures_downloader.FuturesDownloader
"""
from __future__ import annotations

import argparse
import logging
import shutil
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from cta.data_code.futures_downloader import (
    ALL_INTERVALS,
    DATA_DIR,
    DAY_DIR,
    DownloadResult,
    FuturesDownloader,
    MINUTE_INTERVALS,
    alpha_prefix,
)

# =============================================================================
# 日志
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("download_all")

# =============================================================================
# 路径常量
# =============================================================================
CTA_ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = Path(__file__).resolve().parent               # cta/data_code/
RANKING_CSV = CTA_ROOT / "feature" / "symbols_research_ranking.csv"
TRACKING_DATE = datetime.now().strftime("%Y%m%d")
FINISHED_CSV = DATA_DIR / f"finished_{TRACKING_DATE}.csv"
EMPTY_CSV = DATA_DIR / f"empty_{TRACKING_DATE}.csv"

# 兼容历史路径（仅迁移，不再写入）
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
    "symbol", "exchange", "interval",
    "status", "rows", "date_start", "date_end",
    "completed_at", "detail",
]
EMPTY_COLS = [
    "symbol", "exchange", "interval",
    "date_start", "date_end", "count",
    "reason", "recorded_at",
]

# empty.csv 聚合参数
MAX_EMPTY_DATE = "2026-04-17"     # 晚于此日期的空日不记录（视为数据尚未落地）
EMPTY_GAP_DAYS = 60               # 连续空日区间的最大间隙


# =============================================================================
# finished.csv / empty.csv 读写（线程安全）
# =============================================================================
_finished_lock = Lock()
_empty_lock = Lock()


def _migrate_tracking_csv(target: Path, legacy_paths: Tuple[Path, ...]) -> None:
    """
    兼容历史路径：若 target 不存在，从第一份存在的 legacy 文件复制到 target。
    仅复制，不删除旧文件，避免误删用户历史。
    """
    if target.exists():
        return
    # 若已有日期化文件，说明迁移已完成，不再每日重复复制
    pattern = f"{target.stem.split('_')[0]}_*.csv"
    if any(DATA_DIR.glob(pattern)):
        return
    for legacy in legacy_paths:
        if legacy.exists():
            try:
                target.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(legacy, target)
                logger.info(f"migrate tracking csv: {legacy} -> {target}")
            except Exception as e:  # noqa: BLE001
                logger.warning(f"migrate tracking csv failed: {legacy} -> {target}: {e}")
            return


def migrate_legacy_tracking_files() -> None:
    """在启动阶段迁移历史 tracking csv 到 cta/data。"""
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
    """收集可读的状态文件：历史日期化文件 + 旧固定文件（若存在）。"""
    paths: List[Path] = []
    paths.extend(sorted(p for p in DATA_DIR.glob(f"{prefix}_*.csv") if p.is_file()))
    for p in legacy_paths:
        if p.exists():
            paths.append(p)
    # 去重并保持顺序
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
            logger.warning(f"load tracking csv failed: {p}: {e}")
    if not dfs:
        return pd.DataFrame(columns=cols)
    return pd.concat(dfs, ignore_index=True)[cols]


def load_finished() -> pd.DataFrame:
    return _load_csv_many(
        _tracking_files("finished", LEGACY_FINISHED_CSVS),
        FINISHED_COLS,
    )


def load_empty() -> pd.DataFrame:
    return _load_csv_many(
        _tracking_files("empty", LEGACY_EMPTY_CSVS),
        EMPTY_COLS,
    )


def append_finished(result: DownloadResult) -> None:
    """线程安全地把一条记录 append 到当日 finished_YYYYMMDD.csv"""
    row = {
        "symbol":       result.symbol,
        "exchange":     result.exchange,
        "interval":     result.interval,
        "status":       result.status,
        "rows":         result.rows,
        "date_start":   result.date_start,
        "date_end":     result.date_end,
        "completed_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
        "detail":       result.detail,
    }
    with _finished_lock:
        header = not FINISHED_CSV.exists()
        FINISHED_CSV.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame([row], columns=FINISHED_COLS).to_csv(
            FINISHED_CSV, mode="a", header=header, index=False, encoding="utf-8-sig"
        )


def _aggregate_empty_rows(
    empties: List[Tuple[str, str, str, str, str]],
    max_date: str = MAX_EMPTY_DATE,
    gap_days: int = EMPTY_GAP_DAYS,
) -> List[Dict[str, object]]:
    """
    把逐日 empty 记录聚合为时间区间。

    输入:
        empties = [(symbol, exchange, interval, trade_date, reason), ...]
    聚合:
        1. 按 (symbol, exchange, interval, reason) 分组
        2. 同组日期排序；相邻日期相差 > gap_days 视为新区间
        3. 日期 > max_date 丢弃
    返回:
        [{symbol, exchange, interval, date_start, date_end, count, reason, recorded_at}, ...]
    """
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
                out_rows.append({
                    "symbol":      sym,
                    "exchange":    exch,
                    "interval":    itv,
                    "date_start":  run[0].strftime("%Y-%m-%d"),
                    "date_end":    run[-1].strftime("%Y-%m-%d"),
                    "count":       len(run),
                    "reason":      reason,
                    "recorded_at": ts_now,
                })
                run = [d]
            else:
                run.append(d)
        out_rows.append({
            "symbol":      sym,
            "exchange":    exch,
            "interval":    itv,
            "date_start":  run[0].strftime("%Y-%m-%d"),
            "date_end":    run[-1].strftime("%Y-%m-%d"),
            "count":       len(run),
            "reason":      reason,
            "recorded_at": ts_now,
        })
    return out_rows


def flush_empty_aggregated(
    empties: List[Tuple[str, str, str, str, str]],
) -> int:
    """聚合后 append 到当日 empty_YYYYMMDD.csv，返回写入的区间行数"""
    rows = _aggregate_empty_rows(empties)
    if not rows:
        return 0
    with _empty_lock:
        header = not EMPTY_CSV.exists()
        EMPTY_CSV.parent.mkdir(parents=True, exist_ok=True)
        pd.DataFrame(rows, columns=EMPTY_COLS).to_csv(
            EMPTY_CSV, mode="a", header=header, index=False, encoding="utf-8-sig"
        )
    return len(rows)


def finished_pairs(df: pd.DataFrame) -> Set[Tuple[str, str]]:
    """已完成的 (symbol, interval) 集合（status 为 success/empty 都视为已处理）"""
    if df.empty:
        return set()
    done = df[df["status"].isin(["success", "empty"])]
    return set(zip(done["symbol"].astype(str), done["interval"].astype(str)))


# =============================================================================
# 单品种处理
# =============================================================================
def _existing_dates(symbol: str, interval: str) -> Set[str]:
    """逐日 parquet 已存在的日期集合"""
    d = DATA_DIR / interval / alpha_prefix(symbol)
    if not d.exists():
        return set()
    return {p.stem for p in d.glob("*.parquet")}


def process_day(dl: FuturesDownloader, symbol: str, exchange: str) -> DownloadResult:
    """下载日线"""
    return dl.download_day(symbol, exchange, out_dir=DAY_DIR)


def process_minute_symbol(
    dl: FuturesDownloader,
    symbol: str,
    exchange: str,
    intervals: List[str],
    workers: int,
) -> Tuple[Dict[str, DownloadResult], List[Tuple[str, str, str, str, str]]]:
    """
    下载品种的全部分钟级数据。

    返回:
        ({interval: 汇总 DownloadResult}, empties)
        empties = [(symbol, exchange, interval, trade_date, reason), ...]
    """
    # 本品种的 empty 收集（线程安全：用锁保护 list.append）
    empties: List[Tuple[str, str, str, str, str]] = []
    emp_lock = Lock()

    def _record_empty(itv: str, d: str, reason: str) -> None:
        with emp_lock:
            empties.append((symbol, exchange, itv, d, reason or "empty"))

    # 1. mapping
    mapping_df, suf = dl.fetch_fut_mapping(symbol, exchange)
    if mapping_df.empty:
        return (
            {
                itv: DownloadResult(
                    symbol=symbol, exchange=exchange, interval=itv,
                    status="empty", detail="fut_mapping 无数据（所有 exchange 变体失败）",
                )
                for itv in intervals
            },
            empties,
        )

    logger.info(f"  mapping -> ts_suffix={suf}, trade_days={len(mapping_df)}")

    # 2. 准备待下载的 (trade_date, contract_code)
    date_contract = list(zip(mapping_df["trade_date"], mapping_df["mapping_ts_code"]))
    existing_by_itv: Dict[str, Set[str]] = {
        itv: _existing_dates(symbol, itv) for itv in intervals
    }

    todo: List[Tuple[str, str]] = []
    for d, c in date_contract:
        if any(d not in existing_by_itv[itv] for itv in intervals):
            todo.append((d, c))

    skip_cnt = len(date_contract) - len(todo)
    logger.info(
        f"  dates total={len(date_contract)}, skip(exists)={skip_cnt}, to_fetch={len(todo)}"
    )

    # 3. 并发拉取
    stats: Dict[str, Dict[str, int]] = {
        itv: {"success": 0, "empty": 0, "skip": 0, "error": 0, "rows": 0}
        for itv in intervals
    }
    date_range: Dict[str, List[str]] = {itv: [] for itv in intervals}

    def _one(date_contract_pair: Tuple[str, str]) -> Dict[str, DownloadResult]:
        d, c = date_contract_pair
        return dl.download_day_minute_all(
            symbol=symbol, exchange=exchange,
            contract_code=c, trade_date=d,
            intervals=intervals,
        )

    if workers > 1 and todo:
        with ThreadPoolExecutor(max_workers=workers) as ex:
            futures = {ex.submit(_one, pair): pair for pair in todo}
            for i, fut in enumerate(as_completed(futures), 1):
                d, c = futures[fut]
                try:
                    res = fut.result()
                except Exception as e:  # noqa: BLE001
                    logger.error(f"  [{d} {c}] 未捕获异常: {e}")
                    for itv in intervals:
                        stats[itv]["error"] += 1
                    continue
                for itv, r in res.items():
                    stats[itv][r.status] = stats[itv].get(r.status, 0) + 1
                    stats[itv]["rows"] += r.rows
                    if r.status == "success":
                        date_range[itv].append(d)
                    if r.status == "empty":
                        _record_empty(itv, d, r.detail or "empty")
                if i % 200 == 0 or i == len(todo):
                    logger.info(f"  progress {i}/{len(todo)}")
    else:
        for i, pair in enumerate(todo, 1):
            d, _ = pair
            res = _one(pair)
            for itv, r in res.items():
                stats[itv][r.status] = stats[itv].get(r.status, 0) + 1
                stats[itv]["rows"] += r.rows
                if r.status == "success":
                    date_range[itv].append(d)
                if r.status == "empty":
                    _record_empty(itv, d, r.detail or "empty")
            if i % 200 == 0 or i == len(todo):
                logger.info(f"  progress {i}/{len(todo)}")

    # 4. 汇总
    results: Dict[str, DownloadResult] = {}
    for itv in intervals:
        s = stats[itv]
        all_success_dates = sorted(set(date_range[itv]) | existing_by_itv[itv])
        if all_success_dates:
            status = "success"
            detail = (
                f"success_day={s['success']}, empty_day={s['empty']}, "
                f"skip_day={s['skip']}, error_day={s['error']}"
            )
            results[itv] = DownloadResult(
                symbol=symbol, exchange=exchange, interval=itv,
                status=status, rows=s["rows"],
                date_start=all_success_dates[0],
                date_end=all_success_dates[-1],
                detail=detail,
            )
        else:
            results[itv] = DownloadResult(
                symbol=symbol, exchange=exchange, interval=itv,
                status="empty",
                detail=f"all_days_empty/error: empty={s['empty']}, error={s['error']}",
            )
    return results, empties


# =============================================================================
# 排名表加载
# =============================================================================
def load_ranking() -> pd.DataFrame:
    if not RANKING_CSV.exists():
        raise FileNotFoundError(f"排名文件不存在: {RANKING_CSV}")
    df = pd.read_csv(RANKING_CSV, encoding="utf-8-sig")
    df = df[["symbol", "exchange", "research_rank"]].copy()
    df["symbol"] = df["symbol"].astype(str).str.strip().str.upper()
    df["exchange"] = df["exchange"].astype(str).str.strip().str.upper()
    df = df.dropna(subset=["symbol", "exchange", "research_rank"])
    df = df.sort_values("research_rank").reset_index(drop=True)
    return df


# =============================================================================
# 主流程
# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="按 research_rank 批量下载中国商品期货全频率数据"
    )
    parser.add_argument(
        "--intervals", nargs="+",
        default=list(ALL_INTERVALS),
        choices=list(ALL_INTERVALS),
        help=f"下载频率列表 (默认全部: {list(ALL_INTERVALS)})",
    )
    parser.add_argument(
        "--workers", type=int, default=4,
        help="按日并发下载 worker 数，默认 4",
    )
    parser.add_argument(
        "--rate-limit", type=int, default=450,
        help="tushare 每分钟最大请求数（付费 ~500，默认 450 留余量）",
    )
    parser.add_argument(
        "--max-rank", type=int, default=None,
        help="只处理 research_rank <= max-rank 的品种",
    )
    parser.add_argument(
        "--only-symbols", nargs="*", default=None,
        help="只处理给定品种，例如 --only-symbols CU0 RB0",
    )
    parser.add_argument(
        "--token", type=str, default=None,
        help="tushare token（默认从 TUSHARE_TOKEN 环境变量读取）",
    )
    args = parser.parse_args()

    intervals: List[str] = list(dict.fromkeys(args.intervals))
    minute_intervals = [i for i in intervals if i in MINUTE_INTERVALS]
    want_day = "day" in intervals

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CODE_DIR.mkdir(parents=True, exist_ok=True)
    migrate_legacy_tracking_files()

    logger.info("=" * 70)
    logger.info("CTA 商品期货批量下载")
    logger.info(f"  code dir:      {CODE_DIR}")
    logger.info(f"  排名文件:      {RANKING_CSV}")
    logger.info(f"  数据根目录:    {DATA_DIR}")
    logger.info(f"  tracking date: {TRACKING_DATE}")
    logger.info(f"  finished csv:  {FINISHED_CSV}")
    logger.info(f"  empty csv:     {EMPTY_CSV}")
    logger.info(f"  下载频率:      {intervals}")
    logger.info(f"  worker 数:     {args.workers}")
    logger.info(f"  rate limit:    {args.rate_limit}/min")
    logger.info(f"  max_empty:     {MAX_EMPTY_DATE}")
    if args.max_rank:
        logger.info(f"  max rank:      {args.max_rank}")
    if args.only_symbols:
        logger.info(f"  only symbols:  {args.only_symbols}")
    logger.info("=" * 70)

    # 排名
    ranking = load_ranking()
    if args.max_rank:
        ranking = ranking[ranking["research_rank"] <= args.max_rank].reset_index(drop=True)
    if args.only_symbols:
        want = {s.upper() for s in args.only_symbols}
        ranking = ranking[ranking["symbol"].isin(want)].reset_index(drop=True)

    if ranking.empty:
        logger.warning("没有品种需要处理")
        return

    # 已完成
    finished_df = load_finished()
    done_pairs = finished_pairs(finished_df)
    logger.info(f"已完成记录: {len(done_pairs)} 条 (按 symbol+interval)")

    dl = FuturesDownloader(
        token=args.token,
        rate_limit=args.rate_limit,
        workers=args.workers,
    )

    t_total = time.time()
    total_sym = len(ranking)
    for idx, row in ranking.iterrows():
        symbol = str(row["symbol"])
        exchange = str(row["exchange"])
        rank = int(row["research_rank"])
        logger.info("-" * 70)
        logger.info(f"[{idx+1}/{total_sym}] rank={rank} {symbol} ({exchange})")

        # -------- 日线 --------
        if want_day and (symbol, "day") not in done_pairs:
            t0 = time.time()
            try:
                r = process_day(dl, symbol, exchange)
            except Exception as e:  # noqa: BLE001
                r = DownloadResult(
                    symbol=symbol, exchange=exchange, interval="day",
                    status="error", detail=f"unexpected: {e}",
                )
            append_finished(r)
            done_pairs.add((symbol, "day"))
            logger.info(
                f"  [day] {r.status} rows={r.rows} "
                f"range=[{r.date_start}~{r.date_end}] {time.time()-t0:.1f}s"
            )
        elif want_day:
            logger.info(f"  [day] 已完成，跳过")

        # -------- 分钟级 --------
        need_minute = [i for i in minute_intervals if (symbol, i) not in done_pairs]
        if not need_minute:
            if minute_intervals:
                logger.info(f"  [minute*] 全部已完成，跳过")
            continue

        if not dl.token:
            logger.error("  未设置 TUSHARE_TOKEN，跳过分钟级下载")
            for itv in need_minute:
                r = DownloadResult(
                    symbol=symbol, exchange=exchange, interval=itv,
                    status="error", detail="no tushare token",
                )
                append_finished(r)
                done_pairs.add((symbol, itv))
            continue

        t0 = time.time()
        try:
            results, empties = process_minute_symbol(
                dl, symbol, exchange, need_minute, workers=args.workers,
            )
        except Exception as e:  # noqa: BLE001
            logger.error(f"  process_minute_symbol 异常: {e}", exc_info=True)
            results = {
                itv: DownloadResult(
                    symbol=symbol, exchange=exchange, interval=itv,
                    status="error", detail=f"unexpected: {e}",
                )
                for itv in need_minute
            }
            empties = []

        # 本品种 empty 聚合后 append
        if empties:
            n_ranges = flush_empty_aggregated(empties)
            logger.info(f"  empty ranges written: {n_ranges} (raw empty days={len(empties)})")

        for itv in need_minute:
            r = results.get(itv) or DownloadResult(
                symbol=symbol, exchange=exchange, interval=itv,
                status="error", detail="missing result",
            )
            append_finished(r)
            done_pairs.add((symbol, itv))
            logger.info(
                f"  [{itv}] {r.status} rows={r.rows} "
                f"range=[{r.date_start}~{r.date_end}] detail={r.detail}"
            )
        logger.info(f"  symbol minutes total: {time.time()-t0:.1f}s")

    elapsed = time.time() - t_total
    logger.info("=" * 70)
    logger.info(f"全部完成！总耗时 {elapsed:.1f}s")
    logger.info(f"finished csv: {FINISHED_CSV}")
    logger.info(f"empty csv:    {EMPTY_CSV}")


if __name__ == "__main__":
    main()
