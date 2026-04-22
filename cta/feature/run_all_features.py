"""
批量生成 CTA 特征（day + minute + minute5/15/30/60），支持多进程并发、断点续跑

输出布局（按天分片 parquet）
-----------------------------
cta/data/feature/{interval}/{SYMBOL}/{YYYY-MM-DD}.parquet    # 每天一个文件
cta/data/feature/{interval}/_all_symbols.parquet             # 截面合并 (--cross-section)
cta/data/feature/finished.csv
cta/data/feature/fail.csv

说明:
- 拆分粒度 = 计算时 datetime 的自然日（day 级每文件 1 行；minute 级每文件一天内全部 bar）
- 计算时仍然用"全历史"喂入滚动特征；date range 只影响落盘范围
- 随时间前进，可以 `--start-date 2026-04-01` 只补最近这段

特性
----
- 频率全覆盖：day / minute / minute5 / minute15 / minute30 / minute60
  兼容旧名 5min / 15min / 30min / 60min（自动归一化）
- 执行顺序：长周期 → 短周期 (day → minute60 → minute30 → minute15 → minute5 → minute)
  因为 minute 级耗时最长；优先完成信息密度高、耗时短的频率
- 品种顺序：按 `symbols_research_ranking.csv` 的 research_rank 升序
- 多进程并行：4 个 CPU 并行；minute 级自动降低到 2 worker 以控制内存
- 每 (symbol, interval) 独立任务；一个失败不影响其它
- 断点续跑:
    * 无 date range 且非 --overwrite 且 (symbol, interval) 在 finished.csv 中 success
      -> 整体跳过该 (symbol, interval)
    * 有 date range 或 --overwrite -> 进入子进程
    * 子进程内部按日 parquet 存在且足够大 + 非 --overwrite 跳过该日
- 跟踪文件:
    cta/data/feature/finished.csv
        列: symbol, exchange, interval, status, days_written, rows, cols,
            date_start, date_end, output_dir, completed_at, detail
    cta/data/feature/fail.csv
        列: symbol, exchange, interval, error_type, error, failed_at

用法
----
# 全部频率（自动识别 data/ 已有目录）
python3 -m cta.feature.run_all_features

# 单频率 / 多频率
python3 -m cta.feature.run_all_features --interval day
python3 -m cta.feature.run_all_features --interval minute minute5

# 指定品种
python3 -m cta.feature.run_all_features --interval day --symbols CU0 RB0

# 指定日期范围（只写盘该范围内的 parquet）
python3 -m cta.feature.run_all_features --interval day \
    --start-date 2024-01-01 --end-date 2024-12-31

# 增量补最新（仅某日后的）
python3 -m cta.feature.run_all_features --interval minute --start-date 2026-04-01

# 重算某范围
python3 -m cta.feature.run_all_features --interval day \
    --start-date 2024-01-01 --end-date 2024-03-31 --overwrite

# 跑完品种后再跑截面
python3 -m cta.feature.run_all_features --cross-section

在线复用
--------
单品种实时特征见 cta.feature.online.compute_features / FeatureGenerator
按日读取已落盘特征见 cta.feature.feature_loader.load_symbol_features
"""
from __future__ import annotations

import argparse
import gc
import logging
import time
import traceback
from concurrent.futures import ProcessPoolExecutor, as_completed
from datetime import datetime
from pathlib import Path
from threading import Lock
from typing import Dict, List, Optional, Set, Tuple

import pandas as pd

from cta.feature.loader import (
    list_available_intervals,
    load_day_data,
    load_intraday_data,
    normalize_interval,
)

# =============================================================================
# 常量 & 日志
# =============================================================================
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("feature.run_all")

CTA_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = CTA_ROOT / "data"
FEATURE_DIR = DATA_DIR / "feature"
FINISHED_CSV = FEATURE_DIR / "finished.csv"
FAIL_CSV = FEATURE_DIR / "fail.csv"

RANKING_CSV = CTA_ROOT / "feature" / "symbols_research_ranking.csv"

