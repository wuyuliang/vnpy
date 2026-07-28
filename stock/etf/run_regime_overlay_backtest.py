"""Run the causal regime overlay on the accepted EMA allocation strategy."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from dataclasses import asdict
from math import isfinite
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
from PIL import Image

from .ema_trend_allocation_strategy import (
    TRADE_COLUMNS,
    TrendAllocationConfig,
)
from .regime_data import (
    build_causal_bars,
    download_regime_inputs,
    normalize_trade_calendar,
)
from .regime_overlay_strategy import (
    build_buy_hold_equity,
    calculate_continuous_metrics,
    calculate_period_table,
    calendar_periods,
    run_regime_overlay_backtest,
)
from .regime_overlay_charts import render_regime_comparison_charts
from .regime_rules import RegimeConfig, predict_regime
from .render_trade_charts import make_chart_filename

ETF_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = ETF_ROOT / "output/20260727_chuangyeban_regime_overlay"
OUTPUT_FILES = (
    "signals.csv",
    "trades.csv",
    "positions.csv",
    "equity_curve.csv",
    "comparison.csv",
    "annual_metrics.csv",
    "semiannual_metrics.csv",
    "summary.json",
    "source_audit.json",
)
CHART_AUDIT_FILES = ("index.csv", "render_summary.json")
OUTPUT_ENTRIES = (*OUTPUT_FILES, "charts")


def _display_name(symbol: str) -> str:
    return "易方达创业板ETF" if symbol == "159915.SZ" else symbol


def _chart_files(symbol: str, display_name: str) -> tuple[str, ...]:
    return (
        make_chart_filename(1, symbol, f"{display_name}_状态覆盖"),
        make_chart_filename(2, symbol, f"{display_name}_EMA基线"),
        *CHART_AUDIT_FILES,
    )


def _output_artifact_paths(chart_files: Sequence[str]) -> tuple[str, ...]:
    return (
        *OUTPUT_FILES,
        *(f"charts/{filename}" for filename in chart_files),
    )


def _json_compatible(value: object) -> Any:
    if value is None or isinstance(value, (str, bool, int)):
        return value
    if value is pd.NA or value is pd.NaT:
        return None
    if isinstance(value, Path):
        return str(value)
    if isinstance(value, (pd.Timestamp, np.datetime64)):
        return str(pd.Timestamp(value).date())
    if isinstance(value, np.bool_):
        return bool(value)
    if isinstance(value, np.integer):
        return int(value)
    if isinstance(value, (float, np.floating)):
        number = float(value)
        if not isfinite(number):
            raise ValueError("JSON output contains a non-finite number")
        return number
    if isinstance(value, Mapping):
        return {str(key): _json_compatible(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_json_compatible(item) for item in value]
    raise TypeError(f"unsupported JSON value: {type(value).__name__}")


def _write_json(payload: Mapping[str, object], path: Path) -> dict[str, Any]:
    compatible = _json_compatible(payload)
    path.write_text(
        json.dumps(
            compatible,
            ensure_ascii=False,
            allow_nan=False,
            indent=2,
            sort_keys=True,
        ),
        encoding="utf-8",
    )
    return compatible


def _guard_output_directory(output_dir: Path, overwrite: bool) -> None:
    if output_dir.exists() and any(output_dir.iterdir()) and not overwrite:
        raise FileExistsError(f"output directory already contains files: {output_dir}")


def _combined_runtime_error(
    context: str,
    original_error: Exception,
    recovery_error: Exception,
) -> RuntimeError:
    error = RuntimeError(
        f"{context}; original error "
        f"{type(original_error).__name__}: {original_error}; recovery error "
        f"{type(recovery_error).__name__}: {recovery_error}"
    )
    error.original_error = original_error
    error.recovery_error = recovery_error
    return error


def _publish_staging(staging_dir: Path, output_dir: Path) -> None:
    backup_dir = output_dir.with_name(f".{output_dir.name}.{uuid4().hex}.backup")
    had_output = output_dir.exists()
    if had_output:
        output_dir.replace(backup_dir)
    try:
        staging_dir.replace(output_dir)
    except Exception as publish_error:
        try:
            if output_dir.exists():
                shutil.rmtree(output_dir)
            if had_output:
                backup_dir.replace(output_dir)
        except Exception as restore_error:
            raise _combined_runtime_error(
                "failed to restore previous output after publish failure",
                publish_error,
                restore_error,
            ) from restore_error
        raise
    else:
        if had_output:
            try:
                shutil.rmtree(backup_dir)
            except Exception as cleanup_error:
                try:
                    if output_dir.exists():
                        shutil.rmtree(output_dir)
                    backup_dir.replace(output_dir)
                except Exception as rollback_error:
                    raise _combined_runtime_error(
                        "failed to roll back output after backup cleanup failure",
                        cleanup_error,
                        rollback_error,
                    ) from rollback_error
                raise


def _validate_staged_outputs(
    staging_dir: Path,
    *,
    chart_files: Sequence[str],
    output_artifact_paths: Sequence[str],
) -> None:
    actual_entries = {path.name for path in staging_dir.iterdir()}
    if actual_entries != set(OUTPUT_ENTRIES):
        raise RuntimeError(
            "staged output entries do not match expected entries: "
            f"{sorted(actual_entries)}"
        )
    missing = [
        filename
        for filename in OUTPUT_FILES
        if not (staging_dir / filename).is_file()
        or (staging_dir / filename).stat().st_size == 0
    ]
    if missing:
        raise RuntimeError(f"staged output missing files: {missing}")
    charts_dir = staging_dir / "charts"
    if not charts_dir.is_dir() or not any(charts_dir.iterdir()):
        raise RuntimeError("staged charts directory is missing or empty")
    actual_chart_files = {path.name for path in charts_dir.iterdir()}
    if actual_chart_files != set(chart_files):
        raise RuntimeError(
            "staged chart files do not match expected files: "
            f"{sorted(actual_chart_files)}"
        )
    empty_chart_files = [
        filename
        for filename in chart_files
        if not (charts_dir / filename).is_file()
        or (charts_dir / filename).stat().st_size == 0
    ]
    if empty_chart_files:
        raise RuntimeError(
            f"staged chart files are missing or empty: {empty_chart_files}"
        )

    for filename in ("summary.json", "source_audit.json"):
        with (staging_dir / filename).open(encoding="utf-8") as file:
            payload = json.load(
                file,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-standard JSON constant: {value}")
                ),
            )
        if filename == "summary.json" and payload.get("output_files") != list(
            output_artifact_paths
        ):
            raise RuntimeError("summary output_files do not match staged artifacts")
    for filename in OUTPUT_FILES:
        if filename.endswith(".csv"):
            pd.read_csv(staging_dir / filename)

    image_files = chart_files[:2]
    image_sizes = ((1680, 1120), (1680, 1000))
    for filename, expected_size in zip(image_files, image_sizes, strict=True):
        with Image.open(charts_dir / filename) as image:
            image.load()
            if image.size != expected_size:
                raise RuntimeError(
                    f"staged chart {filename} has size {image.size}, "
                    f"expected {expected_size}"
                )

    index = pd.read_csv(charts_dir / "index.csv")
    expected_strategies = ["regime_overlay", "ema_only"]
    if (
        len(index) != 2
        or "strategy" not in index
        or "image_path" not in index
        or index["strategy"].tolist() != expected_strategies
        or index["image_path"].tolist() != list(image_files)
    ):
        raise RuntimeError("staged chart index does not match expected rows")
    for image_path in index["image_path"]:
        if not (charts_dir / image_path).is_file():
            raise RuntimeError(f"staged chart index image does not exist: {image_path}")

    with (charts_dir / "render_summary.json").open(encoding="utf-8") as file:
        render_summary = json.load(
            file,
            parse_constant=lambda value: (_ for _ in ()).throw(
                ValueError(f"non-standard JSON constant: {value}")
            ),
        )
    expected_render_summary = {
        "rendered_images": 2,
        "expected_images": 2,
        "image_files": list(image_files),
        "state_background_driver": "score_3d",
        "score_tracks": ["score_1d", "score_3d"],
    }
    if any(
        render_summary.get(key) != value
        for key, value in expected_render_summary.items()
    ):
        raise RuntimeError("staged render summary does not match expected chart audit")


def _empty_trades() -> pd.DataFrame:
    return pd.DataFrame(columns=TRADE_COLUMNS)


def _strategy_metrics(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    config: TrendAllocationConfig,
) -> dict[str, object]:
    return calculate_continuous_metrics(
        equity_curve,
        trades,
        initial_equity=config.initial_capital,
        start=equity_curve.iloc[0]["datetime"],
        end=equity_curve.iloc[-1]["datetime"],
    )


def _comparison_row(
    strategy: str,
    metrics: Mapping[str, object],
) -> dict[str, object]:
    return {"strategy": strategy, **dict(metrics)}


def run_regime_overlay_analysis(
    *,
    symbol: str,
    daily: pd.DataFrame,
    factors: pd.DataFrame | None,
    calendar: pd.DataFrame,
    price_adjustment_mode: str,
    start: object,
    end: object,
    output_dir: Path,
    source_audit: Mapping[str, object] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Calculate and atomically publish the complete overlay backtest."""
    output_dir = Path(output_dir)
    _guard_output_directory(output_dir, overwrite)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_dir.with_name(f".{output_dir.name}.{uuid4().hex}.staging")
    staging_dir.mkdir()
    try:
        display_name = _display_name(symbol)
        chart_files = _chart_files(symbol, display_name)
        output_artifact_paths = _output_artifact_paths(chart_files)
        bars = build_causal_bars(daily, factors, price_adjustment_mode)
        bars = bars.loc[bars["symbol"].eq(symbol)].reset_index(drop=True)
        if bars.empty:
            raise ValueError(f"daily data missing symbol: {symbol}")
        start_date = pd.Timestamp(start).normalize()
        end_date = pd.Timestamp(end).normalize()
        if start_date > end_date:
            raise ValueError("start must be on or before end")
        prewarm_count = int((bars["datetime"] < start_date).sum())
        if prewarm_count < 252:
            raise ValueError("backtest requires at least 252 prewarm bars")
        execution_bars = bars.loc[
            bars["datetime"].between(start_date, end_date, inclusive="both")
        ].copy()
        if execution_bars.empty:
            raise ValueError("no bars in requested backtest period")
        actual_start = pd.Timestamp(execution_bars.iloc[0]["datetime"])
        actual_end = pd.Timestamp(execution_bars.iloc[-1]["datetime"])
        if actual_start != start_date or actual_end != end_date:
            raise ValueError("requested start and end must both be actual trading rows")

        normalized_calendar = normalize_trade_calendar(calendar)
        regime_config = RegimeConfig(
            price_adjustment_mode=price_adjustment_mode,
        )
        predictions = predict_regime(
            bars,
            normalized_calendar,
            regime_config,
        )
        trend_config = TrendAllocationConfig(
            symbol=symbol,
            confirmation_days=1,
            slow_period=20,
            slope_lookback=3,
            risk_increase_cooldown_days=10,
        )
        result = run_regime_overlay_backtest(
            bars,
            predictions,
            start=start_date,
            end=end_date,
            config=trend_config,
        )
        if result.ema_result is None:
            raise RuntimeError("overlay result missing independent EMA result")

        overlay_metrics = dict(result.summary)
        ema_metrics = _strategy_metrics(
            result.ema_result.equity_curve,
            result.ema_result.trades,
            trend_config,
        )
        buy_hold_equity = build_buy_hold_equity(
            execution_bars,
            initial_capital=trend_config.initial_capital,
        )
        buy_hold_metrics = _strategy_metrics(
            buy_hold_equity,
            _empty_trades(),
            trend_config,
        )
        comparison = pd.DataFrame(
            [
                _comparison_row("regime_overlay", overlay_metrics),
                _comparison_row("ema_only", ema_metrics),
                _comparison_row("buy_hold", buy_hold_metrics),
            ]
        )

        annual_periods = calendar_periods(
            start_date,
            end_date,
            frequency="year",
        )
        half_year_periods = calendar_periods(
            start_date,
            end_date,
            frequency="half_year",
        )
        strategy_curves = (
            (
                "regime_overlay",
                result.equity_curve,
                result.trades,
            ),
            (
                "ema_only",
                result.ema_result.equity_curve,
                result.ema_result.trades,
            ),
            ("buy_hold", buy_hold_equity, _empty_trades()),
        )
        annual_metrics = pd.concat(
            [
                calculate_period_table(
                    curve,
                    trades,
                    initial_capital=trend_config.initial_capital,
                    periods=annual_periods,
                    strategy=name,
                )
                for name, curve, trades in strategy_curves
            ],
            ignore_index=True,
        )
        semiannual_metrics = pd.concat(
            [
                calculate_period_table(
                    curve,
                    trades,
                    initial_capital=trend_config.initial_capital,
                    periods=half_year_periods,
                    strategy=name,
                )
                for name, curve, trades in strategy_curves
            ],
            ignore_index=True,
        )

        signals = result.signals
        target_counts = signals["target_weight"].value_counts().sort_index().to_dict()
        target_distribution = {
            f"{float(weight):g}": {
                "days": int(count),
                "fraction": float(count) / len(signals),
            }
            for weight, count in target_counts.items()
        }
        causal_dates_valid = bool(
            (
                signals["max_feature_source_date"]
                <= signals["regime_feature_asof_date"]
            ).all()
            and (signals["regime_feature_asof_date"] < signals["datetime"]).all()
        )
        ceilings_valid = bool(
            (signals["target_weight"] <= signals["ema_target_weight"]).all()
            and (signals["target_weight"] <= signals["regime_cap"]).all()
        )
        semi_labels = [
            period.label
            for period in half_year_periods
            if not semiannual_metrics.loc[
                semiannual_metrics["period"].eq(period.label)
            ].empty
        ]
        risk_checks = {
            "max_drawdown_within_35_percent": (
                float(overlay_metrics["max_drawdown"]) >= -0.35
            ),
            "annual_one_way_turnover_within_8x": (
                float(overlay_metrics["annual_one_way_turnover"]) <= 8.0
            ),
        }
        summary_payload: dict[str, object] = {
            "schema_version": 1,
            "symbol": symbol,
            "backtest": {
                "start_date": str(start_date.date()),
                "end_date": str(end_date.date()),
                "initial_state": "flat",
                "forced_final_liquidation": False,
            },
            "data": {
                "price_adjustment_mode": price_adjustment_mode,
                "first_bar_date": bars["datetime"].min(),
                "last_bar_date": bars["datetime"].max(),
                "bar_count": len(bars),
                "prewarm_bar_count": prewarm_count,
                "execution_bar_count": len(execution_bars),
            },
            "config": asdict(trend_config),
            "performance": {
                "regime_overlay": overlay_metrics,
                "ema_only": ema_metrics,
                "buy_hold": buy_hold_metrics,
            },
            "semiannual": {
                "period_count": len(semi_labels),
                "first_period": semi_labels[0],
                "last_period": semi_labels[-1],
            },
            "overlay_diagnostics": {
                "target_distribution": target_distribution,
                "ema_reduction_days": int(signals["ema_reduction_applied"].sum()),
                "regime_reduction_days": int(signals["regime_reduction_applied"].sum()),
                "score_1d_reduction_days": int(
                    signals["score_1d_reduction_applied"].sum()
                ),
                "cooldown_blocked_days": int(signals["risk_increase_blocked"].sum()),
            },
            "risk_checks": risk_checks,
            "anti_lookahead_audit": {
                "causal_dates_valid": causal_dates_valid,
                "position_ceilings_valid": ceilings_valid,
                "prediction_alignment": "previous actual symbol bar to next open",
            },
            "output_files": list(output_artifact_paths),
        }
        source_payload: dict[str, object] = dict(source_audit or {})
        source_payload.update(
            {
                "symbol": symbol,
                "price_adjustment_mode": price_adjustment_mode,
                "backtest_start_date": start_date,
                "backtest_end_date": end_date,
                "bar_count": len(bars),
                "prewarm_bar_count": prewarm_count,
                "factor_lineage_valid": bool(
                    price_adjustment_mode == "raw"
                    or (bars["factor_source_date"] <= bars["datetime"]).all()
                ),
                "calendar_maximum_date": normalized_calendar["datetime"].max(),
            }
        )

        result.signals.to_csv(staging_dir / "signals.csv", index=False)
        result.trades.to_csv(staging_dir / "trades.csv", index=False)
        result.positions.to_csv(staging_dir / "positions.csv", index=False)
        result.equity_curve.to_csv(staging_dir / "equity_curve.csv", index=False)
        comparison.to_csv(staging_dir / "comparison.csv", index=False)
        annual_metrics.to_csv(staging_dir / "annual_metrics.csv", index=False)
        semiannual_metrics.to_csv(
            staging_dir / "semiannual_metrics.csv",
            index=False,
        )
        summary = _write_json(summary_payload, staging_dir / "summary.json")
        _write_json(source_payload, staging_dir / "source_audit.json")
        render_regime_comparison_charts(
            symbol=symbol,
            name=display_name,
            overlay_result=result,
            ema_result=result.ema_result,
            ema_metrics=ema_metrics,
            output_dir=staging_dir / "charts",
            report_start=start_date,
            report_end=end_date,
        )
        _validate_staged_outputs(
            staging_dir,
            chart_files=chart_files,
            output_artifact_paths=output_artifact_paths,
        )
        _publish_staging(staging_dir, output_dir)
        return summary
    except Exception as operation_error:
        if staging_dir.exists():
            try:
                shutil.rmtree(staging_dir)
            except Exception as cleanup_error:
                raise _combined_runtime_error(
                    "backtest operation failed and staging cleanup failed",
                    operation_error,
                    cleanup_error,
                ) from cleanup_error
        raise


