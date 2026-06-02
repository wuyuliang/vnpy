"""Dispatch/data-loading helpers for ``download_all``."""
from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from threading import Lock
from typing import Dict, List, Sequence, Set, Tuple

import pandas as pd

from cta.data_code.futures_downloader import (
    DATA_DIR,
    DAY_DIR,
    MINUTE_INTERVALS,
    DownloadResult,
    FuturesDownloader,
    alpha_prefix,
)
from cta.data_code.index_downloader import IndexDownloader
from cta.feature.macro_feature import MacroFeatureBuilder

logger = logging.getLogger(__name__)

CTA_ROOT = Path(__file__).resolve().parent.parent
RANKING_CSV = CTA_ROOT / "feature" / "symbols_research_ranking.csv"

_INTERVAL_ALIAS: Dict[str, str] = {
    "day": "day",
    "min": "minute",
    "minute": "minute",
    "1min": "minute",
    "minute5": "minute5",
    "5min": "minute5",
    "minute15": "minute15",
    "15min": "minute15",
    "minute30": "minute30",
    "30min": "minute30",
    "minute60": "minute60",
    "60min": "minute60",
}


def _normalize_interval_tokens(tokens: List[str]) -> List[str]:
    out: List[str] = []
    seen: Set[str] = set()
    for raw in tokens:
        key = str(raw).strip().lower()
        if key not in _INTERVAL_ALIAS:
            raise ValueError(f"unsupported interval token: {raw}")
        canon = _INTERVAL_ALIAS[key]
        if canon in seen:
            continue
        seen.add(canon)
        out.append(canon)
    return out


def _is_financial_symbol(symbol: str) -> bool:
    from cta.data_code.financial_futures_downloader import FinancialFuturesDownloader

    return alpha_prefix(symbol) in set(FinancialFuturesDownloader.SUPPORTED_PREFIXES)


def _resolve_macro_build_symbols(
    *,
    index_day_dir: Path,
    references: Sequence[tuple[str, str]] | None = None,
) -> List[str]:
    refs = list(references) if references is not None else IndexDownloader.load_reference_symbols()
    day_dir = Path(index_day_dir)
    if not day_dir.exists():
        return []
    out: List[str] = []
    for ts_code, _ in refs:
        code = str(ts_code).upper().strip()
        if not code:
            continue
        safe = code.replace(".", "_")
        if (day_dir / f"{safe}.csv").exists():
            out.append(code)
    return out


def _existing_dates(symbol: str, interval: str) -> Set[str]:
    d = DATA_DIR / "origin" / interval / alpha_prefix(symbol)
    if not d.exists():
        return set()
    return {p.stem for p in d.glob("*.parquet")}


def process_day(
    dl: FuturesDownloader,
    symbol: str,
    exchange: str,
    *,
    overwrite: bool = False,
) -> DownloadResult:
    return dl.download_day(symbol, exchange, out_dir=DAY_DIR, overwrite=overwrite)


def process_minute_symbol(
    dl: FuturesDownloader,
    symbol: str,
    exchange: str,
    intervals: List[str],
    workers: int,
) -> Tuple[Dict[str, DownloadResult], List[Tuple[str, str, str, str, str]]]:
    empties: List[Tuple[str, str, str, str, str]] = []
    emp_lock = Lock()

    def _record_empty(itv: str, d: str, reason: str) -> None:
        with emp_lock:
            empties.append((symbol, exchange, itv, d, reason or "empty"))

    mapping_df, suf = dl.fetch_fut_mapping(symbol, exchange)
    if mapping_df.empty:
        return (
            {
                itv: DownloadResult(
                    symbol=symbol,
                    exchange=exchange,
                    interval=itv,
                    status="empty",
                    detail="fut_mapping 无数据（所有 exchange 变体失败）",
                )
                for itv in intervals
            },
            empties,
        )
    logger.info("  mapping -> ts_suffix=%s, trade_days=%s", suf, len(mapping_df))

    date_contract = list(zip(mapping_df["trade_date"], mapping_df["mapping_ts_code"]))
    existing_by_itv: Dict[str, Set[str]] = {itv: _existing_dates(symbol, itv) for itv in intervals}
    todo: List[Tuple[str, str]] = []
    for d, c in date_contract:
        if any(d not in existing_by_itv[itv] for itv in intervals):
            todo.append((d, c))
    skip_cnt = len(date_contract) - len(todo)
    logger.info("  dates total=%s, skip(exists)=%s, to_fetch=%s", len(date_contract), skip_cnt, len(todo))

    stats: Dict[str, Dict[str, int]] = {
        itv: {"success": 0, "empty": 0, "skip": 0, "error": 0, "rows": 0}
        for itv in intervals
    }
    date_range: Dict[str, List[str]] = {itv: [] for itv in intervals}

    def _one(date_contract_pair: Tuple[str, str]) -> Dict[str, DownloadResult]:
        d, c = date_contract_pair
        return dl.download_day_minute_all(
            symbol=symbol,
            exchange=exchange,
            contract_code=c,
            trade_date=d,
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
                    logger.error("  [%s %s] 未捕获异常: %s", d, c, e)
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
                    logger.info("  progress %s/%s", i, len(todo))
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
                logger.info("  progress %s/%s", i, len(todo))

    results: Dict[str, DownloadResult] = {}
    for itv in intervals:
        s = stats[itv]
        all_success_dates = sorted(set(date_range[itv]) | existing_by_itv[itv])
        if all_success_dates:
            detail = (
                f"success_day={s['success']}, empty_day={s['empty']}, "
                f"skip_day={s['skip']}, error_day={s['error']}"
            )
            results[itv] = DownloadResult(
                symbol=symbol,
                exchange=exchange,
                interval=itv,
                status="success",
                rows=s["rows"],
                date_start=all_success_dates[0],
                date_end=all_success_dates[-1],
                detail=detail,
            )
        else:
            results[itv] = DownloadResult(
                symbol=symbol,
                exchange=exchange,
                interval=itv,
                status="empty",
                detail=f"all_days_empty/error: empty={s['empty']}, error={s['error']}",
            )
    return results, empties


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


def build_macro_if_enabled() -> tuple[Path | None, int, int, list[str]]:
    builder = MacroFeatureBuilder()
    refs = IndexDownloader.load_reference_symbols()
    available_symbols = _resolve_macro_build_symbols(
        index_day_dir=builder.index_root,
        references=refs,
    )
    if not available_symbols:
        return None, 0, 0, []
    macro_df = builder.build(symbols=available_symbols)
    out_path = builder.save(macro_df)
    return out_path, len(macro_df), len(macro_df.columns), available_symbols


__all__ = [
    "RANKING_CSV",
    "MINUTE_INTERVALS",
    "_normalize_interval_tokens",
    "_is_financial_symbol",
    "_resolve_macro_build_symbols",
    "process_day",
    "process_minute_symbol",
    "load_ranking",
    "build_macro_if_enabled",
]
