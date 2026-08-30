"""Render auditable multi-timeframe review cards for rejected candidates."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from cta.strategy.brooks.scalp.report import (
    CHART_BACKGROUND,
    CHART_INK,
    CHART_MUTED,
    _draw_candlestick_panel,
)


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
    for value, label, color in levels:
        if not low <= value <= high:
            continue
        y = chart[3] - (value - low) / (high - low) * (chart[3] - chart[1])
        draw.line((chart[0], y, chart[2], y), fill=color, width=1)
        draw.text((chart[2] - 92, max(chart[1], y - 12)), label, fill=color)


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


__all__ = ["render_candidate_card"]
