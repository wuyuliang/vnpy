"""Render the single-symbol narrow-channel quick-validation chart."""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

CARD_SIZE = (2200, 1400)
BACKGROUND = "#f4efe5"
PANEL_BACKGROUND = "#fffdf7"
HEADER = "#213047"
INK = "#1f2937"
MUTED = "#64748b"
GRID = "#d8d2c4"
UP_CANDLE = "#c7362f"
DOWN_CANDLE = "#16835f"
UP_CHANNEL = "#e1f1e5"
DOWN_CHANNEL = "#f8e2dc"
EMA_COLORS = {"ema5": "#7c3aed", "ema10": "#0f766e", "ema20": "#ea580c"}
LONG_COLOR = "#2563eb"
SHORT_COLOR = "#d97706"
EXIT_COLOR = "#dc2626"
STOP_COLOR = "#111827"


@lru_cache(maxsize=16)
def _font(size: int) -> ImageFont.ImageFont:
    candidates = (
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    )
    for candidate in candidates:
        try:
            return ImageFont.truetype(candidate, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _positions(count: int, left: int, right: int) -> list[float]:
    if count <= 1:
        return [(left + right) / 2]
    return np.linspace(left, right, count).tolist()


def _price_y(value: float, low: float, high: float, top: int, bottom: int) -> float:
    span = max(high - low, 1e-12)
    return bottom - (value - low) / span * (bottom - top)


def _draw_dashed_line(
    draw: ImageDraw.ImageDraw,
    points: list[tuple[float, float]],
    *,
    fill: str,
    width: int = 2,
) -> None:
    for index in range(0, len(points) - 1, 2):
        draw.line((points[index], points[index + 1]), fill=fill, width=width)


def _draw_series(
    draw: ImageDraw.ImageDraw,
    values: pd.Series,
    xs: list[float],
    *,
    low: float,
    high: float,
    top: int,
    bottom: int,
    fill: str,
    width: int,
    dashed: bool = False,
) -> None:
    points: list[tuple[float, float]] = []
    for x, value in zip(xs, values, strict=True):
        if pd.isna(value):
            if len(points) >= 2:
                if dashed:
                    _draw_dashed_line(draw, points, fill=fill, width=width)
                else:
                    draw.line(points, fill=fill, width=width)
            points = []
            continue
        points.append((x, _price_y(float(value), low, high, top, bottom)))
    if len(points) >= 2:
        if dashed:
            _draw_dashed_line(draw, points, fill=fill, width=width)
        else:
            draw.line(points, fill=fill, width=width)


def _date_ticks(bars: pd.DataFrame, count: int = 10) -> list[int]:
    if bars.empty:
        return []
    return sorted(set(np.linspace(0, len(bars) - 1, min(count, len(bars))).astype(int)))


def select_chart_signal_markers(signals: pd.DataFrame) -> pd.DataFrame:
    """Return every long/short signal, including signals ignored while invested."""
    required = {"datetime", "close", "long_signal", "short_signal", "entry_status"}
    missing = required - set(signals.columns)
    if missing:
        raise ValueError(f"chart signals missing columns: {sorted(missing)}")
    markers = signals.loc[
        signals["long_signal"].astype(bool) | signals["short_signal"].astype(bool),
        ["datetime", "close", "long_signal", "short_signal", "entry_status"],
    ].copy()
    markers["datetime"] = pd.to_datetime(markers["datetime"], errors="raise").dt.normalize()
    markers["side"] = np.where(markers["long_signal"], "LONG", "SHORT")
    return markers[["datetime", "close", "side", "entry_status"]].reset_index(drop=True)


def _draw_price_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    bars: pd.DataFrame,
    *,
    title: str,
    ema_columns: tuple[str, ...] = (),
    stop_column: str | None = None,
    trades: pd.DataFrame | None = None,
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=12, fill=PANEL_BACKGROUND, outline=GRID, width=2)
    draw.text((left + 16, top + 10), title, fill=INK, font=_font(20))
    chart_left, chart_top = left + 72, top + 48
    chart_right, chart_bottom = right - 22, bottom - 48
    if bars.empty:
        draw.text((chart_left, chart_top), "No bars", fill=MUTED, font=_font(18))
        return

    values = [bars["low"].min(), bars["high"].max()]
    for column in ema_columns:
        if column in bars:
            values.extend([bars[column].min(), bars[column].max()])
    if stop_column and stop_column in bars and bars[stop_column].notna().any():
        values.extend([bars[stop_column].min(), bars[stop_column].max()])
    finite = np.asarray(values, dtype=float)
    finite = finite[np.isfinite(finite)]
    low = float(finite.min())
    high = float(finite.max())
    padding = max((high - low) * 0.06, high * 0.005)
    low -= padding
    high += padding
    xs = _positions(len(bars), chart_left, chart_right)
    bar_step = (chart_right - chart_left) / max(len(bars) - 1, 1)

    for index, row in bars.iterrows():
        state = str(row.get("weekly_state", "NEUTRAL"))
        fill = UP_CHANNEL if state == "UP_CHANNEL" else (
            DOWN_CHANNEL if state == "DOWN_CHANNEL" else None
        )
        if fill:
            x = xs[index]
            draw.rectangle(
                (
                    max(chart_left, x - bar_step / 2),
                    chart_top,
                    min(chart_right, x + bar_step / 2),
                    chart_bottom,
                ),
                fill=fill,
            )

    for fraction in (0.0, 0.25, 0.50, 0.75, 1.0):
        y = chart_top + fraction * (chart_bottom - chart_top)
        draw.line((chart_left, y, chart_right, y), fill=GRID, width=1)
        label = high - fraction * (high - low)
        draw.text((left + 6, y - 8), f"{label:.3f}", fill=MUTED, font=_font(12))

    candle_half = max(1, min(6, int(bar_step * 0.28)))
    for index, row in bars.iterrows():
        x = xs[index]
        color = UP_CANDLE if float(row["close"]) >= float(row["open"]) else DOWN_CANDLE
        y_high = _price_y(float(row["high"]), low, high, chart_top, chart_bottom)
        y_low = _price_y(float(row["low"]), low, high, chart_top, chart_bottom)
        y_open = _price_y(float(row["open"]), low, high, chart_top, chart_bottom)
        y_close = _price_y(float(row["close"]), low, high, chart_top, chart_bottom)
        draw.line((x, y_high, x, y_low), fill=color, width=1)
        body_top, body_bottom = sorted((y_open, y_close))
        draw.rectangle(
            (x - candle_half, body_top, x + candle_half, max(body_top + 1, body_bottom)),
            fill=color,
        )

    for column in ema_columns:
        if column in bars:
            _draw_series(
                draw,
                bars[column],
                xs,
                low=low,
                high=high,
                top=chart_top,
                bottom=chart_bottom,
                fill=EMA_COLORS[column],
                width=2,
            )
    if stop_column and stop_column in bars:
        _draw_series(
            draw,
            bars[stop_column],
            xs,
            low=low,
            high=high,
            top=chart_top,
            bottom=chart_bottom,
            fill=STOP_COLOR,
            width=2,
            dashed=True,
        )

    date_to_index = {
        pd.Timestamp(date).normalize(): index for index, date in enumerate(bars["datetime"])
    }
    if {"long_signal", "short_signal", "entry_status"}.issubset(bars.columns):
        for marker in select_chart_signal_markers(bars).itertuples(index=False):
            marker_index = date_to_index.get(pd.Timestamp(marker.datetime).normalize())
            if marker_index is None:
                continue
            x = xs[marker_index]
            y = _price_y(float(marker.close), low, high, chart_top, chart_bottom)
            color = LONG_COLOR if marker.side == "LONG" else SHORT_COLOR
            diamond = [(x, y - 7), (x + 7, y), (x, y + 7), (x - 7, y)]
            draw.line(diamond + [diamond[0]], fill=color, width=2)
    if trades is not None and not trades.empty:
        for trade in trades.itertuples(index=False):
            entry_date = pd.Timestamp(trade.entry_date).normalize()
            entry_index = date_to_index.get(entry_date)
            if entry_index is not None:
                x = xs[entry_index]
                y = _price_y(float(trade.entry_fill), low, high, chart_top, chart_bottom)
                if trade.side == "LONG":
                    draw.polygon([(x, y - 12), (x - 9, y + 8), (x + 9, y + 8)], fill=LONG_COLOR)
                    label = f"L{trade.trade_id}"
                else:
                    draw.polygon([(x, y + 12), (x - 9, y - 8), (x + 9, y - 8)], fill=SHORT_COLOR)
                    label = f"S{trade.trade_id}"
                draw.text((x + 10, y - 14), label, fill=INK, font=_font(12))
            if str(trade.status) == "CLOSED" and pd.notna(trade.exit_date):
                exit_date = pd.Timestamp(trade.exit_date).normalize()
                exit_index = date_to_index.get(exit_date)
                if exit_index is not None:
                    x = xs[exit_index]
                    y = _price_y(float(trade.exit_fill), low, high, chart_top, chart_bottom)
                    draw.line((x - 8, y - 8, x + 8, y + 8), fill=EXIT_COLOR, width=3)
                    draw.line((x - 8, y + 8, x + 8, y - 8), fill=EXIT_COLOR, width=3)

    for index in _date_ticks(bars):
        x = xs[index]
        label = pd.Timestamp(bars.iloc[index]["datetime"]).strftime("%Y-%m")
        draw.line((x, chart_bottom, x, chart_bottom + 5), fill=MUTED, width=1)
        draw.text((x - 26, chart_bottom + 10), label, fill=MUTED, font=_font(11))