# 成功文件的最小尺寸阈值（用于区分实际有数据 vs 空文件）
SIZE_THRESHOLDS = {
    "day":      1 * 1024 * 1024,     # 1 MB
    "minute":   50 * 1024 * 1024,    # 50 MB
    "minute5":  10 * 1024 * 1024,    # 10 MB
    "minute15": 5 * 1024 * 1024,
    "minute30": 3 * 1024 * 1024,
    "minute60": 2 * 1024 * 1024,
}
DEFAULT_SIZE_THRESHOLD = 512 * 1024

# 每个频率默认 worker 上限（控制内存）
# 16GB RAM 下：minute 特征大约 2-3 GB/品种，保守 2 worker；其它轻量可以 4
INTERVAL_WORKER_CAP = {
    "day":      4,
    "minute":   2,
    "minute5":  4,
    "minute15": 4,
    "minute30": 4,
    "minute60": 4,
}

FINISHED_COLS = [
    "symbol", "exchange", "interval", "status",
    "days_written", "rows", "cols",
    "date_start", "date_end", "output_dir",
    "completed_at", "detail",
]
FAIL_COLS = [
    "symbol", "exchange", "interval", "error_type", "error", "failed_at",
]

# 跑批时的频率顺序：长周期 → 短周期
#   day 最先（单品种最快）、minute 最后（单品种最慢最耗内存）
# 该顺序同时决定 "--interval all" 展开顺序 与 多频率 CLI 输入的排序
INTERVAL_RUN_ORDER: List[str] = [
    "day", "minute60", "minute30", "minute15", "minute5", "minute",
]
ALL_INTERVALS: List[str] = list(INTERVAL_RUN_ORDER)


# =============================================================================
# 跟踪文件读写（主进程独占，无需多进程锁）
# =============================================================================
_write_lock = Lock()


def _load_csv(path: Path, cols: List[str]) -> pd.DataFrame:
    """
    宽松读取跟踪 CSV：该文件是 append-only，早期 run 与当前 run 的列数可能不同。
    使用 python engine + on_bad_lines='skip' 跳过不兼容的历史行。
    """
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
    """
    finished.csv 是 append 模式，每次 schema 变化会写一个新 header。
    这里逐块读取：遇到一行等同于 header 时视为新块起点。
    """
    with open(path, "r", encoding="utf-8-sig") as fh:
        lines = fh.readlines()
    if not lines:
        return pd.DataFrame()
    # 找出所有 header 行位置（第一列是 'symbol'）
    headers: List[int] = [i for i, l in enumerate(lines)
                          if l.split(",", 1)[0].strip() == "symbol"]
    if not headers:
        return pd.DataFrame()
    headers.append(len(lines))
    frames = []
    import io
    for i in range(len(headers) - 1):
        block = "".join(lines[headers[i]:headers[i + 1]])
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
    """(symbol, interval) 已 success 的集合（规范化 interval）"""
    if df.empty:
        return set()
    done = df[df["status"] == "success"]
    return {
        (str(s), normalize_interval(str(i)))
        for s, i in zip(done["symbol"], done["interval"])
    }


def _ensure_schema(path: Path, cols: List[str]) -> None:
    """若文件存在但 header 列与当前 schema 不一致，将旧文件归档并从头重建。"""
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
    # schema 漂移：归档旧文件
    archive = path.with_suffix(path.suffix + ".legacy")
    try:
        path.rename(archive)
        logger.info(f"schema 变更，已归档旧跟踪文件 -> {archive}")
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


