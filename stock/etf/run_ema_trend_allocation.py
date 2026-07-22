"""Optimize and audit the three-level allocation strategy from local CSV data."""

from __future__ import annotations

import argparse
import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from math import isfinite, sqrt
from pathlib import Path
from sys import float_info
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from .ema5_open_strategy import Ema5OpenConfig, run_ema5_open_backtest
from .ema_trend_allocation_strategy import (
    TrendAllocationConfig,
    calculate_period_metrics,
    run_trend_allocation_backtest,
)
from .optimize_ema_trend_allocation import (
    OOS_END,
    OOS_START,
    SELECTION_CUTOFF,
    TRAIN_END,
    TRAIN_START,
    VALIDATION_END,
    VALIDATION_START,
    OptimizationResult,
    evaluate_release,
    optimize_on_train_validation,
)
from .render_trade_charts import make_chart_filename, render_all_trade_charts

ETF_ROOT = Path(__file__).resolve().parent
DEFAULT_SOURCE_ROOT = ETF_ROOT / "data/20260717_2018_20260717_point_in_time_live"
DEFAULT_DAILY_CSV = DEFAULT_SOURCE_ROOT / "etfs_lifecycle_clean.csv"
DEFAULT_METADATA_CSV = DEFAULT_SOURCE_ROOT / "metadata.csv"
DEFAULT_BENCHMARK_CSV = DEFAULT_SOURCE_ROOT / "benchmark.csv"
DEFAULT_OUTPUT_DIR = ETF_ROOT / "output/20260719_chuangyeban_optimized"
BENCHMARK_SYMBOL = "000300.SH"
PERIODS = {
    "training": (TRAIN_START, TRAIN_END),
    "validation": (VALIDATION_START, VALIDATION_END),
    "oos": (OOS_START, OOS_END),
}
OWNED_OUTPUT_FILES = (
    "candidate_results.csv",
    "selected_parameters.json",
    "summary.json",
    "signals.csv",
    "trades.csv",
    "positions.csv",
    "equity_curve.csv",
)
OWNED_OUTPUT_ENTRIES = (*OWNED_OUTPUT_FILES, "charts")


def select_ema_trend_allocation(
    selection_bars: pd.DataFrame,
    base_config: TrendAllocationConfig,
) -> OptimizationResult:
    """Select parameters using only the caller-provided pre-OOS bars."""
    return optimize_on_train_validation(selection_bars, base_config)


def _annualize_return(total_return: float, days: int) -> float:
    if total_return <= -1:
        return -1.0
    return_years = max((days - 1) / 252, 1 / 252)
    try:
        return (1 + total_return) ** (1 / return_years) - 1
    except OverflowError:
        return float_info.max


def calculate_buy_hold_metrics(
    bars: pd.DataFrame,
    start: object,
    end: object,
) -> dict[str, Any]:
    """Calculate close-to-close buy-and-hold metrics without trading costs."""
    required = {"datetime", "close"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"buy-and-hold data missing columns: {sorted(missing)}")
    frame = bars.loc[:, ["datetime", "close"]].copy()
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    if frame["datetime"].isna().any():
        raise ValueError("buy-and-hold data contains missing dates")
    frame["datetime"] = frame["datetime"].dt.normalize()
    if frame["datetime"].duplicated().any():
        raise ValueError("buy-and-hold data contains duplicate dates")
    frame["close"] = pd.to_numeric(frame["close"], errors="coerce")
    if (
        frame["close"].isna().any()
        or not frame["close"].map(isfinite).all()
        or frame["close"].le(0).any()
    ):
        raise ValueError("buy-and-hold prices must be finite and positive")
    frame = frame.sort_values("datetime", ignore_index=True)

    start_date = pd.Timestamp(start).normalize()
    end_date = pd.Timestamp(end).normalize()
    if start_date > end_date:
        raise ValueError("start must be on or before end")
    period = frame.loc[
        frame["datetime"].between(start_date, end_date, inclusive="both")
    ].copy()
    if period.empty:
        raise ValueError("no buy-and-hold rows in requested period")

    returns = period["close"].pct_change(fill_method=None).fillna(0.0)
    drawdown = period["close"] / period["close"].cummax() - 1
    days = len(period)
    total_return = float(period.iloc[-1]["close"] / period.iloc[0]["close"] - 1)
    daily_std = float(returns.std(ddof=0))
    return {
        "start_date": str(pd.Timestamp(period.iloc[0]["datetime"]).date()),
        "end_date": str(pd.Timestamp(period.iloc[-1]["datetime"]).date()),
        "days": days,
        "trading_days": days,
        "total_return": total_return,
        "annual_return": _annualize_return(total_return, days),
        "annual_volatility": daily_std * sqrt(252),
        "sharpe": (
            float(returns.mean()) / daily_std * sqrt(252) if daily_std > 0 else 0.0
        ),
        "max_drawdown": float(drawdown.min()),
        "annual_one_way_turnover": 0.0,
        "trade_count": 0,
    }


