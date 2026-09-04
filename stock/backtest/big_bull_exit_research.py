"""Research exits for MA5/MA10 big-bull opportunities."""
from __future__ import annotations

import argparse
import json
import logging
import shutil
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pandas as pd

from stock.analysis import render_symbol_bull_pullback_charts as stock_charts
from stock.data_code.stock_downloader import is_st_stock_name
from stock.strategy.big_bull_mode import BIG_BULL_SIGNAL_TYPE


logger = logging.getLogger(__name__)

EXIT_RESULT_COLUMNS: list[str] = [
    "entry_date",
    "entry_price",
    "exit_date",
    "exit_price",
    "exit_rule",
    "holding_days",
    "return_pct",
    "mfe_pct",
    "mae_pct",
    "future_mfe_pct",
    "capture_ratio",
]


@dataclass(frozen=True)
class BigBullExitConfig:
    """Exit thresholds designed to retain the strongest part of a bull run."""

    initial_stop_pct: float = 0.10
    trend_profit_activate_pct: float = 0.20
    ma20_break_days: int = 2
    atr_window: int = 20
    chandelier_atr_multiple: float = 3.0
    max_holding_days: int = 240


def prepare_exit_bars(frame: pd.DataFrame, cfg: BigBullExitConfig | None = None) -> pd.DataFrame:
    """Normalize daily bars and add MA/ATR columns used by exit simulation."""
    cfg = cfg or BigBullExitConfig()
    required = {"datetime", "high", "low", "close"}
    if frame.empty or not required.issubset(frame.columns):
        return pd.DataFrame()

    bars = frame.copy()
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        if column not in bars.columns:
            bars[column] = pd.NA
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars["volume"] = bars["volume"].fillna(0.0)
    bars = (
        bars.dropna(subset=["datetime", "high", "low", "close"])
        .sort_values("datetime")
        .drop_duplicates("datetime", keep="last")
        .reset_index(drop=True)
    )
    if bars.empty:
        return bars

    close = pd.to_numeric(bars["close"], errors="coerce")
    high = pd.to_numeric(bars["high"], errors="coerce")
    low = pd.to_numeric(bars["low"], errors="coerce")
    bars["ma5"] = close.rolling(5).mean()
    bars["ma10"] = close.rolling(10).mean()
    bars["ma20"] = close.rolling(20, min_periods=5).mean()
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    bars["atr20"] = true_range.rolling(cfg.atr_window, min_periods=5).mean()
    return bars


def _empty_exit_result(entry_date: str, exit_rule: str) -> dict[str, Any]:
    return {
        "entry_date": entry_date,
        "entry_price": pd.NA,
        "exit_date": "",
        "exit_price": pd.NA,
        "exit_rule": exit_rule,
        "holding_days": 0,
        "return_pct": pd.NA,
        "mfe_pct": pd.NA,
        "mae_pct": pd.NA,
        "future_mfe_pct": pd.NA,
        "capture_ratio": pd.NA,
    }


