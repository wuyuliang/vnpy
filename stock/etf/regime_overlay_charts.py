"""Prepare daily and weekly state tracks for regime overlay charts."""

from __future__ import annotations

import json
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from math import isfinite
from pathlib import Path
from typing import Any
from uuid import uuid4

import numpy as np
import pandas as pd
from PIL import Image

from stock.etf.ema_trend_allocation_strategy import TrendAllocationResult
from stock.etf.regime_overlay_strategy import RegimeOverlayResult
from stock.etf.regime_rules import score_to_state
from stock.etf.render_trade_charts import (
    aggregate_weekly_bars,
    make_chart_filename,
    render_symbol_card,
)


@dataclass(frozen=True)
class StateBand:
    """A market regime label and its chart color."""

    state: str
    color: str


STATE_COLORS: dict[str, str] = {
    "趋势向下": "#f3c1bc",
    "震荡向下": "#f3dfb1",
    "无趋势": "#e2e8f0",
    "震荡向上": "#c8e8d4",
    "趋势向上": "#9fd8b5",
}

TRACK_COLUMNS = [
    "datetime",
    "score_1d",
    "score_3d",
    "state_3d",
    "state_color",
]
INDEX_COLUMNS = [
    "rank",
    "strategy",
    "symbol",
    "name",
    "trade_count",
    "buy_count",
    "sell_count",
    "is_open",
    "total_return",
    "max_drawdown",
    "sharpe",
    "annual_one_way_turnover",
    "image_path",
]


def _validate_score(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(
        value,
        (int, float, np.integer, np.floating),
    ):
        raise ValueError(f"{name} must be numeric")
    score = float(value)
    if not isfinite(score) or not -3.0 <= score <= 3.0:
        raise ValueError(f"{name} must be finite and within [-3, 3]")
    return score


def state_band(score: object) -> StateBand:
    """Return the documented regime and color for one valid score."""
    state = score_to_state(_validate_score(score, "score")).value
    return StateBand(state=state, color=STATE_COLORS[state])


def prepare_daily_state_tracks(signals: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize daily regime tracks without filling dates."""
    required = ["datetime", "score_1d", "score_3d", "state_3d"]
    missing = set(required) - set(signals.columns)
    if missing:
        raise ValueError(f"state signals missing columns: {sorted(missing)}")

    frame = signals.loc[:, required].copy()
    try:
        dates = pd.to_datetime(frame["datetime"], format="mixed", errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("datetime must contain parseable dates") from exc
    if dates.isna().any():
        raise ValueError("datetime must not be missing")
    if dates.dt.tz is not None:
        raise ValueError("datetime must be timezone-naive")
    frame["datetime"] = dates.dt.normalize()
    if frame["datetime"].duplicated().any():
        raise ValueError("state signals contain duplicate dates")

    for column in ("score_1d", "score_3d"):
        frame[column] = frame[column].map(
            lambda value, name=column: _validate_score(value, name)
        )

    expected_states = frame["score_3d"].map(lambda value: state_band(value).state)
    if not frame["state_3d"].astype(str).eq(expected_states).all():
        raise ValueError("state_3d does not match score_3d")
    frame["state_color"] = frame["score_3d"].map(lambda value: state_band(value).color)
    return frame.loc[:, TRACK_COLUMNS].sort_values("datetime", ignore_index=True)


def aggregate_weekly_state_tracks(daily: pd.DataFrame) -> pd.DataFrame:
    """Select each Friday-ending week's last actual daily state row."""
    frame = prepare_daily_state_tracks(daily)
    if frame.empty:
        return frame
    return (
        frame.set_index("datetime")
        .resample("W-FRI")
        .last()
        .dropna(subset=["score_1d", "score_3d"])
        .reset_index()
        .loc[:, TRACK_COLUMNS]
    )


def _latest_position(
    positions: pd.DataFrame,
    report_end: pd.Timestamp,
) -> pd.Series | None:
    if "datetime" not in positions.columns:
        raise ValueError("positions missing datetime column")
    try:
        dates = pd.to_datetime(
            positions["datetime"],
            format="mixed",
            errors="raise",
        )
    except (TypeError, ValueError) as exc:
        raise ValueError("position datetime must contain parseable dates") from exc
    if dates.isna().any():
        raise ValueError("position datetime must not be missing")
    if dates.dt.tz is not None:
        raise ValueError("position datetime must be timezone-naive")
    dates = dates.dt.normalize()
    rows = positions.loc[dates.eq(report_end.normalize())]
    return None if rows.empty else rows.iloc[-1]


def _position_is_open(position: pd.Series | None) -> bool:
    if position is None:
        return False
    missing = {"quantity", "weight"} - set(position.index)
    if missing:
        raise ValueError(f"position missing columns: {sorted(missing)}")
    open_states = []
    for column in ("quantity", "weight"):
        value = position[column]
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float, np.integer, np.floating))
            or not isfinite(float(value))
            or float(value) < 0
        ):
            raise ValueError(f"position {column} must be a finite non-negative number")
        open_states.append(float(value) > 0)
    if open_states[0] != open_states[1]:
        raise ValueError("position quantity and weight open state conflict")
    return open_states[0]