def _draw_volume_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    bars: pd.DataFrame,
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=12, fill=PANEL_BACKGROUND, outline=GRID, width=2)
    draw.text((left + 16, top + 10), "Daily Volume", fill=INK, font=_font(20))
    chart_left, chart_top = left + 72, top + 45
    chart_right, chart_bottom = right - 22, bottom - 22
    if bars.empty:
        return
    xs = _positions(len(bars), chart_left, chart_right)
    step = (chart_right - chart_left) / max(len(bars) - 1, 1)
    half = max(1, min(5, int(step * 0.28)))
    maximum = max(float(bars["volume"].max()), 1.0)
    for index, row in bars.iterrows():
        x = xs[index]
        height = float(row["volume"]) / maximum * (chart_bottom - chart_top)
        color = UP_CANDLE if float(row["close"]) >= float(row["open"]) else DOWN_CANDLE
        draw.rectangle((x - half, chart_bottom - height, x + half, chart_bottom), fill=color)


def render_narrow_channel_chart(
    *,
    signals: pd.DataFrame,
    weekly: pd.DataFrame,
    trades: pd.DataFrame,
    summary: dict[str, Any],
) -> Image.Image:
    """Render weekly channel, daily EMA/trades/stops, and volume in one PNG."""
    if signals.empty:
        raise ValueError("signals must not be empty")
    end = pd.Timestamp(signals["datetime"].max()).normalize()
    start = end - pd.DateOffset(years=2)
    daily_window = signals.loc[signals["datetime"] >= start].copy().reset_index(drop=True)
    weekly_window = weekly.loc[weekly["datetime"] >= start].copy().reset_index(drop=True)
    image = Image.new("RGB", CARD_SIZE, BACKGROUND)
    draw = ImageDraw.Draw(image)
    draw.rectangle((0, 0, CARD_SIZE[0], 96), fill=HEADER)
    draw.text(
        (30, 14),
        "159915.SZ 易方达创业板ETF 周线窄通道多空快速验证",
        fill="#fff8e8",
        font=_font(28),
    )
    long_summary = summary["long"]
    short_summary = summary["short"]
    subtitle = (
        f"research_only | {signals['datetime'].min().date()}..{end.date()} | "
        f"LONG trades={long_summary['completed_trade_count']} R={long_summary['total_net_r']:.2f} "
        f"{long_summary['verdict']} | SHORT trades={short_summary['completed_trade_count']} "
        f"R={short_summary['total_net_r']:.2f} {short_summary['verdict']}"
    )
    draw.text((30, 56), subtitle, fill="#dbe7ef", font=_font(15))
    draw.rectangle((1380, 18, 2155, 78), outline="#91a4ba", width=1)
    legend = (
        "UP/DOWN channel  EMA5/10/20  ◇signal  ▲entry  ×exit  --stop"
    )
    draw.text((1400, 39), legend, fill="#fff8e8", font=_font(13))

    _draw_price_panel(
        draw,
        (35, 116, 2165, 485),
        weekly_window,
        title="Weekly K | confirmed 6-week narrow channels",
    )
    _draw_price_panel(
        draw,
        (35, 505, 2165, 1120),
        daily_window,
        title="Daily K | EMA5 / EMA10 / EMA20 | entries, exits and active stop",
        ema_columns=("ema5", "ema10", "ema20"),
        stop_column="active_stop",
        trades=trades,
    )
    _draw_volume_panel(draw, (35, 1140, 2165, 1370), daily_window)
    return image


def save_chart_atomic(image: Image.Image, path: Path) -> None:
    """Save one PNG through a sibling temporary file and atomic rename."""
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp")
    try:
        image.save(temporary, format="PNG")
        temporary.replace(path)
    finally:
        temporary.unlink(missing_ok=True)
