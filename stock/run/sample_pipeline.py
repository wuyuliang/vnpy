"""Stock pipeline: download day data, scan signals, render symbol charts."""
from __future__ import annotations

import argparse
import json
import logging
import os
import shutil
from datetime import datetime
from pathlib import Path
from typing import Any

import pandas as pd

from stock.analysis.render_symbol_bull_pullback_charts import render_all, render_opportunity_rows_to_dir
from stock.config.default_universe import get_default_sample_universe
from stock.data_code.stock_downloader import (
    StockDownloader,
    is_st_stock_name,
    stock_price_limit_threshold_pct,
)
from stock.strategy.big_bull_mode import (
    BIG_BULL_COLUMNS,
    BIG_BULL_SIGNAL_TYPE,
    BigBullConfig,
    extract_return_60d_by_opportunity_date,
    scan_ma5_ma10_big_bull,
    score_and_filter_big_bull_opportunities,
)
from stock.strategy.signal_evaluators import (
    BullPullbackConfig,
    scan_breakout_pullback_continuation,
    scan_bull_pullback_continuation,
    scan_stock_signal_opportunities,
    scan_volume_spike_up,
)

logger = logging.getLogger(__name__)


def _default_data_root() -> Path:
    return Path(__file__).resolve().parents[1] / "data" / "origin"


def _default_report_root() -> Path:
    return Path(__file__).resolve().parents[1] / "report" / "opportunities"


def _default_daily_opportunity_root(report_root: Path) -> Path:
    if report_root.name == "opportunities":
        return report_root.parent / "opportunity_date"
    return report_root / "opportunity_date"


def _default_volume_spike_daily_root() -> Path:
    return Path(__file__).resolve().parents[1] / "report" / "opportunities_date_spike_up"


def _default_breakout_daily_root() -> Path:
    return Path(__file__).resolve().parents[1] / "report" / "opportunities_date_break_out"


def _default_big_bull_daily_root() -> Path:
    return Path(__file__).resolve().parents[1] / "report" / "opportunities_date_ma5_ma10_big"


def _default_analysis_root() -> Path:
    return Path(__file__).resolve().parents[1] / "analysis"


def _run_id() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S_stock_signals")


def resolve_latest_trade_date_range(
    downloader: Any,
    *,
    latest_days: int,
    today: str | None = None,
) -> tuple[str, str]:
    """Resolve the latest N open trading dates into a start/end date range."""
    if latest_days <= 0:
        raise ValueError("--latest-days must be a positive integer")
    end = pd.Timestamp(today or datetime.now()).normalize()
    lookback_days = max(30, latest_days * 4 + 14)
    start = end - pd.Timedelta(days=lookback_days)
    trade_dates = downloader.list_open_trade_dates(str(start.date()), str(end.date()))
    if not trade_dates:
        raise RuntimeError(f"no open trade dates found between {start.date()} and {end.date()}")
    selected = trade_dates[-latest_days:]
    return selected[0], selected[-1]


def load_local_day_frame(data_root: Path, ts_code: str) -> pd.DataFrame:
    """Load one day csv from local storage."""
    path = Path(data_root) / "day" / f"{ts_code.replace('.', '_')}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def discover_local_symbols(data_root: Path) -> list[dict[str, str]]:
    """Discover locally downloaded day csv files as a symbol list."""
    day_dir = Path(data_root) / "day"
    if not day_dir.exists():
        return []
    symbols: list[dict[str, str]] = []
    for path in sorted(day_dir.glob("*.csv")):
        stem = path.stem.upper()
        if stem.endswith("_SH"):
            ts_code = stem[:-3] + ".SH"
            exchange = "SSE"
        elif stem.endswith("_SZ"):
            ts_code = stem[:-3] + ".SZ"
            exchange = "SZSE"
        else:
            continue
        symbols.append({"ts_code": ts_code, "exchange": exchange, "name": ""})
    return symbols


