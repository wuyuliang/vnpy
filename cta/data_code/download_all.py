"""Batch downloader entrypoint for commodity/financial/index data."""
from __future__ import annotations

import argparse
import logging
import os
import time
from datetime import datetime
from pathlib import Path
from typing import List, Optional

from cta.data_code.download_all_dispatch import (
    MINUTE_INTERVALS,
    RANKING_CSV,
    _is_financial_symbol,
    _normalize_interval_tokens,
    _resolve_macro_build_symbols,
    build_macro_if_enabled,
    load_ranking,
    process_day,
    process_minute_symbol,
)
from cta.data_code.download_all_progress import (
    EMPTY_CSV,
    FINISHED_CSV,
    MAX_EMPTY_DATE,
    TRACKING_DATE,
    append_finished,
    finished_pairs,
    flush_empty_aggregated,
    load_finished,
    migrate_legacy_tracking_files,
)
from cta.data_code.financial_futures_downloader import FinancialFuturesDownloader
from cta.data_code.futures_downloader import ALL_INTERVALS, DATA_DIR, DownloadResult, FuturesDownloader, RateLimiter, alpha_prefix
from cta.data_code.index_downloader import IndexDownloader

logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
logger = logging.getLogger("download_all")

CTA_ROOT = Path(__file__).resolve().parent.parent
CODE_DIR = Path(__file__).resolve().parent


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="按 research_rank 批量下载期货（商品+金融）和指数 reference 数据")
    parser.add_argument(
        "--intervals",
        nargs="+",
        default=list(ALL_INTERVALS),
        help=f"下载频率列表，支持 day/minute/minute5/15/30/60 及别名 (默认全部: {list(ALL_INTERVALS)})",
    )
    parser.add_argument("--workers", type=int, default=4, help="按日并发下载 worker 数，默认 4")
    parser.add_argument("--rate-limit", type=int, default=450, help="tushare 每分钟最大请求数（付费 ~500，默认 450 留余量）")
    parser.add_argument("--max-rank", type=int, default=None, help="只处理 research_rank <= max-rank 的品种")
    parser.add_argument("--only-symbols", nargs="*", default=None, help="只处理给定品种，例如 --only-symbols CU0 RB0")
    parser.add_argument("--token", type=str, default=None, help="tushare token（默认从 TUSHARE_TOKEN 环境变量读取）")
    parser.add_argument("--start", type=str, default="2010-01-01", help="下载开始日期 YYYY-MM-DD")
    parser.add_argument("--end", type=str, default=datetime.now().strftime("%Y-%m-%d"), help="下载结束日期 YYYY-MM-DD（默认: 当天）")
    parser.add_argument("--include-financial", dest="include_financial", action="store_true", default=True, help="是否下载金融期货（IF/IH/IC/IM/T/TF/TS），默认开启")
    parser.add_argument("--exclude-financial", dest="include_financial", action="store_false", help="关闭金融期货下载")
    parser.add_argument("--include-index", dest="include_index", action="store_true", default=True, help="是否下载 reference 指数（000001/000852/000300），默认开启")
    parser.add_argument("--exclude-index", dest="include_index", action="store_false", help="关闭 reference 指数下载")
    parser.add_argument("--build-macro", dest="build_macro", action="store_true", default=True, help="是否构建 macro_daily.parquet，默认开启")
    parser.add_argument("--no-build-macro", dest="build_macro", action="store_false", help="关闭 macro 特征构建")
    return parser


def _log_run_header(intervals: List[str], args: argparse.Namespace) -> None:
    logger.info("=" * 70)
    logger.info("CTA 商品期货批量下载")
    logger.info("  code dir:      %s", CODE_DIR)
    logger.info("  排名文件:      %s", RANKING_CSV)
    logger.info("  数据根目录:    %s", DATA_DIR)
    logger.info("  tracking date: %s", TRACKING_DATE)
    logger.info("  finished csv:  %s", FINISHED_CSV)
    logger.info("  empty csv:     %s", EMPTY_CSV)
    logger.info("  下载频率:      %s", intervals)
    logger.info("  日期范围:      [%s, %s]", args.start, args.end)
    logger.info("  worker 数:     %s", args.workers)
    logger.info("  rate limit:    %s/min", args.rate_limit)
    logger.info("  max_empty:     %s", MAX_EMPTY_DATE)
    logger.info("  include_fin:   %s", args.include_financial)
    logger.info("  include_index: %s", args.include_index)
    logger.info("  build_macro:   %s", args.build_macro)
    if args.max_rank:
        logger.info("  max rank:      %s", args.max_rank)
    if args.only_symbols:
        logger.info("  only symbols:  %s", args.only_symbols)
    logger.info("=" * 70)


def _build_ranking(args: argparse.Namespace):
    ranking = load_ranking()
    if args.max_rank:
        ranking = ranking[ranking["research_rank"] <= args.max_rank].reset_index(drop=True)
    if args.only_symbols:
        want = {s.upper() for s in args.only_symbols}
        ranking = ranking[ranking["symbol"].isin(want)].reset_index(drop=True)
    return ranking


