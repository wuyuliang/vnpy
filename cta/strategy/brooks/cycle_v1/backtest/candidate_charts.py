"""Render auditable multi-timeframe review cards for rejected candidates."""

from __future__ import annotations

import argparse
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import json
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from cta.strategy.brooks.scalp.config import load_config as load_scalp_config
from cta.strategy.brooks.scalp.metadata import MetadataBundle
from cta.strategy.brooks.scalp.report import (
    CHART_BACKGROUND,
    CHART_INK,
    CHART_MUTED,
    _draw_candlestick_panel,
)

from ..config import load_config
from ..instruments.sessions import (
    aggregate_completed_bars,
    aggregate_completed_daily_bars,
)
from ..legacy_adapters.scalp import load_normalized_symbol
from .scanner import build_symbol_replay_frames
from .timeframes import TimeframeSet


_CANDIDATE_COLUMNS = {
    "candidate_id",
    "symbol",
    "contract_code",
    "setup",
    "direction",
    "cycle",
    "signal_time",
    "active_time",
    "entry",
    "stop",
    "target",
}
_REJECTION_COLUMNS = {
    "candidate_id",
    "feature_asof",
    "reason_code",
    "detail",
}
_LONG_COLUMNS = {"feature_asof", "cycle", "reason"}
_OHLCV_COLUMNS = {"open", "high", "low", "close", "volume"}
_CARD_SIZE = (1680, 1240)
_PANEL_RECTS = (
    (42, 124, 1638, 452),
    (42, 472, 1638, 800),
    (42, 820, 1638, 1148),
)
DEFAULT_DATA_ROOT = Path("cta/data/origin/minute")


@dataclass(frozen=True)
class _ChartContext:
    daily: pd.DataFrame
    hourly: pd.DataFrame
    minute: pd.DataFrame
    long: pd.DataFrame


def _diagnose_candidates(
    candidates: pd.DataFrame,
    rejections: pd.DataFrame,
    long_frame: pd.DataFrame,
) -> pd.DataFrame:
    """Bind each candidate rejection to the latest visible large snapshot."""
    _require_columns(candidates, _CANDIDATE_COLUMNS, "candidates")
    _require_columns(rejections, _REJECTION_COLUMNS, "rejections")
    _require_columns(long_frame, _LONG_COLUMNS, "long cycle frame")

    selected_rejections = rejections.loc[
        rejections["candidate_id"].notna()
        & rejections["reason_code"].astype(str).eq("LARGE_CYCLE_UNAVAILABLE")
    ].copy()
    selected_rejections["feature_asof"] = selected_rejections[
        "feature_asof"
    ].map(_shanghai_timestamp)

    cycles = long_frame.copy()
    cycles["feature_asof"] = cycles["feature_asof"].map(_shanghai_timestamp)
    cycles = cycles.sort_values("feature_asof", kind="stable").reset_index(drop=True)

    ordered = candidates.copy()
    ordered["signal_time"] = ordered["signal_time"].map(_shanghai_timestamp)
    ordered["active_time"] = ordered["active_time"].map(_shanghai_timestamp)
    ordered = ordered.sort_values("signal_time", kind="stable").reset_index(drop=True)

    rows: list[dict[str, Any]] = []
    for sequence, candidate in enumerate(ordered.to_dict("records"), start=1):
        candidate_id = str(candidate["candidate_id"])
        rejection_rows = selected_rejections.loc[
            selected_rejections["candidate_id"].astype(str).eq(candidate_id)
        ]
        if len(rejection_rows) != 1:
            raise ValueError(
                f"candidate {candidate_id} requires one LARGE_CYCLE_UNAVAILABLE rejection"
            )
        rejection = rejection_rows.iloc[0]
        rejection_asof = pd.Timestamp(rejection["feature_asof"])
        visible = cycles.loc[cycles["feature_asof"].le(rejection_asof)]
        if visible.empty:
            raise ValueError(f"candidate {candidate_id} has no visible large snapshot")
        snapshot = visible.iloc[-1]
        large_cycle = str(snapshot["cycle"])
        if large_cycle not in {"UNAVAILABLE", "TRANSITION"}:
            raise ValueError(
                f"candidate {candidate_id} rejection conflicts with large cycle {large_cycle}"
            )
        large_reason = str(snapshot["reason"]).strip()
        if not large_reason or large_reason.lower() == "nan":
            raise ValueError(f"candidate {candidate_id} has no large-cycle reason")
        rows.append(
            {
                **candidate,
                "sequence": sequence,
                "rejection_feature_asof": rejection_asof,
                "rejection_code": str(rejection["reason_code"]),
                "rejection_detail": str(rejection["detail"]),
                "large_cycle": large_cycle,
                "large_reason": large_reason,
                "diagnostic_source": "recomputed_30min_snapshot",
            }
        )
    return pd.DataFrame(rows)