def filter_non_st_symbols(symbols: list[dict[str, str]]) -> list[dict[str, str]]:
    """Remove symbols whose names indicate ST status."""
    return [
        item
        for item in symbols
        if str(item.get("name", "")).strip()
        and not is_st_stock_name(item.get("name", ""))
    ]


def _empty_opportunity_frame() -> pd.DataFrame:
    base_columns = [
        "symbol",
        "exchange",
        "name",
        "total_mv",
        "circ_mv",
        "limit_up_count_2y",
        "limit_down_count_2y",
        "signal_type",
        "signal_datetime",
        "opportunity_date",
        "signal_price",
        "close_price",
        "entry_datetime",
        "entry_price",
        "entry_action",
        "trigger",
        "ema5",
        "ema10",
        "ema20",
        "volume",
        "volume_prev",
        "volume_5_avg",
        "volume_3_avg",
        "volume_10_avg",
        "volume_condition",
        "lookback_high_18m",
        "close_to_lookback_high",
        "breakout_level",
        "pullback_low",
        "pullback_high",
        "bars_since_breakout",
    ]
    return pd.DataFrame(
        columns=base_columns + [column for column in BIG_BULL_COLUMNS if column not in base_columns]
    )


def merge_opportunity_rows(rows: list[pd.DataFrame]) -> pd.DataFrame:
    """Merge signal rows into the pipeline opportunity schema."""
    if rows:
        merged = pd.concat(rows, ignore_index=True)
        if "opportunity_date" in merged.columns:
            merged = merged.sort_values(["opportunity_date", "symbol", "signal_datetime"]).reset_index(drop=True)
        return merged
    return _empty_opportunity_frame()


def filter_opportunities_by_date_range(merged: pd.DataFrame, *, start: str, end: str) -> pd.DataFrame:
    """Keep opportunities whose opportunity_date falls inside the requested date range."""
    if merged.empty or "opportunity_date" not in merged.columns:
        return merged
    dates = pd.to_datetime(merged["opportunity_date"], errors="coerce")
    mask = (dates >= pd.Timestamp(start)) & (dates <= pd.Timestamp(end))
    return merged.loc[mask.fillna(False)].reset_index(drop=True)


def filter_non_st_opportunities(merged: pd.DataFrame) -> pd.DataFrame:
    """Remove ST rows and fail closed when stock names are unavailable."""
    if merged.empty:
        return merged
    if "name" not in merged.columns:
        return merged.iloc[0:0].copy()
    names = merged["name"].fillna("").astype(str).str.strip()
    mask = names.ne("") & ~names.map(is_st_stock_name)
    return merged.loc[mask].reset_index(drop=True)


def _symbol_metadata(symbols: list[dict[str, str]]) -> pd.DataFrame:
    rows = [
        {
            "symbol": str(item.get("ts_code", "")).upper(),
            "exchange": str(item.get("exchange", "")).upper(),
            "name": str(item.get("name", "")),
        }
        for item in symbols
        if str(item.get("ts_code", "")).strip()
    ]
    return pd.DataFrame(rows).drop_duplicates("symbol", keep="last") if rows else pd.DataFrame(columns=["symbol", "exchange", "name"])


def calculate_two_year_limit_counts(frame: pd.DataFrame, end: str) -> tuple[int, int]:
    """Count limit days from the two-year cutoff through the backtest end."""
    if frame.empty:
        return 0, 0
    bars = frame.copy()
    bars["datetime"] = pd.to_datetime(bars.get("datetime"), errors="coerce")
    bars = bars.dropna(subset=["datetime"]).copy()
    cutoff = pd.Timestamp(end) - pd.DateOffset(years=2)
    bars = bars[(bars["datetime"] >= cutoff) & (bars["datetime"] <= pd.Timestamp(end))].copy()
    if bars.empty:
        return 0, 0
    if "pct_chg" in bars.columns:
        pct_chg = pd.to_numeric(bars["pct_chg"], errors="coerce")
    else:
        close = pd.to_numeric(bars.get("close"), errors="coerce")
        pct_chg = close.pct_change() * 100.0
    symbol = str(bars["symbol"].iloc[0]) if "symbol" in bars.columns else ""
    limit_threshold = stock_price_limit_threshold_pct(symbol)
    return (
        int((pct_chg >= limit_threshold).sum()),
        int((pct_chg <= -limit_threshold).sum()),
    )