def _handle_financial_day(
    *,
    symbol: str,
    exchange: str,
    prefix: str,
    want_day: bool,
    done_pairs: set[tuple[str, str]],
    fdl: FinancialFuturesDownloader | None,
    args: argparse.Namespace,
    intervals: List[str],
) -> None:
    if fdl is None:
        logger.error("  no tushare token; financial symbol skipped: %s", symbol)
        for itv in intervals:
            if (symbol, itv) in done_pairs:
                continue
            append_finished(
                DownloadResult(
                    symbol=symbol,
                    exchange=exchange,
                    interval=itv,
                    status="error",
                    detail="no tushare token for financial downloader",
                )
            )
            done_pairs.add((symbol, itv))
        return

    if want_day and (symbol, "day") not in done_pairs:
        t0 = time.time()
        try:
            day_df = fdl.fetch_continuous_day(prefix, args.start, args.end)
            if day_df.empty:
                r = DownloadResult(symbol=symbol, exchange=exchange, interval="day", status="empty", detail="financial day empty")
            else:
                r = DownloadResult(
                    symbol=symbol,
                    exchange=exchange,
                    interval="day",
                    status="success",
                    rows=int(len(day_df)),
                    date_start=str(day_df["datetime"].min())[:10],
                    date_end=str(day_df["datetime"].max())[:10],
                )
        except Exception as e:  # noqa: BLE001
            r = DownloadResult(symbol=symbol, exchange=exchange, interval="day", status="error", detail=f"financial day unexpected: {e}")
        append_finished(r)
        done_pairs.add((symbol, "day"))
        logger.info("  [day] %s rows=%s range=[%s~%s] %.1fs", r.status, r.rows, r.date_start, r.date_end, time.time() - t0)
    elif want_day:
        logger.info("  [day] 已完成，跳过")


def _handle_financial_minutes(
    *,
    symbol: str,
    exchange: str,
    prefix: str,
    minute_intervals: List[str],
    done_pairs: set[tuple[str, str]],
    fdl: FinancialFuturesDownloader | None,
    args: argparse.Namespace,
) -> None:
    if fdl is None:
        return
    need_minute_fin = [i for i in minute_intervals if (symbol, i) not in done_pairs]
    for itv in need_minute_fin:
        t0 = time.time()
        try:
            fdl.fetch_continuous_minute(prefix, itv, args.start, args.end)
            base = DATA_DIR / "origin" / itv / prefix
            files = sorted(base.glob("*.parquet")) if base.exists() else []
            if files:
                r = DownloadResult(
                    symbol=symbol,
                    exchange=exchange,
                    interval=itv,
                    status="success",
                    rows=0,
                    date_start=files[0].stem,
                    date_end=files[-1].stem,
                    detail=f"files={len(files)}",
                )
            else:
                r = DownloadResult(symbol=symbol, exchange=exchange, interval=itv, status="empty", detail="financial minute empty")
        except Exception as e:  # noqa: BLE001
            r = DownloadResult(symbol=symbol, exchange=exchange, interval=itv, status="error", detail=f"financial minute unexpected: {e}")
        append_finished(r)
        done_pairs.add((symbol, itv))
        logger.info("  [%s] %s rows=%s range=[%s~%s] %.1fs detail=%s", itv, r.status, r.rows, r.date_start, r.date_end, time.time() - t0, r.detail)


def _handle_commodity(
    *,
    symbol: str,
    exchange: str,
    want_day: bool,
    minute_intervals: List[str],
    done_pairs: set[tuple[str, str]],
    dl: FuturesDownloader,
    args: argparse.Namespace,
) -> None:
    if want_day and (symbol, "day") not in done_pairs:
        t0 = time.time()
        try:
            r = process_day(dl, symbol, exchange)
        except Exception as e:  # noqa: BLE001
            r = DownloadResult(symbol=symbol, exchange=exchange, interval="day", status="error", detail=f"unexpected: {e}")
        append_finished(r)
        done_pairs.add((symbol, "day"))
        logger.info("  [day] %s rows=%s range=[%s~%s] %.1fs", r.status, r.rows, r.date_start, r.date_end, time.time() - t0)
    elif want_day:
        logger.info("  [day] 已完成，跳过")

    need_minute = [i for i in minute_intervals if (symbol, i) not in done_pairs]
    if not need_minute:
        if minute_intervals:
            logger.info("  [minute*] 全部已完成，跳过")
        return

    if not dl.token:
        logger.error("  未设置 TUSHARE_TOKEN，跳过分钟级下载")
        for itv in need_minute:
            append_finished(DownloadResult(symbol=symbol, exchange=exchange, interval=itv, status="error", detail="no tushare token"))
            done_pairs.add((symbol, itv))
        return

    t0 = time.time()
    try:
        results, empties = process_minute_symbol(dl, symbol, exchange, need_minute, workers=args.workers)
    except Exception as e:  # noqa: BLE001
        logger.error("  process_minute_symbol 异常: %s", e, exc_info=True)
        results = {
            itv: DownloadResult(symbol=symbol, exchange=exchange, interval=itv, status="error", detail=f"unexpected: {e}")
            for itv in need_minute
        }
        empties = []

    if empties:
        n_ranges = flush_empty_aggregated(empties)
        logger.info("  empty ranges written: %s (raw empty days=%s)", n_ranges, len(empties))

    for itv in need_minute:
        r = results.get(itv) or DownloadResult(symbol=symbol, exchange=exchange, interval=itv, status="error", detail="missing result")
        append_finished(r)
        done_pairs.add((symbol, itv))
        logger.info("  [%s] %s rows=%s range=[%s~%s] detail=%s", itv, r.status, r.rows, r.date_start, r.date_end, r.detail)
    logger.info("  symbol minutes total: %.1fs", time.time() - t0)


