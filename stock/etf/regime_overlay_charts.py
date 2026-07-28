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
    dates = pd.to_datetime(positions["datetime"], errors="coerce").dt.normalize()
    rows = positions.loc[dates.eq(report_end.normalize())]
    return None if rows.empty else rows.iloc[-1]


def _position_is_open(position: pd.Series | None) -> bool:
    if position is None:
        return False
    for column in ("quantity", "weight"):
        value = position.get(column)
        if (
            not isinstance(value, bool)
            and isinstance(value, (int, float, np.integer, np.floating))
            and isfinite(float(value))
            and float(value) > 0
        ):
            return True
    return False


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


def _publish_staging(staging_path: Path, output_path: Path) -> None:
    backup_path = output_path.with_name(f".{output_path.name}.{uuid4().hex}.backup")
    had_output = output_path.exists()
    if had_output:
        output_path.replace(backup_path)
    try:
        staging_path.replace(output_path)
    except Exception:
        if output_path.exists():
            shutil.rmtree(output_path)
        if had_output:
            backup_path.replace(output_path)
        raise
    else:
        if had_output:
            shutil.rmtree(backup_path)


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

    daily_states = prepare_daily_state_tracks(overlay_result.signals)
    weekly_states = aggregate_weekly_state_tracks(daily_states)
    weekly_bars = aggregate_weekly_bars(overlay_result.signals)
    target_counts = overlay_result.signals["target_weight"].value_counts()
    target_distribution = {
        f"{weight:g}": {
            "days": int(target_counts.get(weight, 0)),
            "fraction": float(target_counts.get(weight, 0))
            / len(overlay_result.signals),
        }
        for weight in (0.0, 0.5, 1.0)
    }
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
        trades=overlay_result.trades,
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
        trades=ema_result.trades,
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

    index = pd.DataFrame(
        [
            _index_row(
                rank=1,
                strategy="regime_overlay",
                symbol=symbol,
                name=name,
                trades=overlay_result.trades,
                is_open=overlay_is_open,
                metrics=overlay_result.summary,
                image_path=filenames[0],
            ),
            _index_row(
                rank=2,
                strategy="ema_only",
                symbol=symbol,
                name=name,
                trades=ema_result.trades,
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
        _validate_staged_artifacts(staging_path, filenames, index, summary)
        _publish_staging(staging_path, output_path)
    except Exception:
        if staging_path.exists():
            shutil.rmtree(staging_path)
        raise
    return summary