def _normalize_daily_basic(frame: pd.DataFrame | None) -> pd.DataFrame:
    columns = ["symbol", "latest_close", "total_mv", "circ_mv"]
    if frame is None or frame.empty:
        return pd.DataFrame(columns=columns)
    daily_basic = frame.copy()
    if "ts_code" not in daily_basic.columns:
        return pd.DataFrame(columns=columns)
    daily_basic["symbol"] = daily_basic["ts_code"].astype(str).str.upper()
    for source, target in [("close", "latest_close"), ("total_mv", "total_mv"), ("circ_mv", "circ_mv")]:
        if source in daily_basic.columns:
            daily_basic[target] = pd.to_numeric(daily_basic[source], errors="coerce")
        else:
            daily_basic[target] = pd.NA
    return daily_basic.loc[:, columns].drop_duplicates("symbol", keep="last").reset_index(drop=True)


def enrich_opportunities(
    merged: pd.DataFrame,
    *,
    symbols: list[dict[str, str]],
    limit_counts_by_symbol: dict[str, tuple[int, int]],
    latest_daily_basic: pd.DataFrame | None,
) -> pd.DataFrame:
    """Attach name, market cap, two-year limit counts, and close price fields."""
    if merged.empty:
        return _empty_opportunity_frame()

    enriched = merged.copy()
    metadata = _symbol_metadata(symbols)
    if not metadata.empty:
        enriched = enriched.merge(metadata.loc[:, ["symbol", "name"]], on="symbol", how="left")
    if "name" not in enriched.columns:
        enriched["name"] = ""
    enriched["name"] = enriched["name"].fillna("")

    daily_basic = _normalize_daily_basic(latest_daily_basic)
    if not daily_basic.empty:
        enriched = enriched.merge(daily_basic, on="symbol", how="left")
    for column in ["total_mv", "circ_mv", "latest_close"]:
        if column not in enriched.columns:
            enriched[column] = pd.NA
    enriched["limit_up_count_2y"] = enriched["symbol"].map(lambda value: limit_counts_by_symbol.get(str(value), (0, 0))[0])
    enriched["limit_down_count_2y"] = enriched["symbol"].map(lambda value: limit_counts_by_symbol.get(str(value), (0, 0))[1])
    if "close_price" not in enriched.columns:
        enriched["close_price"] = enriched["latest_close"]
    enriched["close_price"] = pd.to_numeric(enriched["close_price"], errors="coerce").fillna(enriched["latest_close"])
    enriched = enriched.drop(columns=["latest_close"], errors="ignore")
    for column in _empty_opportunity_frame().columns:
        if column not in enriched.columns:
            enriched[column] = pd.NA
    return enriched.loc[:, _empty_opportunity_frame().columns]


def write_opportunity_csv(merged: pd.DataFrame, report_root: Path, run_id: str) -> Path:
    """Write merged opportunity rows to csv."""
    report_root.mkdir(parents=True, exist_ok=True)
    output = report_root / f"{run_id}_stock_signal_opportunities.csv"
    merged.to_csv(output, index=False, encoding="utf-8-sig")
    return output