def _json_compatible(value: object) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(value))
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not isfinite(number):
            raise ValueError("JSON output contains a non-finite number")
        return number
    if value is pd.NA:
        return None
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _atomic_write_csv(frame: pd.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        frame.to_csv(temporary, index=False)
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)


def _atomic_write_json(payload: Mapping[str, object], path: Path) -> dict[str, Any]:
    compatible = _json_compatible(payload)
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        temporary.write_text(
            json.dumps(
                compatible,
                allow_nan=False,
                ensure_ascii=False,
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
    return compatible


def _guard_output_directory(output_dir: Path, overwrite: bool) -> None:
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"output directory already contains output: {output_dir}")


def _path_exists(path: Path) -> bool:
    return path.exists() or path.is_symlink()


def _remove_path(path: Path) -> None:
    if path.is_symlink() or path.is_file():
        path.unlink()
    elif path.is_dir():
        shutil.rmtree(path)


def _publish_owned_entries(
    staging_dir: Path,
    output_dir: Path,
    staged_entries: Sequence[str],
) -> None:
    names = tuple(staged_entries)
    invalid = set(names) - set(OWNED_OUTPUT_ENTRIES)
    if invalid:
        raise ValueError(f"cannot publish unknown entries: {sorted(invalid)}")
    missing = [name for name in names if not _path_exists(staging_dir / name)]
    if missing:
        raise ValueError(f"staged output missing entries: {missing}")

    output_existed = output_dir.exists()
    output_dir.mkdir(parents=True, exist_ok=True)
    rollback_dir = staging_dir / ".rollback"
    rollback_dir.mkdir()
    backed_up: list[str] = []
    try:
        for name in OWNED_OUTPUT_ENTRIES:
            destination = output_dir / name
            if _path_exists(destination):
                destination.replace(rollback_dir / name)
                backed_up.append(name)
        for name in names:
            (staging_dir / name).replace(output_dir / name)
    except Exception:
        for name in OWNED_OUTPUT_ENTRIES:
            _remove_path(output_dir / name)
        for name in backed_up:
            (rollback_dir / name).replace(output_dir / name)
        if not output_existed and output_dir.exists() and not any(output_dir.iterdir()):
            output_dir.rmdir()
        raise
    finally:
        shutil.rmtree(rollback_dir, ignore_errors=True)


def _load_symbol_rows(frame: pd.DataFrame, symbol: str, source: str) -> pd.DataFrame:
    required = {"symbol", "datetime", "close"}
    missing = required - set(frame.columns)
    if missing:
        raise ValueError(f"{source} missing columns: {sorted(missing)}")
    selected = frame.loc[frame["symbol"].astype(str).eq(symbol)].copy()
    if selected.empty:
        raise ValueError(f"{source} missing symbol: {symbol}")
    selected["datetime"] = pd.to_datetime(selected["datetime"], errors="raise")
    if selected["datetime"].isna().any():
        raise ValueError(f"{source} contains missing dates for {symbol}")
    selected["datetime"] = selected["datetime"].dt.normalize()
    return selected.sort_values("datetime", ignore_index=True)


def _load_display_name(metadata_csv: Path, symbol: str) -> str:
    metadata = pd.read_csv(metadata_csv)
    required = {"symbol", "name", "fund_type"}
    missing = required - set(metadata.columns)
    if missing:
        raise ValueError(f"ETF metadata missing columns: {sorted(missing)}")
    selected = metadata.loc[metadata["symbol"].astype(str).eq(symbol)]
    if len(selected) != 1 or not str(selected.iloc[0]["name"]).strip():
        raise ValueError(f"metadata requires one named row for {symbol}")
    return str(selected.iloc[0]["name"]).strip()


def _full_strategy_metrics(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    result_summary: Mapping[str, object],
) -> dict[str, Any]:
    start = pd.Timestamp(equity_curve.iloc[0]["datetime"])
    end = pd.Timestamp(equity_curve.iloc[-1]["datetime"])
    metrics = calculate_period_metrics(equity_curve, trades, start, end)
    total_return = float(result_summary.get("total_return", metrics["total_return"]))
    metrics["total_return"] = total_return
    metrics["annual_return"] = _annualize_return(total_return, len(equity_curve))
    for key in (
        "max_drawdown",
        "trade_count",
        "initial_capital",
        "initial_equity",
        "final_equity",
        "total_commission",
        "total_slippage_cost",
        "target_weight",
        "is_open",
    ):
        if key in result_summary:
            metrics[key] = result_summary[key]
    return metrics


def _strategy_periods(result: object) -> dict[str, dict[str, Any]]:
    equity_curve = result.equity_curve
    trades = result.trades
    periods = {
        name: calculate_period_metrics(equity_curve, trades, start, end)
        for name, (start, end) in PERIODS.items()
    }
    periods["full"] = _full_strategy_metrics(
        equity_curve,
        trades,
        result.summary,
    )
    return periods


def _buy_hold_periods(bars: pd.DataFrame) -> dict[str, dict[str, Any]]:
    periods = {
        name: calculate_buy_hold_metrics(bars, start, end)
        for name, (start, end) in PERIODS.items()
    }
    periods["full"] = calculate_buy_hold_metrics(
        bars,
        bars.iloc[0]["datetime"],
        bars.iloc[-1]["datetime"],
    )
    return periods


def _render_charts_atomically(
    *,
    daily_csv: Path,
    metadata_csv: Path,
    trades_csv: Path,
    positions_csv: Path,
    output_dir: Path,
    report_start: object,
    report_end: object,
    symbols: Sequence[str],
    expected_filenames: Sequence[str],
) -> dict[str, Any]:
    staging = output_dir / f".charts.{uuid4().hex}.tmp"
    try:
        render_summary = render_all_trade_charts(
            daily_csv=daily_csv,
            metadata_csv=metadata_csv,
            trades_csv=trades_csv,
            positions_csv=positions_csv,
            output_dir=staging,
            report_start=report_start,
            report_end=report_end,
            symbols=symbols,
            overwrite=True,
        )
        completed_images = int(render_summary["rendered_images"]) + int(
            render_summary["existing_images"]
        )
        missing_images = [
            filename
            for filename in expected_filenames
            if not (staging / filename).is_file()
        ]
        if completed_images < len(expected_filenames) or missing_images:
            raise RuntimeError(
                f"chart rendering incomplete for expected files: {missing_images}"
            )
        charts_dir = output_dir / "charts"
        render_summary["output_dir"] = str(charts_dir)
        _atomic_write_json(render_summary, staging / "render_summary.json")
        if charts_dir.exists():
            raise FileExistsError(f"chart output already exists: {charts_dir}")
        staging.replace(charts_dir)
        return render_summary
    finally:
        shutil.rmtree(staging, ignore_errors=True)


def _rejected_release(reason: str) -> dict[str, object]:
    return {
        "status": "rejected",
        "checks": {},
        "failed_checks": [reason],
    }


def _run_and_write_staged(
    *,
    daily_csv: Path,
    metadata_csv: Path,
    benchmark_csv: Path,
    output_dir: Path,
    staging_dir: Path,
    config: TrendAllocationConfig,
) -> dict[str, Any]:
    """Build a complete run in staging and publish only finished outputs."""
    cfg = config
    display_name = _load_display_name(metadata_csv, cfg.symbol)
    daily = pd.read_csv(daily_csv)
    symbol_bars = _load_symbol_rows(daily, cfg.symbol, "ETF daily data")
    benchmark = _load_symbol_rows(
        pd.read_csv(benchmark_csv),
        BENCHMARK_SYMBOL,
        "benchmark data",
    )
    actual_start = str(pd.Timestamp(symbol_bars.iloc[0]["datetime"]).date())
    actual_end = str(pd.Timestamp(symbol_bars.iloc[-1]["datetime"]).date())

    baseline_config = Ema5OpenConfig(
        symbol=cfg.symbol,
        initial_capital=cfg.initial_capital,
        lot_size=cfg.lot_size,
        commission_rate=cfg.commission_rate,
        min_commission=cfg.min_commission,
        slippage_rate=cfg.slippage_rate,
    )
    current_result = run_ema5_open_backtest(symbol_bars, baseline_config)
    current_periods = _strategy_periods(current_result)
    etf_buy_hold = _buy_hold_periods(symbol_bars)
    csi300_buy_hold = _buy_hold_periods(benchmark)

    cutoff = pd.Timestamp(SELECTION_CUTOFF)
    selection_bars = symbol_bars.loc[symbol_bars["datetime"].le(cutoff)].copy()
    if selection_bars.empty:
        raise ValueError("ETF daily data has no rows on or before selection cutoff")
    selection_end = str(pd.Timestamp(selection_bars.iloc[-1]["datetime"]).date())
    optimization = select_ema_trend_allocation(selection_bars, cfg)
    candidates = optimization.candidate_results
    _atomic_write_csv(candidates, staging_dir / "candidate_results.csv")

    common_summary: dict[str, object] = {
        "symbol": cfg.symbol,
        "display_name": display_name,
        "benchmark_symbol": BENCHMARK_SYMBOL,
        "selection_cutoff": SELECTION_CUTOFF,
        "selection_data_end_date": selection_end,
        "candidate_count": int(len(candidates)),
        "actual_start_date": actual_start,
        "actual_end_date": actual_end,
        "daily_csv": str(daily_csv),
        "metadata_csv": str(metadata_csv),
        "benchmark_csv": str(benchmark_csv),
        "output_dir": str(output_dir),
        "current_strategy": current_periods,
        "etf_buy_hold": etf_buy_hold,
        "csi300_buy_hold": csi300_buy_hold,
    }
    if optimization.selected_config is None:
        release = _rejected_release("no_feasible_candidate")
        selected_payload = {
            "status": "rejected",
            "config": None,
            "selection_cutoff": SELECTION_CUTOFF,
            "selection_data_end_date": selection_end,
            "release": release,
        }
        summary_payload = {
            **common_summary,
            "candidate": None,
            "release": release,
        }
        _atomic_write_json(
            selected_payload,
            staging_dir / "selected_parameters.json",
        )
        _atomic_write_json(summary_payload, staging_dir / "summary.json")
        _publish_owned_entries(
            staging_dir,
            output_dir,
            ("candidate_results.csv", "selected_parameters.json", "summary.json"),
        )
        raise RuntimeError("no feasible candidate in training and validation periods")

    selected_config = optimization.selected_config
    candidate_result = run_trend_allocation_backtest(symbol_bars, selected_config)
    candidate_periods = _strategy_periods(candidate_result)
    release = evaluate_release(
        candidate_periods["full"],
        candidate_periods["oos"],
        current_periods["full"],
        current_periods["oos"],
    )
    selected_payload = {
        "status": release["status"],
        "config": asdict(selected_config),
        "selection_cutoff": SELECTION_CUTOFF,
        "selection_data_end_date": selection_end,
        "selection_order": [
            "validation_total_return_desc",
            "validation_max_drawdown_desc",
            "validation_annual_one_way_turnover_asc",
            "slow_confirmation_slope_asc",
        ],
        "release": release,
    }
    summary_payload = {
        **common_summary,
        "candidate": candidate_periods,
        "release": release,
    }

    _atomic_write_csv(candidate_result.signals, staging_dir / "signals.csv")
    _atomic_write_csv(candidate_result.trades, staging_dir / "trades.csv")
    _atomic_write_csv(candidate_result.positions, staging_dir / "positions.csv")
    _atomic_write_csv(candidate_result.equity_curve, staging_dir / "equity_curve.csv")
    _atomic_write_json(selected_payload, staging_dir / "selected_parameters.json")
    summary = _atomic_write_json(summary_payload, staging_dir / "summary.json")
    render_summary = _render_charts_atomically(
        daily_csv=daily_csv,
        metadata_csv=metadata_csv,
        trades_csv=staging_dir / "trades.csv",
        positions_csv=staging_dir / "positions.csv",
        output_dir=staging_dir,
        report_start=candidate_result.equity_curve.iloc[0]["datetime"],
        report_end=candidate_result.equity_curve.iloc[-1]["datetime"],
        symbols=[cfg.symbol],
        expected_filenames=[make_chart_filename(1, cfg.symbol, display_name)],
    )
    render_summary.update(
        {
            "trades_csv": str(output_dir / "trades.csv"),
            "positions_csv": str(output_dir / "positions.csv"),
            "output_dir": str(output_dir / "charts"),
        }
    )
    _atomic_write_json(
        render_summary,
        staging_dir / "charts/render_summary.json",
    )
    _publish_owned_entries(
        staging_dir,
        output_dir,
        OWNED_OUTPUT_ENTRIES,
    )
    return summary


def run_and_write(
    *,
    daily_csv: Path,
    metadata_csv: Path,
    benchmark_csv: Path,
    output_dir: Path,
    config: TrendAllocationConfig | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Select, rerun, compare, and transactionally publish local outputs."""
    cfg = config or TrendAllocationConfig()
    _guard_output_directory(output_dir, overwrite)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_dir.parent / f".{output_dir.name}.{uuid4().hex}.tmp"
    staging_dir.mkdir()
    try:
        return _run_and_write_staged(
            daily_csv=daily_csv,
            metadata_csv=metadata_csv,
            benchmark_csv=benchmark_csv,
            output_dir=output_dir,
            staging_dir=staging_dir,
            config=cfg,
        )
    finally:
        shutil.rmtree(staging_dir, ignore_errors=True)


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone local optimization CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-csv", type=Path, default=DEFAULT_DAILY_CSV)
    parser.add_argument("--metadata-csv", type=Path, default=DEFAULT_METADATA_CSV)
    parser.add_argument("--benchmark-csv", type=Path, default=DEFAULT_BENCHMARK_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--symbol", default="159915.SZ")
    parser.add_argument("--initial-capital", type=float, default=1_000_000.0)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Run the local optimization and print its strict JSON summary."""
    args = build_parser().parse_args(argv)
    summary = run_and_write(
        daily_csv=args.daily_csv,
        metadata_csv=args.metadata_csv,
        benchmark_csv=args.benchmark_csv,
        output_dir=args.output_dir,
        config=TrendAllocationConfig(
            symbol=args.symbol,
            initial_capital=args.initial_capital,
        ),
        overwrite=args.overwrite,
    )
    print(
        json.dumps(
            summary,
            allow_nan=False,
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