def simulate_big_bull_exit(
    frame: pd.DataFrame,
    *,
    entry_date: str,
    cfg: BigBullExitConfig | None = None,
) -> dict[str, Any]:
    """Simulate one research exit without using bars before they are available."""
    cfg = cfg or BigBullExitConfig()
    bars = prepare_exit_bars(frame, cfg)
    if bars.empty:
        return _empty_exit_result(entry_date, "missing_bars")

    parsed_entry = pd.to_datetime(entry_date, errors="coerce")
    if pd.isna(parsed_entry):
        return _empty_exit_result(entry_date, "invalid_entry_date")
    entry_timestamp = pd.Timestamp(parsed_entry).normalize()
    entry_candidates = bars[bars["datetime"].dt.normalize() == entry_timestamp]
    if entry_candidates.empty:
        exit_rule = (
            "entry_after_data"
            if entry_timestamp > bars["datetime"].dt.normalize().max()
            else "missing_entry_bar"
        )
        return _empty_exit_result(entry_date, exit_rule)

    entry_index = int(entry_candidates.index[0])
    entry_row = bars.iloc[entry_index]
    entry_price = (
        float(entry_row["open"])
        if pd.notna(entry_row["open"])
        else float(entry_row["close"])
    )
    if entry_price <= 0:
        return _empty_exit_result(entry_date, "invalid_entry_price")

    setup_window = bars.iloc[max(0, entry_index - 5) : entry_index]
    setup_stop = (
        float(setup_window["low"].min()) * 0.97
        if not setup_window.empty
        else float("-inf")
    )
    initial_stop = max(setup_stop, entry_price * (1.0 - cfg.initial_stop_pct))
    highest_high = float(entry_row["high"])
    trend_mode = False
    below_ma20_days = 0

    last_holding_index = min(len(bars) - 1, entry_index + cfg.max_holding_days)
    exit_index = last_holding_index
    exit_rule = (
        "max_holding_days"
        if last_holding_index == entry_index + cfg.max_holding_days
        else "end_of_data"
    )
    exit_fill_price: float | None = None

    if float(entry_row["low"]) <= initial_stop:
        exit_index = entry_index
        exit_rule = "initial_stop"
        exit_fill_price = min(entry_price, initial_stop)
    else:
        for index in range(entry_index + 1, last_holding_index + 1):
            row = bars.iloc[index]
            close = float(row["close"])
            if not trend_mode and float(row["low"]) <= initial_stop:
                exit_index = index
                exit_rule = "initial_stop"
                exit_fill_price = (
                    min(float(row["open"]), initial_stop)
                    if pd.notna(row["open"])
                    else initial_stop
                )
                break
            highest_high = max(highest_high, float(row["high"]))
            profit_pct = close / entry_price - 1.0

            if not trend_mode and profit_pct >= cfg.trend_profit_activate_pct:
                trend_mode = True

            if not trend_mode:
                continue

            ma20 = float(row["ma20"]) if pd.notna(row["ma20"]) else close
            below_ma20_days = below_ma20_days + 1 if close < ma20 else 0
            ma5 = float(row["ma5"]) if pd.notna(row["ma5"]) else close
            ma10 = float(row["ma10"]) if pd.notna(row["ma10"]) else close
            atr20 = (
                float(row["atr20"])
                if pd.notna(row["atr20"]) and float(row["atr20"]) > 0
                else 0.0
            )
            chandelier = highest_high - cfg.chandelier_atr_multiple * atr20
            if below_ma20_days >= cfg.ma20_break_days and ma5 < ma10:
                exit_index = index
                exit_rule = "big_bull_ma20_break"
                break
            chandelier_active = highest_high / entry_price - 1.0 >= 0.30
            if chandelier_active and atr20 > 0 and close < chandelier:
                exit_index = index
                exit_rule = "big_bull_chandelier"
                break

    exit_row = bars.iloc[exit_index]
    exit_price = exit_fill_price if exit_fill_price is not None else float(exit_row["close"])
    holding_days = int(exit_index - entry_index)
    return_pct = exit_price / entry_price - 1.0
    holding_window = bars.iloc[entry_index : exit_index + 1]
    mfe_pct = float(holding_window["high"].max()) / entry_price - 1.0
    mae_pct = float(holding_window["low"].min()) / entry_price - 1.0
    future_window = bars.iloc[entry_index : last_holding_index + 1]
    future_mfe_pct = float(future_window["high"].max()) / entry_price - 1.0
    capture_ratio = return_pct / future_mfe_pct if future_mfe_pct > 0 else 0.0
    return {
        "entry_date": pd.Timestamp(entry_row["datetime"]).strftime("%Y-%m-%d"),
        "entry_price": entry_price,
        "exit_date": pd.Timestamp(exit_row["datetime"]).strftime("%Y-%m-%d"),
        "exit_price": exit_price,
        "exit_rule": exit_rule,
        "holding_days": holding_days,
        "return_pct": return_pct,
        "mfe_pct": mfe_pct,
        "mae_pct": mae_pct,
        "future_mfe_pct": future_mfe_pct,
        "capture_ratio": capture_ratio,
    }


def load_symbol_bars(data_root: Path, symbol: str) -> pd.DataFrame:
    """Load raw local day bars for one stock symbol."""
    path = Path(data_root) / "day" / f"{symbol.replace('.', '_')}.csv"
    if not path.exists():
        return pd.DataFrame()
    return pd.read_csv(path)