def write_daily_opportunity_dirs(
    merged: pd.DataFrame,
    daily_root: Path,
    run_id: str,
    *,
    data_root: Path,
    chart_end_date: str | None = None,
) -> Path:
    """Write date directories containing retrospective symbol-summary PNGs."""
    output_root = Path(daily_root) / run_id
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.mkdir(parents=True, exist_ok=True)

    index_rows: list[dict[str, Any]] = []
    all_rows_by_symbol: dict[str, list[dict[str, Any]]] = {}
    if not merged.empty and "symbol" in merged.columns:
        ordered = merged.sort_values(
            [
                column
                for column in ["symbol", "opportunity_date", "signal_datetime"]
                if column in merged.columns
            ]
        )
        for row in ordered.to_dict("records"):
            symbol = str(row.get("symbol") or "").strip()
            if symbol:
                all_rows_by_symbol.setdefault(symbol, []).append(row)
    render_cache: dict[str, Path] = {}
    if not merged.empty and "opportunity_date" in merged.columns:
        dates = sorted(
            {str(value) for value in merged["opportunity_date"].dropna() if str(value).strip()},
            reverse=True,
        )
        for rank, opportunity_date in enumerate(dates, start=1):
            date_dir_name = f"{rank:04d}_{opportunity_date}"
            date_dir = output_root / date_dir_name
            date_dir.mkdir(parents=True, exist_ok=True)
            day_frame = merged[merged["opportunity_date"].astype(str) == opportunity_date].copy()
            sort_columns = [column for column in ["symbol", "signal_datetime", "entry_datetime"] if column in day_frame.columns]
            if sort_columns:
                day_frame = day_frame.sort_values(sort_columns)
            render_summary = render_opportunity_rows_to_dir(
                day_frame.to_dict("records"),
                output_dir=date_dir,
                data_root=Path(data_root),
                render_cache=render_cache,
                cache_rows_by_key=all_rows_by_symbol,
                bars_end_date=chart_end_date,
            )
            index_rows.append(
                {
                    "rank": rank,
                    "opportunity_date": opportunity_date,
                    "opportunity_count": len(day_frame),
                    "symbol_count": render_summary["symbols"],
                    "rendered_images": render_summary["rendered_images"],
                    "missing_bars": render_summary["missing_bars"],
                    "path": date_dir_name,
                }
            )

    index_lines = ["# Daily Opportunities", ""]
    for row in index_rows:
        index_lines.append(
            f"- {row['rank']:04d} {row['opportunity_date']} "
            f"opportunities={row['opportunity_count']} symbols={row['symbol_count']} path={row['path']}"
        )
    (output_root / "index.md").write_text("\n".join(index_lines) + "\n", encoding="utf-8")
    (output_root / "summary.json").write_text(
        json.dumps(
            {
                "run_id": run_id,
                "chart_mode": "retrospective_symbol_summary",
                "chart_end_date": chart_end_date,
                "date_count": len(index_rows),
                "dates": index_rows,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return output_root


def write_volume_spike_up_daily_dirs(
    merged: pd.DataFrame,
    daily_root: Path,
    run_id: str,
    *,
    data_root: Path,
    chart_end_date: str | None = None,
) -> Path:
    """Write daily PNG dirs for volume_spike_up opportunities only."""
    if merged.empty or "signal_type" not in merged.columns:
        spike_frame = merged.iloc[0:0].copy()
    else:
        spike_frame = merged[merged["signal_type"].astype(str) == "volume_spike_up"].reset_index(drop=True)
    return write_daily_opportunity_dirs(
        spike_frame,
        daily_root,
        run_id,
        data_root=data_root,
        chart_end_date=chart_end_date,
    )


def write_breakout_pullback_daily_dirs(
    merged: pd.DataFrame,
    daily_root: Path,
    run_id: str,
    *,
    data_root: Path,
    chart_end_date: str | None = None,
) -> Path:
    """Write daily PNG dirs for breakout_pullback_continuation opportunities only."""
    if merged.empty or "signal_type" not in merged.columns:
        breakout_frame = merged.iloc[0:0].copy()
    else:
        breakout_frame = merged[merged["signal_type"].astype(str) == "breakout_pullback_continuation"].reset_index(drop=True)
    return write_daily_opportunity_dirs(
        breakout_frame,
        daily_root,
        run_id,
        data_root=data_root,
        chart_end_date=chart_end_date,
    )


def write_big_bull_daily_dirs(
    merged: pd.DataFrame,
    daily_root: Path,
    run_id: str,
    *,
    data_root: Path,
    chart_end_date: str | None = None,
) -> Path:
    """Write daily PNG dirs for MA5/MA10 big-bull opportunities only."""
    if merged.empty or "signal_type" not in merged.columns:
        big_bull_frame = merged.iloc[0:0].copy()
    else:
        big_bull_frame = merged[
            merged["signal_type"].astype(str) == BIG_BULL_SIGNAL_TYPE
        ].reset_index(drop=True)
    return write_daily_opportunity_dirs(
        big_bull_frame,
        daily_root,
        run_id,
        data_root=data_root,
        chart_end_date=chart_end_date,
    )


def _hardlink_or_copy(source: str, destination: str) -> str:
    try:
        os.link(source, destination)
    except OSError:
        shutil.copy2(source, destination)
    return destination


def clone_daily_opportunity_dirs(source_root: Path, daily_root: Path, run_id: str) -> Path:
    """Clone a single-signal daily tree using hard links where supported."""
    source_root = Path(source_root)
    output_root = Path(daily_root) / run_id
    if source_root.resolve() == output_root.resolve():
        return source_root
    if output_root.exists():
        shutil.rmtree(output_root)
    output_root.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(source_root, output_root, copy_function=_hardlink_or_copy)
    return output_root


def fetch_latest_daily_basic(downloader: StockDownloader | None, start: str, end: str) -> pd.DataFrame:
    """Fetch the latest available daily_basic snapshot in the requested date range."""
    if downloader is None:
        return pd.DataFrame(columns=["ts_code", "trade_date", "close", "total_mv", "circ_mv"])
    for trade_date in reversed(downloader.list_open_trade_dates(start, end)):
        daily_basic = downloader.fetch_daily_basic(trade_date)
        if not daily_basic.empty:
            return daily_basic
    return pd.DataFrame(columns=["ts_code", "trade_date", "close", "total_mv", "circ_mv"])


def scan_frame_for_signal_type(
    frame: pd.DataFrame,
    cfg: BullPullbackConfig,
    signal_type: str,
    *,
    big_bull_cfg: BigBullConfig | None = None,
) -> pd.DataFrame:
    """Scan one local symbol frame for the requested signal family."""
    if signal_type == "all":
        rows = [
            scan_stock_signal_opportunities(frame, cfg),
            scan_ma5_ma10_big_bull(frame, big_bull_cfg or BigBullConfig()),
        ]
        non_empty = [item for item in rows if not item.empty]
        return (
            pd.concat(non_empty, ignore_index=True, sort=False)
            if non_empty
            else _empty_opportunity_frame()
        )
    if signal_type == BIG_BULL_SIGNAL_TYPE:
        return scan_ma5_ma10_big_bull(frame, big_bull_cfg or BigBullConfig())
    if signal_type == "volume_spike_up":
        return scan_volume_spike_up(frame, cfg)
    if signal_type == "bull_pullback_continuation":
        return scan_bull_pullback_continuation(frame, cfg)
    if signal_type == "breakout_pullback_continuation":
        return scan_breakout_pullback_continuation(frame, cfg)
    raise ValueError(f"unsupported signal_type: {signal_type}")


def run_pipeline(
    *,
    start: str | None = None,
    end: str | None = None,
    latest_days: int | None = None,
    today: str | None = None,
    skip_download: bool = False,
    merge_existing: bool = False,
    universe: str = "sample",
    symbols: list[dict[str, str]] | None = None,
    data_root: Path | None = None,
    report_root: Path | None = None,
    daily_opportunity_root: Path | None = None,
    volume_spike_daily_root: Path | None = None,
    breakout_daily_root: Path | None = None,
    big_bull_daily_root: Path | None = None,
    analysis_root: Path | None = None,
    run_id: str | None = None,
    cfg: BullPullbackConfig | None = None,
    big_bull_cfg: BigBullConfig | None = None,
    latest_daily_basic: pd.DataFrame | None = None,
    signal_type: str = "all",
) -> dict[str, Any]:
    """Run the sample pipeline end to end."""
    data_root = Path(data_root) if data_root is not None else _default_data_root()
    report_root = Path(report_root) if report_root is not None else _default_report_root()
    daily_opportunity_root = Path(daily_opportunity_root) if daily_opportunity_root is not None else _default_daily_opportunity_root(report_root)
    volume_spike_daily_root = Path(volume_spike_daily_root) if volume_spike_daily_root is not None else _default_volume_spike_daily_root()
    breakout_daily_root = Path(breakout_daily_root) if breakout_daily_root is not None else _default_breakout_daily_root()
    big_bull_daily_root = Path(big_bull_daily_root) if big_bull_daily_root is not None else _default_big_bull_daily_root()
    analysis_root = Path(analysis_root) if analysis_root is not None else _default_analysis_root()
    run_id = run_id or _run_id()
    cfg = cfg or BullPullbackConfig()
    big_bull_cfg = big_bull_cfg or BigBullConfig()

    downloader: StockDownloader | None = None
    if latest_days is not None:
        downloader = StockDownloader(data_root=data_root)
        start, end = resolve_latest_trade_date_range(downloader, latest_days=latest_days, today=today)
    if start is None or end is None:
        raise ValueError("start/end are required unless --latest-days is provided")

    if symbols is None:
        if universe == "all":
            downloader = downloader or StockDownloader(data_root=data_root)
            try:
                symbols = downloader.list_all_a_share_symbols()
            except Exception as exc:  # noqa: BLE001
                if not skip_download:
                    raise
                logger.warning("falling back to local symbols because stock_basic failed: %s", exc)
                symbols = discover_local_symbols(data_root)
        else:
            symbols = get_default_sample_universe()
    symbols = filter_non_st_symbols(symbols)

    if not skip_download:
        downloader = downloader or StockDownloader(data_root=data_root)
        merge_existing_download = latest_days is not None or merge_existing
        if universe == "all":
            downloader.fetch_all_a_share_days(start=start, end=end, symbols=symbols, merge_existing=merge_existing_download)
        else:
            downloader.fetch_many(symbols, start, end, merge_existing=merge_existing_download)

    if latest_daily_basic is None and downloader is not None:
        latest_daily_basic = fetch_latest_daily_basic(downloader, start, end)

    results: list[pd.DataFrame] = []
    market_returns_by_date: dict[str, list[float]] = {}
    limit_counts_by_symbol: dict[str, tuple[int, int]] = {}
    for item in symbols:
        frame = load_local_day_frame(data_root, str(item["ts_code"]))
        if frame.empty:
            logger.warning("missing local day csv for %s", item["ts_code"])
            continue
        ts_code = str(item["ts_code"]).upper()
        limit_counts_by_symbol[ts_code] = calculate_two_year_limit_counts(frame, end)
        if signal_type in {"all", BIG_BULL_SIGNAL_TYPE}:
            for opportunity_date, return_60d in extract_return_60d_by_opportunity_date(
                frame,
                start=start,
                end=end,
            ):
                market_returns_by_date.setdefault(opportunity_date, []).append(return_60d)
        scanned = scan_frame_for_signal_type(
            frame,
            cfg,
            signal_type,
            big_bull_cfg=big_bull_cfg,
        )
        if not scanned.empty:
            results.append(scanned)

    merged = merge_opportunity_rows(results)
    merged = filter_opportunities_by_date_range(merged, start=start, end=end)
    merged = score_and_filter_big_bull_opportunities(
        merged,
        big_bull_cfg,
        market_returns_by_date=(
            market_returns_by_date
            if signal_type in {"all", BIG_BULL_SIGNAL_TYPE}
            else None
        ),
    )
    merged = enrich_opportunities(
        merged,
        symbols=symbols,
        limit_counts_by_symbol=limit_counts_by_symbol,
        latest_daily_basic=latest_daily_basic,
    )
    merged = filter_non_st_opportunities(merged)
    opportunity_csv = write_opportunity_csv(merged, report_root, run_id)
    daily_opportunity_dir = write_daily_opportunity_dirs(
        merged,
        daily_opportunity_root,
        run_id,
        data_root=data_root,
        chart_end_date=end,
    )
    volume_spike_daily_opportunity_dir = write_volume_spike_up_daily_dirs(
        merged,
        volume_spike_daily_root,
        run_id,
        data_root=data_root,
        chart_end_date=end,
    )
    breakout_daily_opportunity_dir = write_breakout_pullback_daily_dirs(
        merged,
        breakout_daily_root,
        run_id,
        data_root=data_root,
        chart_end_date=end,
    )
    if signal_type == BIG_BULL_SIGNAL_TYPE:
        big_bull_daily_opportunity_dir = clone_daily_opportunity_dirs(
            daily_opportunity_dir,
            big_bull_daily_root,
            run_id,
        )
    else:
        big_bull_daily_opportunity_dir = write_big_bull_daily_dirs(
            merged,
            big_bull_daily_root,
            run_id,
            data_root=data_root,
            chart_end_date=end,
        )
    analysis_dir = analysis_root / run_id
    render_summary = render_all(
        opportunity_csv=opportunity_csv,
        output_dir=analysis_dir,
        data_root=data_root,
        bars_end_date=end,
    )
    return {
        "run_id": run_id,
        "start": start,
        "end": end,
        "latest_days": latest_days,
        "symbol_count": len(symbols),
        "opportunity_count": len(merged),
        "opportunity_csv": str(opportunity_csv),
        "daily_opportunity_dir": str(daily_opportunity_dir),
        "volume_spike_daily_opportunity_dir": str(volume_spike_daily_opportunity_dir),
        "breakout_daily_opportunity_dir": str(breakout_daily_opportunity_dir),
        "big_bull_daily_opportunity_dir": str(big_bull_daily_opportunity_dir),
        "analysis_dir": str(analysis_dir),
        "render_summary": render_summary,
    }


def build_arg_parser() -> argparse.ArgumentParser:
    """Build CLI parser."""
    parser = argparse.ArgumentParser(description="Run the stock signal pipeline.")
    parser.add_argument("--start", default=None, help="download/scanning start date, e.g. 2024-01-01")
    parser.add_argument("--end", default=None, help="download/scanning end date, e.g. 2026-07-05")
    parser.add_argument("--latest-days", type=int, default=None, help="derive start/end from the latest N open trading days")
    parser.add_argument("--today", default=None, help="override today's date for latest-days, e.g. 2026-07-10")
    parser.add_argument("--skip-download", action="store_true", help="reuse existing local day csv files")
    parser.add_argument("--merge-existing", action="store_true", help="merge downloaded day bars with existing local csv files")
    parser.add_argument("--universe", choices=["sample", "all"], default="sample", help="sample20 or all Shanghai/Shenzhen A-shares")
    parser.add_argument(
        "--signal-type",
        choices=[
            "all",
            "bull_pullback_continuation",
            "breakout_pullback_continuation",
            "volume_spike_up",
            BIG_BULL_SIGNAL_TYPE,
        ],
        default="all",
        help="scan all signals or a single signal type",
    )
    parser.add_argument("--run-id", default=None, help="override output run id")
    return parser


def main() -> None:
    """CLI entrypoint."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_arg_parser().parse_args()
    result = run_pipeline(
        start=args.start,
        end=args.end,
        latest_days=args.latest_days,
        today=args.today,
        skip_download=bool(args.skip_download),
        merge_existing=bool(args.merge_existing),
        universe=str(args.universe),
        run_id=args.run_id,
        signal_type=str(args.signal_type),
    )
    print(result)


if __name__ == "__main__":
    main()