def _normalize_trades(trades: pd.DataFrame, strategy: str) -> pd.DataFrame:
    if "side" not in trades.columns:
        raise ValueError(f"{strategy} trades missing side column")
    normalized = trades.copy()
    sides: list[str] = []
    for value in normalized["side"]:
        if not isinstance(value, str):
            raise ValueError(f"{strategy} trade side must be buy or sell")
        side = value.strip().lower()
        if side not in {"buy", "sell"}:
            raise ValueError(f"{strategy} trade side must be buy or sell")
        sides.append(side)
    normalized["side"] = sides
    return normalized


def _target_distribution(signals: pd.DataFrame) -> dict[str, dict[str, int | float]]:
    if "target_weight" not in signals.columns:
        raise ValueError("signals missing target_weight column")
    counts = {0.0: 0, 0.5: 0, 1.0: 0}
    for value in signals["target_weight"]:
        if (
            isinstance(value, bool)
            or not isinstance(value, (int, float, np.integer, np.floating))
            or not isfinite(float(value))
            or float(value) not in counts
        ):
            raise ValueError("target_weight must be 0, 0.5, or 1")
        counts[float(value)] += 1
    if sum(counts.values()) != len(signals):
        raise ValueError("target_weight distribution does not cover all signals")
    return {
        f"{weight:g}": {
            "days": count,
            "fraction": count / len(signals),
        }
        for weight, count in counts.items()
    }


def _index_row(
    *,
    rank: int,
    strategy: str,
    symbol: str,
    name: str,
    trades: pd.DataFrame,
    is_open: bool,
    metrics: Mapping[str, object],
    image_path: str,
) -> dict[str, object]:
    return {
        "rank": rank,
        "strategy": strategy,
        "symbol": symbol,
        "name": name,
        "trade_count": int(len(trades)),
        "buy_count": int(trades["side"].eq("buy").sum()),
        "sell_count": int(trades["side"].eq("sell").sum()),
        "is_open": is_open,
        "total_return": metrics["total_return"],
        "max_drawdown": metrics["max_drawdown"],
        "sharpe": metrics["sharpe"],
        "annual_one_way_turnover": metrics["annual_one_way_turnover"],
        "image_path": image_path,
    }


def _validate_staged_artifacts(
    staging_path: Path,
    filenames: list[str],
    image_sizes: Mapping[str, tuple[int, int]],
    index: pd.DataFrame,
    summary: Mapping[str, object],
) -> None:
    expected_files = {*filenames, "index.csv", "render_summary.json"}
    actual_files = {path.name for path in staging_path.iterdir()}
    if actual_files != expected_files:
        raise RuntimeError("staged charts do not match expected files")

    for filename in filenames:
        image_path = staging_path / filename
        if not image_path.is_file() or image_path.stat().st_size == 0:
            raise RuntimeError(f"staged image is missing or empty: {filename}")
        with Image.open(image_path) as image:
            if image.size != image_sizes[filename]:
                raise RuntimeError(
                    f"staged image {filename} has size {image.size}, "
                    f"expected {image_sizes[filename]}"
                )
            image.verify()

    staged_index = pd.read_csv(staging_path / "index.csv")
    if (
        staged_index.columns.tolist() != INDEX_COLUMNS
        or staged_index["image_path"].tolist() != index["image_path"].tolist()
    ):
        raise RuntimeError("staged index does not match expected rows")
    for image_path in staged_index["image_path"]:
        if not (staging_path / image_path).is_file():
            raise FileNotFoundError(f"index image_path does not exist: {image_path}")

    staged_summary = json.loads(
        (staging_path / "render_summary.json").read_text(encoding="utf-8"),
        parse_constant=lambda value: (_ for _ in ()).throw(
            ValueError(f"non-standard JSON constant: {value}")
        ),
    )
    if staged_summary != summary:
        raise RuntimeError("staged render summary does not match expected data")


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


def _publish_staging(staging_path: Path, output_path: Path) -> None:
    backup_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.backup")
    had_output = output_path.exists()
    if had_output:
        output_path.replace(backup_path)
    try:
        staging_path.replace(output_path)
    except Exception as publish_error:
        try:
            if output_path.exists():
                shutil.rmtree(output_path)
            if had_output:
                backup_path.replace(output_path)
        except Exception as restore_error:
            raise _combined_runtime_error(
                "failed to restore previous charts after publish failure",
                publish_error,
                restore_error,
            ) from restore_error
        raise
    else:
        if had_output:
            try:
                shutil.rmtree(backup_path)
            except Exception as cleanup_error:
                try:
                    if output_path.exists():
                        shutil.rmtree(output_path)
                    backup_path.replace(output_path)
                except Exception as rollback_error:
                    raise _combined_runtime_error(
                        "failed to roll back charts after backup cleanup failure",
                        cleanup_error,
                        rollback_error,
                    ) from rollback_error
                raise


