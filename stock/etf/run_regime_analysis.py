"""Run and publish the preregistered causal regime analysis."""

from __future__ import annotations

import argparse
import hashlib
import json
import shutil
from collections.abc import Mapping, Sequence
from math import isfinite
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd

from .regime_data import (
    build_causal_bars,
    download_regime_inputs,
    normalize_trade_calendar,
)
from .regime_evaluation import (
    attach_realized_labels,
    evaluate_regime_predictions,
)
from .regime_rules import (
    RegimeConfig,
    calculate_realized_regime,
    predict_regime,
)

ETF_ROOT = Path(__file__).resolve().parent
DEFAULT_OUTPUT_DIR = ETF_ROOT / "output/20260725_chuangyeban_trend"
OUTPUT_FILES = (
    "daily_predictions.csv",
    "labeled_predictions.csv",
    "accuracy_by_period.csv",
    "confusion_matrix_1d.csv",
    "confusion_matrix_3d.csv",
    "transition_accuracy.csv",
    "summary.json",
    "source_audit.json",
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


def _publish_staging(staging_dir: Path, output_dir: Path) -> None:
    backup_dir = output_dir.with_name(f".{output_dir.name}.{uuid4().hex}.backup")
    had_output = output_dir.exists()
    if had_output:
        output_dir.replace(backup_dir)
    try:
        staging_dir.replace(output_dir)
    except Exception:
        if output_dir.exists():
            shutil.rmtree(output_dir)
        if had_output:
            backup_dir.replace(output_dir)
        raise
    else:
        if had_output:
            shutil.rmtree(backup_dir)


def _select_symbol_and_dates(
    frame: pd.DataFrame,
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
    start_date = pd.Timestamp(start)
    end_date = pd.Timestamp(end)
    return frame.loc[
        frame[symbol_column].astype(str).eq(symbol)
        & dates.between(start_date, end_date, inclusive="both")
    ].copy()


def _latest_prediction_summary(predictions: pd.DataFrame) -> list[dict[str, object]]:
    latest_date = pd.Timestamp(predictions["feature_asof_date"].max())
    latest = predictions.loc[
        predictions["feature_asof_date"].eq(latest_date)
    ].sort_values("prediction_horizon")
    return [
        {
            "feature_asof_date": row["feature_asof_date"],
            "prediction_horizon": row["prediction_horizon"],
            "prediction_for_date": row["prediction_for_date"],
            "score": row["score"],
            "state": row["state"],
        }
        for _, row in latest.iterrows()
    ]


def _validate_staged_outputs(staging_dir: Path) -> None:
    missing = [
        filename
        for filename in OUTPUT_FILES
        if not (staging_dir / filename).is_file()
        or (staging_dir / filename).stat().st_size == 0
    ]
    if missing:
        raise RuntimeError(f"staged output missing files: {missing}")
    for filename in ("summary.json", "source_audit.json"):
        with (staging_dir / filename).open(encoding="utf-8") as file:
            json.load(
                file,
                parse_constant=lambda value: (_ for _ in ()).throw(
                    ValueError(f"non-standard JSON constant: {value}")
                ),
            )


def run_regime_analysis(
    *,
    symbol: str,
    daily: pd.DataFrame,
    factors: pd.DataFrame | None,
    calendar: pd.DataFrame,
    price_adjustment_mode: str,
    output_dir: Path,
    source_audit: Mapping[str, object] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Calculate, evaluate, and atomically publish one symbol's analysis."""
    output_dir = Path(output_dir)
    _guard_output_directory(output_dir, overwrite)
    output_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = output_dir.with_name(f".{output_dir.name}.{uuid4().hex}.staging")
    staging_dir.mkdir()
    try:
        bars = build_causal_bars(
            daily,
            factors,
            price_adjustment_mode,
        )
        bars = bars.loc[bars["symbol"].eq(symbol)].reset_index(drop=True)
        if bars.empty:
            raise ValueError(f"daily data missing symbol: {symbol}")
        normalized_calendar = normalize_trade_calendar(calendar)
        config = RegimeConfig(
            price_adjustment_mode=price_adjustment_mode,
        )
        realized = calculate_realized_regime(bars, config)
        predictions = predict_regime(bars, normalized_calendar, config)
        labeled = attach_realized_labels(predictions, realized)
        evaluation = evaluate_regime_predictions(labeled)

        source_dates_valid = bool(
            (
                predictions["max_feature_source_date"]
                <= predictions["feature_asof_date"]
            ).all()
        )
        known_labels = labeled.loc[labeled["label_date"].notna()]
        labels_follow_features = bool(
            (known_labels["feature_asof_date"] < known_labels["label_date"]).all()
        )
        factor_lineage_valid = bool(
            price_adjustment_mode == "raw"
            or (bars["factor_source_date"] <= bars["datetime"]).all()
        )
        summary_payload: dict[str, object] = {
            "schema_version": 1,
            "symbol": symbol,
            "price_adjustment_mode": price_adjustment_mode,
            "data": {
                "bar_count": len(bars),
                "first_bar_date": bars["datetime"].min(),
                "last_bar_date": bars["datetime"].max(),
                "prediction_row_count": len(predictions),
                "labeled_prediction_count": int(labeled["label_date"].notna().sum()),
            },
            "latest_predictions": _latest_prediction_summary(predictions),
            "evaluation": evaluation.summary,
            "anti_lookahead_audit": {
                "source_dates_valid": source_dates_valid,
                "labels_follow_feature_dates": labels_follow_features,
                "factor_lineage_valid": factor_lineage_valid,
                "future_bar_mutation_test": (
                    "covered by test_mutating_future_bars_does_not_change_prefix_predictions"
                ),
                "future_factor_mutation_test": (
                    "covered by test_mutating_future_factor_does_not_change_adjusted_prefix"
                ),
                "prefix_recalculation_test": (
                    "covered by test_prefix_recalculation_matches_vectorized_predictions"
                ),
            },
            "output_files": list(OUTPUT_FILES),
        }
        source_payload: dict[str, object] = dict(source_audit or {})
        source_payload.update(
            {
                "symbol": symbol,
                "price_adjustment_mode": price_adjustment_mode,
                "bar_count": len(bars),
                "first_bar_date": bars["datetime"].min(),
                "last_bar_date": bars["datetime"].max(),
                "factor_lineage_valid": factor_lineage_valid,
                "maximum_factor_source_date": (
                    bars["factor_source_date"].max()
                    if bars["factor_source_date"].notna().any()
                    else None
                ),
                "calendar_maximum_date": normalized_calendar["datetime"].max(),
            }
        )

        predictions.to_csv(staging_dir / "daily_predictions.csv", index=False)
        labeled.to_csv(staging_dir / "labeled_predictions.csv", index=False)
        evaluation.accuracy_by_period.to_csv(
            staging_dir / "accuracy_by_period.csv",
            index=False,
        )
        evaluation.confusion_matrix_1d.to_csv(
            staging_dir / "confusion_matrix_1d.csv",
            index=False,
        )
        evaluation.confusion_matrix_3d.to_csv(
            staging_dir / "confusion_matrix_3d.csv",
            index=False,
        )
        evaluation.transition_accuracy.to_csv(
            staging_dir / "transition_accuracy.csv",
            index=False,
        )
        summary = _write_json(summary_payload, staging_dir / "summary.json")
        _write_json(source_payload, staging_dir / "source_audit.json")
        _validate_staged_outputs(staging_dir)
        _publish_staging(staging_dir, output_dir)
        return summary
    except Exception:
        if staging_dir.exists():
            shutil.rmtree(staging_dir)
        raise


def _file_audit(path: Path) -> dict[str, object]:
    return {
        "path": str(path.resolve()),
        "size_bytes": path.stat().st_size,
        "sha256": hashlib.sha256(path.read_bytes()).hexdigest(),
    }


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Evaluate causal 1d/3d market regime rules.",
    )
    parser.add_argument("--symbol", default="159915.SZ")
    parser.add_argument("--start", default="2017-08-14")
    parser.add_argument("--end", default=str(pd.Timestamp.today().date()))
    parser.add_argument("--daily-csv", type=Path)
    parser.add_argument("--factors-csv", type=Path)
    parser.add_argument("--calendar-csv", type=Path)
    parser.add_argument("--download", action="store_true")
    parser.add_argument(
        "--price-adjustment-mode",
        choices=["raw", "point_in_time_adjusted"],
        required=True,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """CLI entry point."""
    args = _parser().parse_args(argv)
    csv_arguments = (args.daily_csv, args.factors_csv, args.calendar_csv)
    if args.download and any(path is not None for path in csv_arguments):
        raise ValueError("--download cannot be combined with local CSV inputs")

    if args.download:
        inputs = download_regime_inputs(
            args.symbol,
            args.start,
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
        raw_daily = pd.read_csv(args.daily_csv)
        daily = _select_symbol_and_dates(
            raw_daily,
            args.symbol,
            args.start,
            args.end,
        )
        factors = None
        if args.factors_csv is not None:
            raw_factors = pd.read_csv(args.factors_csv)
            factors = _select_symbol_and_dates(
                raw_factors,
                args.symbol,
                args.start,
                args.end,
            )
        calendar = pd.read_csv(args.calendar_csv)
        source_audit = {
            "provider": "local_csv",
            "daily": _file_audit(args.daily_csv),
            "calendar": _file_audit(args.calendar_csv),
            "factors": (
                _file_audit(args.factors_csv) if args.factors_csv is not None else None
            ),
            "requested_start_date": args.start,
            "requested_end_date": args.end,
        }

    run_regime_analysis(
        symbol=args.symbol,
        daily=daily,
        factors=factors,
        calendar=calendar,
        price_adjustment_mode=args.price_adjustment_mode,
        output_dir=args.output_dir,
        source_audit=source_audit,
        overwrite=args.overwrite,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