def _render_exit_trade_charts(
    rows_by_symbol: dict[str, list[dict[str, Any]]],
    *,
    data_root: Path,
    analysis_dir: Path,
) -> dict[str, Any]:
    charts_dir = Path(analysis_dir) / "charts"
    if charts_dir.exists():
        shutil.rmtree(charts_dir)
    charts_dir.mkdir(parents=True, exist_ok=True)

    index_rows: list[dict[str, Any]] = []
    rendered_images = 0
    missing_bars = 0
    for rank, symbol in enumerate(sorted(rows_by_symbol), start=1):
        bars = stock_charts.load_symbol_bars(Path(data_root), symbol)
        if bars.empty:
            missing_bars += 1
            index_rows.append(
                {
                    "rank": rank,
                    "symbol": symbol,
                    "trades": len(rows_by_symbol[symbol]),
                    "image_path": "",
                    "render_status": "missing_bars",
                }
            )
            continue
        image = stock_charts.render_symbol_card(
            symbol,
            rows_by_symbol[symbol],
            bars,
            include_exit_markers=True,
        )
        filename = f"{rank:04d}_{symbol.replace('.', '_')}_big_bull_exit.png"
        image.save(charts_dir / filename)
        rendered_images += 1
        index_rows.append(
            {
                "rank": rank,
                "symbol": symbol,
                "trades": len(rows_by_symbol[symbol]),
                "image_path": str(Path("charts") / filename),
                "render_status": "rendered",
            }
        )

    Path(analysis_dir).mkdir(parents=True, exist_ok=True)
    pd.DataFrame(index_rows).to_csv(Path(analysis_dir) / "index.csv", index=False)
    summary = {
        "analysis_dir": str(analysis_dir),
        "symbols": len(rows_by_symbol),
        "rendered_images": rendered_images,
        "missing_bars": missing_bars,
    }
    (Path(analysis_dir) / "render_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def _mean_or_zero(frame: pd.DataFrame, column: str) -> float:
    if frame.empty or column not in frame.columns:
        return 0.0
    value = pd.to_numeric(frame[column], errors="coerce").mean()
    return float(value) if pd.notna(value) else 0.0


def run_exit_research(
    *,
    opportunity_csv: Path,
    data_root: Path,
    output_dir: Path,
    analysis_root: Path,
    run_id: str,
    cfg: BigBullExitConfig | None = None,
) -> dict[str, Any]:
    """Run exit simulation for all non-ST big-bull opportunities and render charts."""
    cfg = cfg or BigBullExitConfig()
    opportunities = pd.read_csv(opportunity_csv)
    if "signal_type" not in opportunities.columns:
        opportunities = opportunities.iloc[0:0].copy()
    else:
        opportunities = opportunities[
            opportunities["signal_type"].astype(str) == BIG_BULL_SIGNAL_TYPE
        ].copy()
    if "name" not in opportunities.columns:
        opportunities["name"] = ""
    names = opportunities["name"].fillna("").astype(str).str.strip()
    opportunities = opportunities[
        names.ne("") & ~names.map(is_st_stock_name)
    ].reset_index(drop=True)

    bars_by_symbol: dict[str, pd.DataFrame] = {}
    rows: list[dict[str, Any]] = []
    chart_rows_by_symbol: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for opportunity in opportunities.to_dict("records"):
        symbol = str(opportunity.get("symbol") or "")
        if not symbol:
            continue
        if symbol not in bars_by_symbol:
            bars_by_symbol[symbol] = load_symbol_bars(Path(data_root), symbol)
        result = simulate_big_bull_exit(
            bars_by_symbol[symbol],
            entry_date=str(opportunity.get("opportunity_date") or ""),
            cfg=cfg,
        )
        trade = dict(opportunity)
        trade.update(result)
        rows.append(trade)
        chart_rows_by_symbol[symbol].append(trade)

    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    trades_csv = output_dir / f"{run_id}_big_bull_exit_trades.csv"
    trades = pd.DataFrame(rows)
    if trades.empty:
        trades = pd.DataFrame(
            columns=[
                "symbol",
                "exchange",
                "name",
                "signal_type",
                "signal_datetime",
                "opportunity_date",
            ]
            + EXIT_RESULT_COLUMNS
        )
    trades.to_csv(trades_csv, index=False, encoding="utf-8-sig")

    analysis_dir = Path(analysis_root) / run_id
    render_summary = _render_exit_trade_charts(
        dict(chart_rows_by_symbol),
        data_root=Path(data_root),
        analysis_dir=analysis_dir,
    )
    summary_path = output_dir / f"{run_id}_big_bull_exit_summary.json"
    summary = {
        "run_id": run_id,
        "rows": len(trades),
        "symbols": int(trades["symbol"].nunique()) if not trades.empty else 0,
        "trades_csv": str(trades_csv),
        "summary_path": str(summary_path),
        "analysis_dir": str(analysis_dir),
        "rendered_images": render_summary["rendered_images"],
        "missing_bars": render_summary["missing_bars"],
        "avg_return_pct": _mean_or_zero(trades, "return_pct"),
        "avg_capture_ratio": _mean_or_zero(trades, "capture_ratio"),
    }
    summary_path.write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    logger.info(
        "big-bull exit research complete: rows=%s symbols=%s charts=%s",
        summary["rows"],
        summary["symbols"],
        summary["rendered_images"],
    )
    return summary


def build_arg_parser() -> argparse.ArgumentParser:
    """Build the big-bull exit research CLI parser."""
    stock_root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Run MA5/MA10 big-bull exit research.")
    parser.add_argument("--opportunity-csv", required=True)
    parser.add_argument("--data-root", default=str(stock_root / "data" / "origin"))
    parser.add_argument("--output-dir", default=str(stock_root / "report" / "exit_research"))
    parser.add_argument("--analysis-root", default=str(stock_root / "analysis"))
    parser.add_argument("--run-id", required=True)
    return parser


def main() -> None:
    """Run exit research from command-line arguments."""
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    args = build_arg_parser().parse_args()
    result = run_exit_research(
        opportunity_csv=Path(args.opportunity_csv),
        data_root=Path(args.data_root),
        output_dir=Path(args.output_dir),
        analysis_root=Path(args.analysis_root),
        run_id=str(args.run_id),
    )
    print(result)


if __name__ == "__main__":
    main()


__all__ = [
    "BigBullExitConfig",
    "build_arg_parser",
    "load_symbol_bars",
    "prepare_exit_bars",
    "run_exit_research",
    "simulate_big_bull_exit",
]