def _select_event_window(
    frame: pd.DataFrame,
    signal_time: object,
    *,
    before: int,
    after: int,
) -> pd.DataFrame:
    """Select a bounded review window around one candidate signal."""
    if before < 0 or after < 0:
        raise ValueError("candidate chart window counts must be nonnegative")
    _require_chart_frame(frame)
    if frame.empty:
        return frame.copy()
    signal = _shanghai_timestamp(signal_time)
    index = _shanghai_index(frame.index)
    ordered = frame.copy()
    ordered.index = index
    ordered = ordered.sort_index(kind="stable")
    position = int(ordered.index.searchsorted(signal, side="left"))
    position = min(position, len(ordered) - 1)
    start = max(0, position - before)
    stop = min(len(ordered), position + after + 1)
    return ordered.iloc[start:stop].copy()


def render_candidate_card(
    row: Mapping[str, Any] | pd.Series,
    *,
    daily: pd.DataFrame,
    hourly: pd.DataFrame,
    minute: pd.DataFrame,
) -> Image.Image:
    """Render one rejected candidate with daily, 60m, and 1m context."""
    signal = _shanghai_timestamp(row["signal_time"])
    active = _shanghai_timestamp(row["active_time"])
    panels = (
        (
            "Daily OHLC",
            _select_event_window(daily, signal, before=20, after=5),
            False,
        ),
        (
            "60min OHLC (visual context only)",
            _select_event_window(hourly, signal, before=36, after=12),
            True,
        ),
        (
            "1min OHLC",
            _select_event_window(minute, signal, before=80, after=40),
            True,
        ),
    )
    if any(frame.empty for _, frame, _ in panels):
        raise ValueError("candidate chart requires non-empty daily, 60min, and 1min panels")

    image = Image.new("RGBA", _CARD_SIZE, CHART_BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    direction = "LONG" if int(row["direction"]) == 1 else "SHORT"
    title = (
        f"AG candidate {int(row['sequence']):03d} | {row['contract_code']} | "
        f"{row['setup']} {direction} | {signal:%Y-%m-%d %H:%M}"
    )
    draw.text((42, 18), title, fill=CHART_INK, font=font)
    draw.text(
        (42, 39),
        (
            f"candidate_id={row['candidate_id']} | medium={row['cycle']} | "
            f"signal={signal:%H:%M} | active={active:%H:%M}"
        ),
        fill=CHART_MUTED,
        font=font,
    )
    draw.text(
        (42, 60),
        (
            f"entry={float(row['entry']):,.2f} | stop={float(row['stop']):,.2f} | "
            f"target={float(row['target']):,.2f} | {row['rejection_code']}: "
            f"{row['large_reason']}"
        ),
        fill="#9f2d24",
        font=font,
    )
    draw.text(
        (42, 81),
        "60min is review context; strategy large_tf=30min. Right of SIGNAL is POST-EVENT REVIEW ONLY.",
        fill="#8a5a12",
        font=font,
    )

    for rect, (label, frame, show_time) in zip(_PANEL_RECTS, panels, strict=True):
        _draw_candlestick_panel(
            draw,
            rect,
            frame,
            label=f"{label} ({len(frame)} nearby bars)",
            show_time=show_time,
        )
        _draw_candidate_overlay(
            image,
            rect,
            frame,
            signal=signal,
            active=active,
            entry=float(row["entry"]),
            stop=float(row["stop"]),
            target=float(row["target"]),
        )
        draw = ImageDraw.Draw(image)
    draw.text(
        (42, 1172),
        f"Large snapshot={row['large_cycle']} | reason={row['large_reason']} | no order and no fill",
        fill=CHART_MUTED,
        font=font,
    )
    return image.convert("RGB")


def generate_candidate_charts(
    report_dir: str | Path,
    *,
    data_root: str | Path = DEFAULT_DATA_ROOT,
) -> pd.DataFrame:
    """Write candidate cards, an index, and a large-cycle explanation."""
    report = Path(report_dir)
    summary_path = report / "summary.json"
    candidates_path = report / "candidates.csv"
    rejections_path = report / "rejections.csv"
    missing = [
        str(path.name)
        for path in (summary_path, candidates_path, rejections_path)
        if not path.is_file()
    ]
    if missing:
        raise FileNotFoundError("candidate chart report is missing: " + ",".join(missing))
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    candidates = pd.read_csv(candidates_path)
    rejections = pd.read_csv(rejections_path)
    if candidates.empty:
        raise ValueError("candidate chart report has no candidates")
    context = _load_chart_context(summary, Path(data_root))
    diagnosed = _diagnose_candidates(candidates, rejections, context.long)

    output_dir = report / "candidate_charts"
    output_dir.mkdir(parents=True, exist_ok=True)
    chart_paths: list[str] = []
    for row in diagnosed.to_dict("records"):
        signal = _shanghai_timestamp(row["signal_time"])
        direction = "LONG" if int(row["direction"]) == 1 else "SHORT"
        filename = (
            f"{int(row['sequence']):03d}_{signal:%Y%m%d_%H%M%S}_"
            f"{row['setup']}_{direction}.png"
        )
        path = output_dir / filename
        image = render_candidate_card(
            row,
            daily=context.daily,
            hourly=context.hourly,
            minute=context.minute,
        )
        image.save(path, format="PNG")
        chart_paths.append(str(path.relative_to(report)))
    index = diagnosed.copy()
    index["chart_path"] = chart_paths
    index.to_csv(output_dir / "index.csv", index=False)
    (report / "LARGE_CYCLE_UNAVAILABLE.md").write_text(
        _render_large_cycle_explanation(index, summary),
        encoding="utf-8",
    )
    return index


def _load_chart_context(
    summary: Mapping[str, Any],
    data_root: Path,
) -> _ChartContext:
    requested = tuple(str(value) for value in summary.get("requested_symbols", ()))
    loaded_symbols = tuple(str(value) for value in summary.get("loaded_symbols", ()))
    if len(requested) != 1 or requested != loaded_symbols:
        raise ValueError("candidate charts require one successfully loaded symbol")
    metadata_update = summary.get("execution_metadata_update", {})
    metadata_root = str(metadata_update.get("metadata_root", "")).strip()
    if not metadata_root:
        raise ValueError("candidate charts require the run metadata cache")
    start = date.fromisoformat(str(summary["requested_start"]))
    end = date.fromisoformat(str(summary["requested_end"]))
    metadata = MetadataBundle.load(metadata_root)
    loaded = load_normalized_symbol(
        symbol=requested[0],
        start=start,
        end=end,
        data_root=data_root,
        metadata=metadata,
        config=load_scalp_config(),
    )
    timeframe_values = summary.get("timeframes", {})
    timeframes = TimeframeSet.from_values(
        str(timeframe_values["long"]),
        str(timeframe_values["medium"]),
        str(timeframe_values["short"]),
    )
    if timeframes.long.minutes != 30:
        raise ValueError("candidate chart explanation requires strategy large_tf=30min")
    replay = build_symbol_replay_frames(
        loaded,
        config=load_config(),
        timeframes=timeframes,
    )
    daily = aggregate_completed_daily_bars(
        loaded.minute_bars,
        sessions=loaded.sessions,
    )
    hourly = aggregate_completed_bars(
        loaded.minute_bars,
        minutes=60,
        sessions=loaded.sessions,
    )
    return _ChartContext(
        daily=_as_chart_frame(daily),
        hourly=_as_chart_frame(hourly),
        minute=_as_chart_frame(loaded.minute_bars),
        long=replay.long,
    )


def _as_chart_frame(frame: pd.DataFrame) -> pd.DataFrame:
    _require_columns(frame, _OHLCV_COLUMNS | {"bar_end"}, "candidate chart bars")
    result = frame.loc[:, ["bar_end", "open", "high", "low", "close", "volume"]].copy()
    result["bar_end"] = result["bar_end"].map(_shanghai_timestamp)
    for column in _OHLCV_COLUMNS:
        result[column] = pd.to_numeric(result[column], errors="raise")
    return (
        result.sort_values("bar_end", kind="stable")
        .drop_duplicates("bar_end", keep="last")
        .set_index("bar_end")
    )


def _render_large_cycle_explanation(
    index: pd.DataFrame,
    summary: Mapping[str, Any],
) -> str:
    lines = [
        "# LARGE_CYCLE_UNAVAILABLE",
        "",
        "## Result",
        "",
        (
            f"This run produced {len(index)} candidates. Every candidate was retained "
            "in the audit, but no order was created because the configured 30min "
            "large cycle was `UNAVAILABLE` at its rejection event."
        ),
        "",
        "60min is review context only. The strategy decision used the latest "
        "completed 30min snapshot visible at `rejection_feature_asof`.",
        "",
        "## Exact Requirement",
        "",
        "`assess_direction_permission` checks the large cycle before confidence, "
        "direction agreement, sizing, or order creation:",
        "",
        "1. `UNAVAILABLE` or `TRANSITION` returns `LARGE_CYCLE_UNAVAILABLE`.",
        "2. A usable directional or trading-range state is required to continue.",
        "3. The medium cycle, minimum confidence, direction, Always-In state, risk, "
        "and sizing gates are evaluated only after the large-cycle availability gate.",
        "",
        "The causal market-cycle classifier can return `UNAVAILABLE` for three "
        "data-completeness reasons:",
        "",
        "- `INSUFFICIENT_CAUSAL_HISTORY`: fewer than 252 completed prior 30min bars "
        "are available for the configured percentile lookback.",
        "- `NON_FINITE_REQUIRED_STATE_INPUT`: at least one required cycle feature is "
        "missing or non-finite.",
        "- `INSUFFICIENT_PRESSURE_HISTORY`: 252 finite prior directional-pressure "
        "observations have not yet accumulated after the initial feature history.",
        "",
        "With all inputs finite, the first possible classified snapshot is the 505th "
        "completed 30min bar: 252 prior feature-history bars plus 252 prior pressure "
        "observations. `TRANSITION` remains blocked even after this warm-up because it "
        "does not provide stable large-cycle direction permission.",
        "",
        "## Candidates",
        "",
        "| # | Signal (Asia/Shanghai) | Active | Setup | Direction | Medium cycle | Large reason | Chart |",
        "|---:|---|---|---|---|---|---|---|",
    ]
    for row in index.to_dict("records"):
        signal = _shanghai_timestamp(row["signal_time"])
        active = _shanghai_timestamp(row["active_time"])
        direction = "LONG" if int(row["direction"]) == 1 else "SHORT"
        lines.append(
            f"| {int(row['sequence'])} | {signal:%Y-%m-%d %H:%M} | "
            f"{active:%Y-%m-%d %H:%M} | {row['setup']} | {direction} | "
            f"{row['cycle']} | `{row['large_reason']}` | "
            f"[{Path(str(row['chart_path'])).name}]({row['chart_path']}) |"
        )
    lines.extend(
        [
            "",
            "## Run Context",
            "",
            f"- Requested interval: `{summary['requested_start']}..{summary['requested_end']}`",
            f"- Requested and loaded symbol: `{summary['loaded_symbols'][0]}`",
            "- Chart panels: exchange-trade-date daily / completed 60min / raw 1min",
            "- Bars right of each SIGNAL marker: `POST-EVENT REVIEW ONLY`",
            "- Orders, fills, and round trips: `0 / 0 / 0`",
            "",
        ]
    )
    return "\n".join(lines)


def _draw_candidate_overlay(
    image: Image.Image,
    rect: tuple[int, int, int, int],
    frame: pd.DataFrame,
    *,
    signal: pd.Timestamp,
    active: pd.Timestamp,
    entry: float,
    stop: float,
    target: float,
) -> None:
    left, top, right, bottom = rect
    chart = (left + 68, top + 30, right - 18, bottom - 20)
    index = _shanghai_index(frame.index)
    signal_x = _event_x(index, signal, chart)
    active_x = _event_x(index, active, chart)
    if abs(active_x - signal_x) < 3:
        active_x = min(chart[2], signal_x + 4)

    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shade = ImageDraw.Draw(overlay)
    shade.rectangle(
        (signal_x, chart[1], chart[2], chart[3]),
        fill=(214, 166, 68, 32),
    )
    image.alpha_composite(overlay)

    draw = ImageDraw.Draw(image)
    _draw_dashed_vertical(draw, signal_x, chart[1], chart[3], "#2563eb")
    _draw_dashed_vertical(draw, active_x, chart[1], chart[3], "#d97706")
    draw.text((signal_x + 5, chart[1] + 4), "SIGNAL", fill="#2563eb")
    draw.text((active_x + 5, chart[1] + 18), "ACTIVE", fill="#d97706")
    draw.text(
        (max(signal_x + 58, chart[0] + 8), chart[1] + 4),
        "POST-EVENT REVIEW ONLY",
        fill="#8a5a12",
    )

    low = float(frame["low"].min())
    high = float(frame["high"].max())
    if high == low:
        low -= 1.0
        high += 1.0
    levels = (
        (entry, "ENTRY", "#2563eb"),
        (stop, "STOP", "#b42318"),
        (target, "TARGET", "#16835f"),
    )
    visible_levels: list[tuple[float, str, str]] = []
    for value, label, color in levels:
        if not low <= value <= high:
            continue
        y = chart[3] - (value - low) / (high - low) * (chart[3] - chart[1])
        visible_levels.append((y, label, color))
    visible_levels.sort(key=lambda item: item[0])
    label_positions = _separate_label_positions(
        [item[0] for item in visible_levels],
        top=float(chart[1]),
        bottom=float(chart[3] - 10),
        minimum_gap=14.0,
    )
    for (y, label, color), label_y in zip(
        visible_levels,
        label_positions,
        strict=True,
    ):
        draw.line((chart[0], y, chart[2], y), fill=color, width=1)
        draw.line(
            (chart[2] - 112, y, chart[2] - 100, label_y + 4),
            fill=color,
            width=1,
        )
        draw.text((chart[2] - 96, label_y), label, fill=color)


def _separate_label_positions(
    values: list[float],
    *,
    top: float,
    bottom: float,
    minimum_gap: float,
) -> list[float]:
    """Separate sorted vertical labels while keeping them inside a chart."""
    if bottom < top or minimum_gap < 0:
        raise ValueError("candidate level label bounds are invalid")
    if not values:
        return []
    ordered = sorted(float(value) for value in values)
    available_gap = (bottom - top) / max(len(ordered) - 1, 1)
    gap = min(minimum_gap, available_gap)
    result = [min(max(ordered[0], top), bottom)]
    for value in ordered[1:]:
        result.append(max(min(value, bottom), result[-1] + gap))
    overflow = result[-1] - bottom
    if overflow > 0:
        result = [value - overflow for value in result]
    if result[0] < top:
        shift = top - result[0]
        result = [value + shift for value in result]
    return result


def _event_x(
    index: pd.DatetimeIndex,
    timestamp: pd.Timestamp,
    chart: tuple[int, int, int, int],
) -> float:
    position = int(index.searchsorted(timestamp, side="left"))
    position = min(max(position, 0), len(index) - 1)
    return chart[0] + (position + 0.5) / len(index) * (chart[2] - chart[0])


def _draw_dashed_vertical(
    draw: ImageDraw.ImageDraw,
    x: float,
    top: int,
    bottom: int,
    color: str,
) -> None:
    for y in range(top, bottom, 10):
        draw.line((x, y, x, min(y + 5, bottom)), fill=color, width=2)


def _require_chart_frame(frame: pd.DataFrame) -> None:
    missing = sorted(_OHLCV_COLUMNS.difference(frame.columns))
    if missing:
        raise ValueError(f"candidate chart bars are missing: {','.join(missing)}")
    if not isinstance(frame.index, pd.DatetimeIndex):
        raise ValueError("candidate chart bars require a DatetimeIndex")


def _shanghai_index(index: pd.DatetimeIndex) -> pd.DatetimeIndex:
    if index.tz is None:
        return index.tz_localize("Asia/Shanghai")
    return index.tz_convert("Asia/Shanghai")


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} are missing columns: {','.join(missing)}")


def _shanghai_timestamp(value: object) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        raise ValueError("candidate chart timestamp is invalid")
    if parsed.tzinfo is None:
        return parsed.tz_localize("Asia/Shanghai")
    return parsed.tz_convert("Asia/Shanghai")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--report-dir", type=Path, required=True)
    parser.add_argument("--data-root", type=Path, default=DEFAULT_DATA_ROOT)
    args = parser.parse_args(argv)
    index = generate_candidate_charts(
        args.report_dir,
        data_root=args.data_root,
    )
    print(
        json.dumps(
            {
                "status": "COMPLETE",
                "report_dir": str(args.report_dir),
                "candidate_count": len(index),
                "image_count": len(index),
                "index": str(args.report_dir / "candidate_charts" / "index.csv"),
                "explanation": str(args.report_dir / "LARGE_CYCLE_UNAVAILABLE.md"),
            },
            ensure_ascii=False,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = ["generate_candidate_charts", "render_candidate_card"]