def _file_audit(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _select_source_rows(
    frame: pd.DataFrame,
    *,
    symbol: str,
    start: str,
    end: str,
) -> pd.DataFrame:
    symbol_column = "symbol" if "symbol" in frame.columns else "ts_code"
    date_column = next(
        (
            column
            for column in ("datetime", "trade_date", "factor_source_date")
            if column in frame.columns
        ),
        None,
    )
    if symbol_column not in frame.columns or date_column is None:
        raise ValueError("source data requires symbol and date columns")
    dates = pd.to_datetime(frame[date_column], errors="raise")
    return frame.loc[
        frame[symbol_column].astype(str).eq(symbol)
        & dates.between(pd.Timestamp(start), pd.Timestamp(end), inclusive="both")
    ].copy()


def build_parser() -> argparse.ArgumentParser:
    """Build the frozen real-run CLI parser."""
    parser = argparse.ArgumentParser(
        description="Backtest the causal regime overlay on the accepted EMA strategy.",
    )
    parser.add_argument("--symbol", default="159915.SZ")
    parser.add_argument("--data-start", default="2016-01-01")
    parser.add_argument("--start", default="2017-08-14")
    parser.add_argument("--end", default="2026-07-20")
    parser.add_argument("--download", action="store_true")
    parser.add_argument("--daily-csv", type=Path)
    parser.add_argument("--factors-csv", type=Path)
    parser.add_argument("--calendar-csv", type=Path)
    parser.add_argument(
        "--price-adjustment-mode",
        choices=["raw", "point_in_time_adjusted"],
        default="point_in_time_adjusted",
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = build_parser().parse_args(argv)
    csv_arguments = (args.daily_csv, args.factors_csv, args.calendar_csv)
    if args.download and any(path is not None for path in csv_arguments):
        raise ValueError("--download cannot be combined with local CSV inputs")
    if args.download:
        inputs = download_regime_inputs(
            args.symbol,
            args.data_start,
            args.end,
        )
        daily = inputs.daily
        factors = inputs.factors
        calendar = inputs.calendar
        source_audit: Mapping[str, object] = inputs.source_audit
    else:
        if args.daily_csv is None or args.calendar_csv is None:
            raise ValueError("local mode requires --daily-csv and --calendar-csv")
        if (
            args.price_adjustment_mode == "point_in_time_adjusted"
            and args.factors_csv is None
        ):
            raise ValueError("point_in_time_adjusted mode requires --factors-csv")
        daily = _select_source_rows(
            pd.read_csv(args.daily_csv),
            symbol=args.symbol,
            start=args.data_start,
            end=args.end,
        )
        factors = None
        if args.factors_csv is not None:
            factors = _select_source_rows(
                pd.read_csv(args.factors_csv),
                symbol=args.symbol,
                start=args.data_start,
                end=args.end,
            )
        calendar = pd.read_csv(args.calendar_csv)
        source_audit = {
            "provider": "local_csv",
            "daily": _file_audit(args.daily_csv),
            "factors": (
                _file_audit(args.factors_csv) if args.factors_csv is not None else None
            ),
            "calendar": _file_audit(args.calendar_csv),
        }

    run_regime_overlay_analysis(
        symbol=args.symbol,
        daily=daily,
        factors=factors,
        calendar=calendar,
        price_adjustment_mode=args.price_adjustment_mode,
        start=args.start,
        end=args.end,
        output_dir=args.output_dir,
        source_audit=source_audit,
        overwrite=args.overwrite,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