def main() -> None:
    args = _build_parser().parse_args()
    intervals: List[str] = _normalize_interval_tokens(list(args.intervals))
    minute_intervals = [i for i in intervals if i in MINUTE_INTERVALS]
    want_day = "day" in intervals

    DATA_DIR.mkdir(parents=True, exist_ok=True)
    CODE_DIR.mkdir(parents=True, exist_ok=True)
    migrate_legacy_tracking_files()
    _log_run_header(intervals, args)

    ranking = _build_ranking(args)
    if ranking.empty:
        logger.warning("没有品种需要处理")
        return

    done_pairs = finished_pairs(load_finished())
    logger.info("已完成记录: %s 条 (按 symbol+interval)", len(done_pairs))

    dl = FuturesDownloader(token=args.token, rate_limit=args.rate_limit, workers=args.workers)
    shared_rl = RateLimiter(args.rate_limit)
    has_tushare_token = bool(str(args.token or os.getenv("TUSHARE_TOKEN", "")).strip())
    fdl: Optional[FinancialFuturesDownloader] = None
    idx_dl: Optional[IndexDownloader] = None
    if (args.include_financial or args.include_index) and has_tushare_token:
        if args.include_financial:
            fdl = FinancialFuturesDownloader(rate_limiter=shared_rl)
        if args.include_index:
            idx_dl = IndexDownloader(rate_limiter=shared_rl)
    elif args.include_financial or args.include_index:
        logger.warning("no tushare token, skip financial/index download")

    if args.include_index and idx_dl is not None:
        try:
            idx_dl.fetch_all(start=args.start, end=args.end)
        except Exception as e:  # noqa: BLE001
            logger.error("index download failed: %s", e, exc_info=True)

    t_total = time.time()
    total_sym = len(ranking)
    for idx, row in ranking.iterrows():
        symbol = str(row["symbol"])
        exchange = str(row["exchange"])
        rank = int(row["research_rank"])
        logger.info("-" * 70)
        logger.info("[%s/%s] rank=%s %s (%s)", idx + 1, total_sym, rank, symbol, exchange)

        if _is_financial_symbol(symbol):
            if not args.include_financial:
                logger.info("  financial disabled, skip symbol=%s", symbol)
                continue
            prefix = alpha_prefix(symbol)
            _handle_financial_day(
                symbol=symbol,
                exchange=exchange,
                prefix=prefix,
                want_day=want_day,
                done_pairs=done_pairs,
                fdl=fdl,
                args=args,
                intervals=intervals,
            )
            _handle_financial_minutes(
                symbol=symbol,
                exchange=exchange,
                prefix=prefix,
                minute_intervals=minute_intervals,
                done_pairs=done_pairs,
                fdl=fdl,
                args=args,
            )
            continue

        _handle_commodity(
            symbol=symbol,
            exchange=exchange,
            want_day=want_day,
            minute_intervals=minute_intervals,
            done_pairs=done_pairs,
            dl=dl,
            args=args,
        )

    if args.build_macro:
        try:
            out_path, rows, cols, symbols = build_macro_if_enabled()
            if out_path is None:
                from cta.feature.macro_feature import MacroFeatureBuilder

                builder = MacroFeatureBuilder()
                logger.warning(
                    "skip macro feature build: no index day csv found under %s "
                    "(expected e.g. 000001_SH.csv). Run with --include-index (and valid token) first.",
                    builder.index_root,
                )
            else:
                logger.info("macro feature built: %s rows=%s cols=%s (symbols=%s)", out_path, rows, cols, symbols)
        except Exception as e:  # noqa: BLE001
            logger.error("macro feature build failed: %s", e, exc_info=True)

    elapsed = time.time() - t_total
    logger.info("=" * 70)
    logger.info("全部完成！总耗时 %.1fs", elapsed)
    logger.info("finished csv: %s", FINISHED_CSV)
    logger.info("empty csv:    %s", EMPTY_CSV)


__all__ = [
    "_normalize_interval_tokens",
    "_is_financial_symbol",
    "_resolve_macro_build_symbols",
    "main",
]


if __name__ == "__main__":
    main()