def render_regime_comparison_charts(
    *,
    symbol: str,
    name: str,
    overlay_result: RegimeOverlayResult,
    ema_result: TrendAllocationResult,
    ema_metrics: Mapping[str, object],
    output_dir: str | Path,
    report_start: object,
    report_end: object,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render the regime overlay and independent EMA comparison artifacts."""
    output_path = Path(output_dir)
    if output_path.exists():
        if not output_path.is_dir():
            raise FileExistsError(f"output path is not a directory: {output_path}")
        if not overwrite and any(output_path.iterdir()):
            raise FileExistsError(f"output directory is not empty: {output_path}")

    start_date = pd.Timestamp(report_start).normalize()
    end_date = pd.Timestamp(report_end).normalize()
    if pd.isna(start_date) or pd.isna(end_date) or start_date > end_date:
        raise ValueError("report_start and report_end must define a valid date range")
    if overlay_result.signals.empty:
        raise ValueError("overlay signals must not be empty")

    overlay_trades = _normalize_trades(overlay_result.trades, "overlay")
    ema_trades = _normalize_trades(ema_result.trades, "ema")
    daily_states = prepare_daily_state_tracks(overlay_result.signals)
    weekly_states = aggregate_weekly_state_tracks(daily_states)
    weekly_bars = aggregate_weekly_bars(overlay_result.signals)
    target_distribution = _target_distribution(overlay_result.signals)
    overlay_performance = {
        **overlay_result.summary,
        "target_distribution": target_distribution,
    }

    overlay_latest = _latest_position(overlay_result.positions, end_date)
    ema_latest = _latest_position(ema_result.positions, end_date)
    overlay_is_open = _position_is_open(overlay_latest)
    ema_is_open = _position_is_open(ema_latest)
    overlay_image = render_symbol_card(
        symbol=symbol,
        name=name,
        fund_type="股票型ETF",
        daily_bars=overlay_result.signals,
        weekly_bars=weekly_bars,
        trades=overlay_trades,
        latest_position=overlay_latest if overlay_is_open else None,
        report_start=start_date,
        report_end=end_date,
        review_label="状态覆盖策略",
        performance=overlay_performance,
        daily_state_scores=daily_states,
        weekly_state_scores=weekly_states,
    )
    ema_image = render_symbol_card(
        symbol=symbol,
        name=name,
        fund_type="股票型ETF",
        daily_bars=overlay_result.signals,
        weekly_bars=weekly_bars,
        trades=ema_trades,
        latest_position=ema_latest if ema_is_open else None,
        report_start=start_date,
        report_end=end_date,
        review_label="EMA 基线",
        performance=ema_metrics,
    )

    filenames = [
        make_chart_filename(1, symbol, f"{name}_状态覆盖"),
        make_chart_filename(2, symbol, f"{name}_EMA基线"),
    ]
    images = [
        (overlay_image, (1680, 1120)),
        (ema_image, (1680, 1000)),
    ]
    for image, expected_size in images:
        if image.size != expected_size:
            raise ValueError(
                f"rendered image has size {image.size}, expected {expected_size}"
            )
    image_sizes = {
        filename: expected_size
        for filename, (_, expected_size) in zip(filenames, images, strict=True)
    }

    index = pd.DataFrame(
        [
            _index_row(
                rank=1,
                strategy="regime_overlay",
                symbol=symbol,
                name=name,
                trades=overlay_trades,
                is_open=overlay_is_open,
                metrics=overlay_result.summary,
                image_path=filenames[0],
            ),
            _index_row(
                rank=2,
                strategy="ema_only",
                symbol=symbol,
                name=name,
                trades=ema_trades,
                is_open=ema_is_open,
                metrics=ema_metrics,
                image_path=filenames[1],
            ),
        ],
        columns=INDEX_COLUMNS,
    )
    summary = {
        "schema_version": 1,
        "symbol": symbol,
        "report_start": start_date.date().isoformat(),
        "report_end": end_date.date().isoformat(),
        "rendered_images": 2,
        "expected_images": 2,
        "image_files": filenames,
        "state_background_driver": "score_3d",
        "score_tracks": ["score_1d", "score_3d"],
    }
    summary_json = json.dumps(
        summary,
        allow_nan=False,
        ensure_ascii=False,
        sort_keys=True,
        indent=2,
    )

    output_path.parent.mkdir(parents=True, exist_ok=True)
    staging_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.staging")
    staging_path.mkdir()
    try:
        for (image, _), filename in zip(images, filenames, strict=True):
            image.save(staging_path / filename)
        index.to_csv(staging_path / "index.csv", index=False)
        (staging_path / "render_summary.json").write_text(
            summary_json,
            encoding="utf-8",
        )
        _validate_staged_artifacts(
            staging_path,
            filenames,
            image_sizes,
            index,
            summary,
        )
        _publish_staging(staging_path, output_path)
    except Exception as operation_error:
        if staging_path.exists():
            try:
                shutil.rmtree(staging_path)
            except Exception as cleanup_error:
                raise _combined_runtime_error(
                    "chart operation failed and staging cleanup failed",
                    operation_error,
                    cleanup_error,
                ) from cleanup_error
        raise
    return summary
