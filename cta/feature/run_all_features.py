"""Main entrypoint for batch feature generation."""
from __future__ import annotations

import argparse
import logging
import time
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from cta.feature.feature_compute_dispatch import FEATURE_DIR, FINISHED_CSV, FAIL_CSV, finished_success_pairs, load_finished
from cta.feature.feature_interval_runner import load_ranking, resolve_intervals, run_cross_section, run_interval

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("feature.run_all")

CTA_ROOT = Path(__file__).resolve().parent.parent


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="批量生成 CTA 特征（多频率 + 多进程）")
    parser.add_argument(
        "--interval",
        nargs="+",
        default=["all"],
        help=("频率列表 (规范名 day/minute/minute5/minute15/minute30/minute60，"
              "也接受 5min/15min/30min/60min；可用 'all' 或 'intraday'，默认 all)"),
    )
    parser.add_argument("--workers", type=int, default=4, help="最大 worker 数 (默认 4)")
    parser.add_argument("--symbols", nargs="*", default=None, help="只跑这些品种")
    parser.add_argument("--max-rank", type=int, default=None, help="只处理 rank <= N")
    parser.add_argument("--overwrite", action="store_true", help="即使按日 parquet 已存在也重算")
    parser.add_argument("--cross-section", action="store_true", help="只跑截面特征（要求各品种按日 parquet 已生成）")
    parser.add_argument("--start-date", default=None, help="只写盘 >=该日期的特征 (YYYY-MM-DD)")
    parser.add_argument("--end-date", default=None, help="只写盘 <=该日期的特征 (YYYY-MM-DD)")
    parser.add_argument("--build-macro", action="store_true", help="构建宏观 reference 特征 cta/data/feature/macro/macro_daily.parquet")
    parser.add_argument(
        "--macro-feature-path",
        default=str(CTA_ROOT / "data" / "feature" / "macro" / "macro_daily.parquet"),
        help="macro feature output parquet path",
    )
    return parser


def _check_date(s: Optional[str], name: str) -> Optional[str]:
    if s is None:
        return None
    try:
        datetime.strptime(s, "%Y-%m-%d")
    except ValueError as e:
        raise SystemExit(f"{name} 格式应为 YYYY-MM-DD, got {s!r}: {e}") from e
    return s


def _validate_dates(start_date: Optional[str], end_date: Optional[str]) -> Tuple[Optional[str], Optional[str]]:
    s = _check_date(start_date, "--start-date")
    e = _check_date(end_date, "--end-date")
    if s and e and s > e:
        raise SystemExit(f"--start-date ({s}) 晚于 --end-date ({e})")
    return s, e


def _log_header(intervals: List[str], workers: int, start_date: Optional[str], end_date: Optional[str], overwrite: bool) -> None:
    logger.info("=" * 70)
    logger.info("CTA 特征批量生成")
    logger.info("  输出根目录:   %s", FEATURE_DIR)
    logger.info("  finished.csv: %s", FINISHED_CSV)
    logger.info("  fail.csv:     %s", FAIL_CSV)
    logger.info("  频率列表:     %s", intervals)
    logger.info("  max workers:  %s", workers)
    logger.info("  日期范围:     [%s, %s]", start_date or "-", end_date or "-")
    logger.info("  overwrite:    %s", overwrite)
    logger.info("=" * 70)


def _run_macro_feature(out_path: str) -> None:
    from cta.feature.macro_feature import MacroFeatureBuilder

    builder = MacroFeatureBuilder()
    macro_df = builder.build()
    saved = builder.save(macro_df, out_path=Path(out_path))
    logger.info("macro feature saved: %s rows=%s cols=%s", saved, len(macro_df), len(macro_df.columns))


def main() -> None:
    args = _build_parser().parse_args()
    start_date, end_date = _validate_dates(args.start_date, args.end_date)
    intervals = resolve_intervals(args.interval)
    FEATURE_DIR.mkdir(parents=True, exist_ok=True)
    _log_header(intervals, args.workers, start_date, end_date, args.overwrite)

    if args.cross_section:
        run_cross_section(intervals, start_date=start_date, end_date=end_date)
        logger.info("截面特征计算完成")
        return

    ranking = load_ranking(symbols_filter=args.symbols, max_rank=args.max_rank)
    if ranking.empty:
        logger.warning("没有品种需要处理")
        return
    logger.info("排名总数: %s", len(ranking))

    finished_df = load_finished()
    finished_pairs = finished_success_pairs(finished_df)
    logger.info("已完成 (symbol, interval) 数: %s", len(finished_pairs))
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
        summary[interval] = (ok, fail, skip)

    logger.info("=" * 70)
    for itv, (ok, fail, skip) in summary.items():
        logger.info("[%8s] success=%s, fail=%s, skip=%s", itv, ok, fail, skip)
    logger.info("全部完成！总耗时 %.1fs", time.time() - t_total)
    logger.info("截面特征请执行: python3 -m cta.feature.run_all_features --cross-section")

    if args.build_macro:
        _run_macro_feature(args.macro_feature_path)


__all__ = ["resolve_intervals", "main"]


if __name__ == "__main__":
    main()