# =============================================================================
# 子进程 worker：单 (symbol, interval) 特征生成
# =============================================================================
def _worker_compute(
    symbol: str,
    exchange: str,
    interval: str,
    overwrite: bool,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Dict[str, object]:
    """
    子进程入口：加载数据、计算特征、按自然日切片落盘。

    输出布局: FEATURE_DIR / {canon_interval} / {SYMBOL} / {YYYY-MM-DD}.parquet
    每个 parquet 包含该自然日内该品种的全部 bar（day 频率每天 1 行）。

    date range 规则:
      - 计算阶段始终喂入品种全历史（保证滚动特征准确）
      - 只有 [start_date, end_date] 交集内的自然日会被落盘
    """
    # 延迟导入（子进程首次调用时才加载，避免 fork 浪费）
    from cta.feature.compute import compute_single_symbol_features

    canon = normalize_interval(interval)
    out_dir = FEATURE_DIR / canon / symbol
    out_dir.mkdir(parents=True, exist_ok=True)

    t0 = time.time()
    try:
        # 1) 加载数据（全历史）
        if canon == "day":
            df = load_day_data(symbol)
        else:
            df = load_intraday_data(symbol, exchange, interval=canon)

        if df is None or df.empty:
            return {
                "kind": "fail",
                "symbol": symbol, "exchange": exchange, "interval": canon,
                "error_type": "EmptyData", "error": "loaded empty df",
            }

        # 2) 计算特征（传入 canonical interval，让 compute 内部决定走哪些特征）
        feat = compute_single_symbol_features(df, interval=canon)

        if feat is None or feat.empty:
            return {
                "kind": "fail",
                "symbol": symbol, "exchange": exchange, "interval": canon,
                "error_type": "EmptyFeature", "error": "compute returned empty",
            }

        # 3) 按自然日切片
        if "datetime" not in feat.columns:
            return {
                "kind": "fail",
                "symbol": symbol, "exchange": exchange, "interval": canon,
                "error_type": "MissingDatetime",
                "error": "feat has no 'datetime' column",
            }
        feat["_date"] = pd.to_datetime(feat["datetime"]).dt.strftime("%Y-%m-%d")
        if start_date:
            feat = feat[feat["_date"] >= start_date]
        if end_date:
            feat = feat[feat["_date"] <= end_date]

        if feat.empty:
            return {
                "kind": "finished",
                "symbol": symbol, "exchange": exchange, "interval": canon,
                "status": "success",
                "days_written": 0, "rows": 0, "cols": 0,
                "date_start": "", "date_end": "",
                "output_dir": str(out_dir),
                "detail": f"empty after date filter [{start_date},{end_date}]",
            }

        # 4) 按日 groupby 落盘；已存在且非 overwrite 则跳过该日
        days_written = 0
        days_skipped = 0
        total_rows = 0
        cols = len(feat.columns) - 1  # 去掉 _date 辅助列
        date_list: List[str] = []
        # 单日 parquet 最小字节数（小于此视为脏文件，会被重写）
        # 全历史阈值 / 每年约 250 交易日 ≈ 每日 0.4%，这里用 1% 做下限
        full_threshold = SIZE_THRESHOLDS.get(canon, DEFAULT_SIZE_THRESHOLD)
        if canon == "day":
            day_threshold = 4096  # 单日 parquet 大约 10-50KB，4KB 作为 sanity check
        else:
            day_threshold = max(int(full_threshold * 0.01), 4096)

        for date, group in feat.groupby("_date", sort=True):
            out_path = out_dir / f"{date}.parquet"
            if out_path.exists() and not overwrite:
                # 已有文件且足够大 -> 跳过
                if out_path.stat().st_size >= day_threshold:
                    days_skipped += 1
                    continue
            group.drop(columns=["_date"]).to_parquet(out_path, index=False)
            days_written += 1
            total_rows += len(group)
            date_list.append(date)

        elapsed = time.time() - t0
        date_start = date_list[0] if date_list else ""
        date_end = date_list[-1] if date_list else ""

        # 主动释放
        del feat, df
        gc.collect()

        return {
            "kind": "finished",
            "symbol": symbol, "exchange": exchange, "interval": canon,
            "status": "success",
            "days_written": int(days_written),
            "rows": int(total_rows),
            "cols": int(cols),
            "date_start": date_start,
            "date_end": date_end,
            "output_dir": str(out_dir),
            "detail": (
                f"elapsed={elapsed:.1f}s, written={days_written}, "
                f"skipped={days_skipped}"
            ),
        }
    except FileNotFoundError as e:
        return {
            "kind": "fail",
            "symbol": symbol, "exchange": exchange, "interval": canon,
            "error_type": "FileNotFound", "error": str(e),
        }
    except Exception as e:  # noqa: BLE001
        tb = traceback.format_exc(limit=5)
        return {
            "kind": "fail",
            "symbol": symbol, "exchange": exchange, "interval": canon,
            "error_type": type(e).__name__,
            "error": f"{e} || {tb.splitlines()[-1] if tb else ''}",
        }


# =============================================================================
# 主进程调度
# =============================================================================
def run_interval(
    interval: str,
    ranking: pd.DataFrame,
    finished_pairs: Set[Tuple[str, str]],
    max_workers: int,
    overwrite: bool,
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> Tuple[int, int, int]:
    """
    对单个 interval 做一次批量。返回 (success, fail, skip)

    当指定 start_date / end_date 或 --overwrite 时，全局的 finished_pairs
    跳过逻辑失效（始终进入子进程，由子进程按日跳过）。
    """
    canon = normalize_interval(interval)
    ts_now = lambda: datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    cap = INTERVAL_WORKER_CAP.get(canon, 4)
    workers = max(1, min(max_workers, cap))

    # 仅当无 date range 且非 overwrite 时，使用全局 finished_pairs 快速跳过
    use_global_skip = (start_date is None and end_date is None and not overwrite)

    todo = []
    for _, r in ranking.iterrows():
        sym, exch = str(r["symbol"]), str(r["exchange"])
        if use_global_skip and (sym, canon) in finished_pairs:
            continue
        todo.append((sym, exch))

    logger.info(
        f"[{canon}] 总 {len(ranking)} 品种, 已完成 {len(ranking)-len(todo)}, "
        f"待处理 {len(todo)}, workers={workers}, "
        f"date_range=[{start_date or '-'}, {end_date or '-'}]"
    )
    if not todo:
        return 0, 0, len(ranking)

    n_ok = n_fail = 0
    t0 = time.time()
    with ProcessPoolExecutor(max_workers=workers) as ex:
        fut_map = {
            ex.submit(
                _worker_compute, sym, exch, canon, overwrite,
                start_date, end_date,
            ): (sym, exch)
            for sym, exch in todo
        }
        for i, fut in enumerate(as_completed(fut_map), 1):
            sym, exch = fut_map[fut]
            try:
                res = fut.result()
            except Exception as e:  # noqa: BLE001
                append_fail({
                    "symbol": sym, "exchange": exch, "interval": canon,
                    "error_type": "WorkerCrash", "error": repr(e),
                    "failed_at": ts_now(),
                })
                n_fail += 1
                logger.error(f"  [{i}/{len(todo)}][{canon}] {sym} 进程崩溃: {e}")
                continue

            if res.get("kind") == "finished":
                append_finished({
                    "symbol":       res["symbol"],
                    "exchange":     res["exchange"],
                    "interval":     res["interval"],
                    "status":       res["status"],
                    "days_written": res.get("days_written", 0),
                    "rows":         res.get("rows", 0),
                    "cols":         res.get("cols", 0),
                    "date_start":   res.get("date_start", ""),
                    "date_end":     res.get("date_end", ""),
                    "output_dir":   res.get("output_dir", ""),
                    "completed_at": ts_now(),
                    "detail":       res.get("detail", ""),
                })
                n_ok += 1
                logger.info(
                    f"  [{i}/{len(todo)}][{canon}] OK {sym} "
                    f"days={res.get('days_written',0)} "
                    f"rows={res.get('rows',0)} cols={res.get('cols',0)} "
                    f"[{res.get('date_start','')}~{res.get('date_end','')}] "
                    f"{res.get('detail','')}"
                )
            else:
                append_fail({
                    "symbol":     res["symbol"],
                    "exchange":   res["exchange"],
                    "interval":   res["interval"],
                    "error_type": res.get("error_type", "UnknownError"),
                    "error":      res.get("error", ""),
                    "failed_at":  ts_now(),
                })
                n_fail += 1
                logger.warning(
                    f"  [{i}/{len(todo)}][{canon}] FAIL {sym}: "
                    f"{res.get('error_type')}: {res.get('error','')[:200]}"
                )

    elapsed = time.time() - t0
    logger.info(f"[{canon}] 完成: success={n_ok}, fail={n_fail}, 耗时 {elapsed:.1f}s")
    return n_ok, n_fail, len(ranking) - len(todo)


# =============================================================================
# 截面特征
# =============================================================================
def run_cross_section(
    intervals: List[str],
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> None:
    """
    聚合每个 interval 下所有品种的按日 parquet，计算截面特征后写到
        FEATURE_DIR / {canon} / _all_symbols.parquet
    可选 date range 只读取该范围内的按日文件。
    """
    from cta.feature.cross_section import compute_cross_section_features

    for interval in intervals:
        canon = normalize_interval(interval)
        sub_dir = FEATURE_DIR / canon
        if not sub_dir.exists():
            logger.warning(f"[{canon}] 特征目录不存在，跳过截面")
            continue

        # 遍历每个品种子目录，收集按日 parquet 文件
        symbol_dirs = sorted(
            d for d in sub_dir.iterdir()
            if d.is_dir() and not d.name.startswith("_")
        )
        if not symbol_dirs:
            logger.warning(f"[{canon}] 没有品种子目录, 跳过截面")
            continue

        files: List[Path] = []
        for sd in symbol_dirs:
            for f in sorted(sd.glob("*.parquet")):
                date = f.stem  # YYYY-MM-DD
                if start_date and date < start_date:
                    continue
                if end_date and date > end_date:
                    continue
                files.append(f)
        if not files:
            logger.warning(
                f"[{canon}] 无匹配按日 parquet (range=[{start_date},{end_date}]), 跳过"
            )
            continue

        logger.info(
            f"[{canon}] 截面加载 {len(files)} 个按日 parquet "
            f"(共 {len(symbol_dirs)} 品种) ..."
        )
        t0 = time.time()
        dfs = [pd.read_parquet(f) for f in files]
        all_data = pd.concat(dfs, ignore_index=True)
        del dfs
        gc.collect()

        logger.info(f"[{canon}] 计算截面特征 ({len(all_data)} 行) ...")
        all_data = compute_cross_section_features(all_data)

        merged_path = sub_dir / "_all_symbols.parquet"
        all_data.to_parquet(merged_path, index=False)
        logger.info(
            f"[{canon}] 截面完成: {len(all_data)} 行, {len(all_data.columns)} 列, "
            f"耗时 {time.time()-t0:.1f}s -> {merged_path}"
        )
        del all_data
        gc.collect()


# =============================================================================
# 频率参数解析
# =============================================================================
def _order_by_run_priority(intervals: List[str]) -> List[str]:
    """按 INTERVAL_RUN_ORDER 中的位置排序（长→短），未知值保持原相对顺序排到末尾。"""
    rank = {k: i for i, k in enumerate(INTERVAL_RUN_ORDER)}
    unknown = [i for i in intervals if i not in rank]
    known = [i for i in intervals if i in rank]
    known.sort(key=lambda x: rank[x])
    return known + unknown


def resolve_intervals(arg_intervals: List[str]) -> List[str]:
    """
    把 CLI --interval 输入解析成规范名列表，并按长→短排序（day → minute60 → ... → minute）
      "all"      -> day + 所有已建好的盘中频率
      "intraday" -> 只取盘中（不含 day）
      其它       -> 逐个 normalize_interval 后去重 + 排序
    """
    if not arg_intervals or "all" in arg_intervals:
        return _order_by_run_priority(["day"] + list_available_intervals())
    if "intraday" in arg_intervals:
        return _order_by_run_priority(list_available_intervals())
    canon = [normalize_interval(i) for i in arg_intervals]
    # 去重 + 过滤非法
    seen: set = set()
    out: List[str] = []
    for i in canon:
        if i in ALL_INTERVALS and i not in seen:
            out.append(i)
            seen.add(i)
    return _order_by_run_priority(out)


# =============================================================================
# 排名
# =============================================================================
def load_ranking(symbols_filter: Optional[List[str]] = None,
                 max_rank: Optional[int] = None) -> pd.DataFrame:
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


# =============================================================================
# main
# =============================================================================
def main() -> None:
    parser = argparse.ArgumentParser(
        description="批量生成 CTA 特征（多频率 + 多进程）"
    )
    parser.add_argument(
        "--interval", nargs="+", default=["all"],
        help=("频率列表 (规范名 day/minute/minute5/minute15/minute30/minute60，"
              "也接受 5min/15min/30min/60min；可用 'all' 或 'intraday'，默认 all)"),
    )
    parser.add_argument("--workers", type=int, default=4, help="最大 worker 数 (默认 4)")
    parser.add_argument("--symbols", nargs="*", default=None, help="只跑这些品种")
    parser.add_argument("--max-rank", type=int, default=None, help="只处理 rank <= N")
    parser.add_argument("--overwrite", action="store_true",
                        help="即使按日 parquet 已存在也重算")
    parser.add_argument("--cross-section", action="store_true",
                        help="只跑截面特征（要求各品种按日 parquet 已生成）")
    parser.add_argument("--start-date", default=None,
                        help="只写盘 >=该日期的特征 (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None,
                        help="只写盘 <=该日期的特征 (YYYY-MM-DD)")
    args = parser.parse_args()

    # 简单校验日期格式
    def _check_date(s: Optional[str], name: str) -> Optional[str]:
        if s is None:
            return None
        try:
            datetime.strptime(s, "%Y-%m-%d")
        except ValueError as e:
            raise SystemExit(f"{name} 格式应为 YYYY-MM-DD, got {s!r}: {e}")
        return s

    start_date = _check_date(args.start_date, "--start-date")
    end_date = _check_date(args.end_date, "--end-date")
    if start_date and end_date and start_date > end_date:
        raise SystemExit(
            f"--start-date ({start_date}) 晚于 --end-date ({end_date})"
        )

    intervals = resolve_intervals(args.interval)
    FEATURE_DIR.mkdir(parents=True, exist_ok=True)

    logger.info("=" * 70)
    logger.info("CTA 特征批量生成")
    logger.info(f"  输出根目录:   {FEATURE_DIR}")
    logger.info(f"  finished.csv: {FINISHED_CSV}")
    logger.info(f"  fail.csv:     {FAIL_CSV}")
    logger.info(f"  频率列表:     {intervals}")
    logger.info(f"  max workers:  {args.workers}")
    logger.info(f"  日期范围:     [{start_date or '-'}, {end_date or '-'}]")
    logger.info(f"  overwrite:    {args.overwrite}")
    logger.info("=" * 70)

    if args.cross_section:
        run_cross_section(intervals, start_date=start_date, end_date=end_date)
        logger.info("截面特征计算完成")
        return

    ranking = load_ranking(
        symbols_filter=args.symbols,
        max_rank=args.max_rank,
    )
    if ranking.empty:
        logger.warning("没有品种需要处理")
        return
    logger.info(f"排名总数: {len(ranking)}")

    # 读取已完成
    finished_df = load_finished()
    finished_pairs = finished_success_pairs(finished_df)
    logger.info(f"已完成 (symbol, interval) 数: {len(finished_pairs)}")

    t_total = time.time()
    summary: Dict[str, Tuple[int, int, int]] = {}
    for interval in intervals:
        ok, fail, skip = run_interval(
            interval=interval,
            ranking=ranking,
            finished_pairs=finished_pairs,
            max_workers=args.workers,
            overwrite=args.overwrite,
            start_date=start_date,
            end_date=end_date,
        )
        summary[normalize_interval(interval)] = (ok, fail, skip)

    logger.info("=" * 70)
    for itv, (ok, fail, skip) in summary.items():
        logger.info(f"[{itv:8s}] success={ok}, fail={fail}, skip={skip}")
    logger.info(f"全部完成！总耗时 {time.time()-t_total:.1f}s")
    logger.info(f"截面特征请执行: python3 -m cta.feature.run_all_features --cross-section")


if __name__ == "__main__":
    main()
