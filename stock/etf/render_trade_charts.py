"""Render one weekly/daily OHLCV review card per traded ETF."""

from __future__ import annotations

import argparse
import json
import math
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from numbers import Real
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

OHLCV_COLUMNS = ["datetime", "open", "high", "low", "close", "volume"]
INDEX_COLUMNS = [
    "rank",
    "symbol",
    "name",
    "fund_type",
    "realized_pnl",
    "trade_count",
    "buy_count",
    "sell_count",
    "first_trade_date",
    "last_trade_date",
    "is_open",
    "render_status",
    "image_path",
]
CARD_WIDTH = 1680
CARD_HEIGHT = 1000
STATE_CARD_HEIGHT = 1120
LEFT_WIDTH = 430
HEADER_HEIGHT = 82
BACKGROUND = "#f7f4ed"
PANEL_BACKGROUND = "#fffdf7"
HEADER_BACKGROUND = "#222f3e"
INK = "#1f2933"
MUTED = "#64748b"
GRID = "#d8d2c4"
UP_COLOR = "#c7362f"
DOWN_COLOR = "#16835f"
FLAT_COLOR = "#6b7280"
BUY_COLOR = "#2563eb"
SELL_COLOR = "#d97706"
SCORE_1D_COLOR = "#2563eb"
SCORE_3D_COLOR = "#d97706"
PERFORMANCE_KEYS = (
    "total_return",
    "max_drawdown",
    "sharpe",
    "annual_one_way_turnover",
)
TARGET_WEIGHT_REASON = re.compile(
    r"^target_weight_(?:0(?:\.0)?|0\.5|1(?:\.0)?)_to_(0(?:\.0)?|0\.5|1(?:\.0)?)$"
)


def normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    """Return valid, sorted, duplicate-free OHLCV rows."""
    missing = set(OHLCV_COLUMNS) - set(frame.columns)
    if missing:
        raise ValueError(f"bar data missing columns: {sorted(missing)}")
    bars = frame[OHLCV_COLUMNS].copy()
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
    for column in OHLCV_COLUMNS[1:]:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars = bars.dropna(subset=OHLCV_COLUMNS)
    bars = bars.loc[
        (bars[["open", "high", "low", "close"]] > 0).all(axis=1) & (bars["volume"] >= 0)
    ]
    return (
        bars.sort_values("datetime")
        .drop_duplicates("datetime", keep="last")
        .reset_index(drop=True)
    )


def aggregate_weekly_bars(daily: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily bars into Friday-ending OHLCV bars."""
    bars = normalize_bars(daily)
    if bars.empty:
        return pd.DataFrame(columns=OHLCV_COLUMNS)
    weekly = (
        bars.set_index("datetime")
        .resample("W-FRI")
        .agg(
            open=("open", "first"),
            high=("high", "max"),
            low=("low", "min"),
            close=("close", "last"),
            volume=("volume", "sum"),
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return normalize_bars(weekly)


def select_daily_window(
    daily: pd.DataFrame,
    report_start: object,
    report_end: object,
    *,
    pre_bars: int = 20,
) -> pd.DataFrame:
    """Select report bars plus a fixed number of prior valid trading bars."""
    if pre_bars < 0:
        raise ValueError("pre_bars must not be negative")
    bars = normalize_bars(daily)
    start = pd.Timestamp(report_start)
    end = pd.Timestamp(report_end)
    if start > end:
        raise ValueError("report_start must not be after report_end")
    first_report_position = int(bars["datetime"].searchsorted(start, side="left"))
    window_start = max(0, first_report_position - pre_bars)
    return bars.iloc[window_start:].loc[bars["datetime"] <= end].reset_index(drop=True)


def build_trade_markers(trades: pd.DataFrame, *, weekly: bool) -> pd.DataFrame:
    """Build numbered markers, merging same-day trades in the same direction."""
    required = {"datetime", "side", "fill_price", "quantity"}
    missing = required - set(trades.columns)
    if missing:
        raise ValueError(f"trade data missing columns: {sorted(missing)}")
    markers = (
        trades.copy().reset_index(drop=False).rename(columns={"index": "source_order"})
    )
    markers["datetime"] = pd.to_datetime(markers["datetime"], errors="coerce")
    markers["side"] = markers["side"].astype(str).str.lower()
    markers["fill_price"] = pd.to_numeric(markers["fill_price"], errors="coerce")
    markers["quantity"] = pd.to_numeric(markers["quantity"], errors="coerce")
    markers = markers.loc[
        markers["datetime"].notna()
        & markers["side"].isin(["buy", "sell"])
        & markers["fill_price"].notna()
        & markers["quantity"].notna()
    ].copy()
    markers = markers.sort_values(["datetime", "source_order"], kind="stable")
    side_number = markers.groupby("side", sort=False).cumcount() + 1
    markers["label"] = markers["side"].map(
        {"buy": "B", "sell": "S"}
    ) + side_number.astype(str)
    if "primary_reason" in markers and not markers.empty:
        targets = markers["primary_reason"].map(_target_weight_suffix)
        markers["label"] += targets
    markers["datetime"] = markers["datetime"].dt.normalize()

    merged_rows: list[dict[str, Any]] = []
    for (_, side), group in markers.groupby(["datetime", "side"], sort=False):
        quantity = float(group["quantity"].sum())
        if quantity:
            fill_price = float(
                (group["fill_price"] * group["quantity"]).sum() / quantity
            )
        else:
            fill_price = float(group["fill_price"].mean())
        merged_rows.append(
            {
                "datetime": group["datetime"].iloc[0],
                "side": side,
                "fill_price": fill_price,
                "quantity": quantity,
                "label": "/".join(group["label"]),
                "source_order": int(group["source_order"].min()),
            }
        )
    markers = pd.DataFrame(
        merged_rows,
        columns=["datetime", "side", "fill_price", "quantity", "label", "source_order"],
    ).sort_values(["datetime", "source_order"], kind="stable")
    if markers.empty:
        markers["datetime"] = pd.to_datetime(markers["datetime"])
        return markers[
            ["datetime", "side", "fill_price", "quantity", "label", "source_order"]
        ]
    if weekly:
        markers["datetime"] = (
            markers["datetime"].dt.to_period("W-FRI").dt.end_time.dt.normalize()
        )
    return markers[
        ["datetime", "side", "fill_price", "quantity", "label", "source_order"]
    ].reset_index(drop=True)


def _target_weight_suffix(reason: object) -> str:
    if not isinstance(reason, str):
        return ""
    match = TARGET_WEIGHT_REASON.fullmatch(reason)
    if match is None:
        return ""
    target = float(match.group(1))
    return f"→{target:.0%}"


def make_chart_filename(rank: int, symbol: str, name: str) -> str:
    """Return a stable rank-prefixed filename safe on common filesystems."""
    safe_symbol = re.sub(r"[^\w-]+", "_", symbol, flags=re.UNICODE).strip("_")
    safe_name = re.sub(r"[^\w-]+", "_", name, flags=re.UNICODE).strip("_")
    return f"{rank:04d}_{safe_symbol or 'UNKNOWN'}_{safe_name or safe_symbol or 'UNKNOWN'}.png"


class _Fonts:
    def __init__(self) -> None:
        self.title = _load_font(25)
        self.subtitle = _load_font(12)
        self.heading = _load_font(17)
        self.body = _load_font(13)
        self.small = _load_font(11)
        self.tiny = _load_font(10)


@lru_cache(maxsize=32)
def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/STHeiti Light.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def render_symbol_card(
    *,
    symbol: str,
    name: str,
    fund_type: str,
    daily_bars: pd.DataFrame,
    weekly_bars: pd.DataFrame,
    trades: pd.DataFrame,
    latest_position: pd.Series | dict[str, Any] | None,
    report_start: object,
    report_end: object,
    review_label: str = "ETF Rotation Trade Review",
    performance: Mapping[str, object] | None = None,
    daily_state_scores: pd.DataFrame | None = None,
    weekly_state_scores: pd.DataFrame | None = None,
) -> Image.Image:
    """Render one ETF review card with weekly/daily candlesticks and volume."""
    if not isinstance(review_label, str) or not review_label.strip():
        raise ValueError("review_label must be a non-empty string")
    review_label = review_label.strip()
    has_state_scores = any(
        frame is not None and not frame.empty
        for frame in (daily_state_scores, weekly_state_scores)
    )
    card_height = STATE_CARD_HEIGHT if has_state_scores else CARD_HEIGHT
    image = Image.new("RGB", (CARD_WIDTH, card_height), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts = _Fonts()
    start = pd.Timestamp(report_start)
    end = pd.Timestamp(report_end)

    draw.rectangle((0, 0, CARD_WIDTH, HEADER_HEIGHT), fill=HEADER_BACKGROUND)
    draw.text((24, 14), f"{symbol}  {name}", fill="#fff8e8", font=fonts.title)
    draw.text(
        (24, 51),
        f"{fund_type}  |  {review_label}  |  {start.date()} to {end.date()}",
        fill="#dbe7ef",
        font=fonts.subtitle,
    )

    _draw_metadata(
        draw,
        fonts,
        symbol,
        name,
        fund_type,
        trades,
        latest_position,
        card_height,
        performance,
    )
    chart_left = LEFT_WIDTH + 28
    chart_right = CARD_WIDTH - 24
    chart_top = HEADER_HEIGHT + 22
    panel_gap = 18
    panel_height = (card_height - chart_top - 24 - panel_gap) // 2
    weekly_markers = build_trade_markers(trades, weekly=True)
    daily_markers = build_trade_markers(trades, weekly=False)
    _draw_chart_panel(
        draw,
        (chart_left, chart_top, chart_right, chart_top + panel_height),
        "Weekly K  |  full warmup context",
        normalize_bars(weekly_bars),
        weekly_markers,
        fonts,
        state_scores=weekly_state_scores,
    )
    daily_top = chart_top + panel_height + panel_gap
    _draw_chart_panel(
        draw,
        (chart_left, daily_top, chart_right, daily_top + panel_height),
        "Daily K  |  report window + 20 prior bars",
        normalize_bars(daily_bars),
        daily_markers,
        fonts,
        state_scores=daily_state_scores,
        draw_scores=daily_state_scores is not None and not daily_state_scores.empty,
    )
    return image


def _draw_metadata(
    draw: ImageDraw.ImageDraw,
    fonts: _Fonts,
    symbol: str,
    name: str,
    fund_type: str,
    trades: pd.DataFrame,
    latest_position: pd.Series | dict[str, Any] | None,
    card_height: int = CARD_HEIGHT,
    performance: Mapping[str, object] | None = None,
) -> None:
    left = 16
    top = HEADER_HEIGHT + 14
    right = LEFT_WIDTH
    bottom = card_height - 24
    draw.rounded_rectangle(
        (left, top, right, bottom), radius=14, fill="#fffaf0", outline="#e2d6bd"
    )
    x = 24
    y = top + 14
    normalized = trades.copy()
    normalized["datetime"] = pd.to_datetime(normalized["datetime"])
    buys = normalized.loc[normalized["side"] == "buy"]
    sells = normalized.loc[normalized["side"] == "sell"]
    buy_amount = float((buys["fill_price"] * buys["quantity"]).sum())
    sell_amount = float((sells["fill_price"] * sells["quantity"]).sum())
    commission = float(normalized.get("commission", pd.Series(dtype=float)).sum())
    slippage = float(normalized.get("slippage_cost", pd.Series(dtype=float)).sum())
    realized = float(normalized.get("realized_pnl", pd.Series(dtype=float)).sum())
    exit_reasons = sorted(
        set(sells.get("primary_reason", pd.Series(dtype=str)).dropna().astype(str))
    )

    draw.text((x, y), "Instrument", fill=INK, font=fonts.heading)
    y += 28
    instrument_lines = [symbol, name, fund_type]
    y = _draw_lines(draw, x, y, instrument_lines, fonts.body)
    y += 12
    draw.text((x, y), "Trades", fill=INK, font=fonts.heading)
    y += 28
    trade_lines = [
        f"first: {normalized['datetime'].min().date()}",
        f"last:  {normalized['datetime'].max().date()}",
        f"buys / sells / total: {len(buys)} / {len(sells)} / {len(normalized)}",
        f"buy amount:  {buy_amount:,.2f}",
        f"sell amount: {sell_amount:,.2f}",
    ]
    y = _draw_lines(draw, x, y, trade_lines, fonts.body)
    y += 12
    draw.text((x, y), "Cost / PnL", fill=INK, font=fonts.heading)
    y += 28
    pnl_color = UP_COLOR if realized >= 0 else DOWN_COLOR
    cost_lines = [
        (f"commission: {commission:,.2f}", INK),
        (f"slippage:   {slippage:,.2f}", INK),
        (f"realized PnL: {realized:,.2f}", pnl_color),
    ]
    for text, color in cost_lines:
        draw.text((x, y), text, fill=color, font=fonts.body)
        y += 21
    y += 12
    draw.text((x, y), "Latest Position", fill=INK, font=fonts.heading)
    y += 28
    if latest_position is None:
        position_lines = ["status: closed", "quantity: 0"]
    else:
        quantity = int(_mapping_value(latest_position, "quantity", 0))
        average_price = float(_mapping_value(latest_position, "average_price", 0.0))
        position_lines = [
            "status: open",
            f"quantity: {quantity:,}",
            f"average price: {average_price:.4f}",
        ]
    y = _draw_lines(draw, x, y, position_lines, fonts.body)
    validated_performance = _validate_performance(performance)
    if validated_performance is not None:
        y += 12
        draw.text((x, y), "Performance", fill=INK, font=fonts.heading)
        y += 28
        performance_lines = [
            f"total return: {validated_performance['total_return']:.2%}",
            f"max drawdown: {validated_performance['max_drawdown']:.2%}",
            f"Sharpe: {validated_performance['sharpe']:.2f}",
            (
                "annual one-way turnover: "
                f"{validated_performance['annual_one_way_turnover']:.2f}x"
            ),
        ]
        y = _draw_lines(draw, x, y, performance_lines, fonts.body)
        target_distribution = validated_performance.get("target_distribution")
        if isinstance(target_distribution, Mapping):
            y += 10
            draw.text((x, y), "Target Distribution", fill=INK, font=fonts.heading)
            y += 28
            target_lines = [
                (f"target {target}: {entry['days']} days ({entry['fraction']:.1%})")
                for target, entry in target_distribution.items()
            ]
            y = _draw_lines(draw, x, y, target_lines, fonts.body)
    y += 12
    draw.text((x, y), "Exit Reasons", fill=INK, font=fonts.heading)
    y += 28
    reason_lines = exit_reasons or ["none"]
    _draw_lines(draw, x, y, reason_lines, fonts.body)


def _validate_performance(
    performance: Mapping[str, object] | None,
) -> dict[str, Any] | None:
    if performance is None:
        return None
    if not isinstance(performance, Mapping):
        raise ValueError("performance must be a mapping")
    if not performance:
        return None
    missing = set(PERFORMANCE_KEYS) - set(performance)
    if missing:
        raise ValueError(f"performance missing keys: {sorted(missing)}")

    validated: dict[str, Any] = dict(performance)
    for key in PERFORMANCE_KEYS:
        value = performance[key]
        if (
            isinstance(value, bool)
            or not isinstance(value, Real)
            or not math.isfinite(float(value))
        ):
            raise ValueError(f"performance[{key!r}] must be a finite number")
        validated[key] = float(value)

    if "target_distribution" in performance:
        distribution = performance["target_distribution"]
        if not isinstance(distribution, Mapping):
            raise ValueError("performance['target_distribution'] must be a mapping")
        normalized_distribution: dict[str, dict[str, int | float]] = {}
        for target in ("0", "0.5", "1"):
            entry = distribution.get(target, {"days": 0, "fraction": 0.0})
            if not isinstance(entry, Mapping):
                raise ValueError(
                    f"performance target_distribution[{target!r}] must be a mapping"
                )
            if "days" not in entry or "fraction" not in entry:
                raise ValueError(
                    "performance target_distribution entries require days and fraction"
                )
            days = entry["days"]
            fraction = entry["fraction"]
            if (
                isinstance(days, bool)
                or not isinstance(days, Real)
                or not math.isfinite(float(days))
                or float(days) < 0
                or not float(days).is_integer()
            ):
                raise ValueError(
                    "performance target_distribution days must be a non-negative integer"
                )
            if (
                isinstance(fraction, bool)
                or not isinstance(fraction, Real)
                or not math.isfinite(float(fraction))
                or not 0.0 <= float(fraction) <= 1.0
            ):
                raise ValueError(
                    "performance target_distribution fraction must be between 0 and 1"
                )
            normalized_distribution[target] = {
                "days": int(days),
                "fraction": float(fraction),
            }
        validated["target_distribution"] = normalized_distribution
    return validated


def _mapping_value(
    value: pd.Series | dict[str, Any], key: str, default: object
) -> object:
    if isinstance(value, pd.Series):
        return value.get(key, default)
    return value.get(key, default)


def _metadata_text(value: object, fallback: str) -> tuple[str, bool]:
    if value is None or pd.isna(value):
        return fallback, True
    text = str(value).strip()
    return (text, False) if text else (fallback, True)


def _draw_lines(
    draw: ImageDraw.ImageDraw,
    x: int,
    y: int,
    lines: list[str],
    font: ImageFont.ImageFont,
) -> int:
    for line in lines:
        draw.text((x, y), str(line), fill=INK, font=font)
        y += 21
    return y


def _draw_chart_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    title: str,
    bars: pd.DataFrame,
    markers: pd.DataFrame,
    fonts: _Fonts,
    *,
    state_scores: pd.DataFrame | None = None,
    draw_scores: bool = False,
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=14, fill=PANEL_BACKGROUND, outline="#e2d6bd")
    draw.text((left + 14, top + 10), title, fill=INK, font=fonts.heading)
    if bars.empty:
        draw.text((left + 20, top + 62), "No OHLCV data", fill=MUTED, font=fonts.body)
        return

    states = _normalize_state_scores(state_scores, require_scores=draw_scores)
    plot_left = left + 56
    plot_right = right - 18
    price_top = top + 40
    score_top = bottom - 174 if draw_scores else None
    score_bottom = bottom - 106 if draw_scores else None
    price_bottom = score_top - 18 if score_top is not None else bottom - 106
    volume_top = bottom - 88
    volume_bottom = bottom - 24
    price_min = float(bars["low"].min())
    price_max = float(bars["high"].max())
    price_padding = max((price_max - price_min) * 0.06, price_max * 0.002)
    price_min -= price_padding
    price_max += price_padding
    volume_max = max(float(bars["volume"].max()), 1.0)

    def price_y(value: float) -> int:
        ratio = (price_max - value) / (price_max - price_min)
        return int(price_top + ratio * (price_bottom - price_top))

    count = len(bars)
    plot_width = plot_right - plot_left
    step = plot_width / max(count, 1)
    _draw_state_backgrounds(
        draw,
        bars,
        states,
        plot_left,
        price_top,
        plot_right,
        price_bottom,
        step,
    )
    for grid_index in range(5):
        y = int(price_top + grid_index * (price_bottom - price_top) / 4)
        draw.line((plot_left, y, plot_right, y), fill=GRID, width=1)
    draw.rectangle((plot_left, price_top, plot_right, price_bottom), outline="#cabfa9")
    draw.rectangle(
        (plot_left, volume_top, plot_right, volume_bottom), outline="#cabfa9"
    )
    draw.text(
        (left + 4, price_top - 2), f"{price_max:.4f}", fill=MUTED, font=fonts.tiny
    )
    draw.text(
        (left + 4, price_bottom - 10), f"{price_min:.4f}", fill=MUTED, font=fonts.tiny
    )
    draw.text((left + 6, volume_top + 3), "Vol", fill=MUTED, font=fonts.tiny)

    candle_width = max(2, min(11, int(step * 0.62)))
    x_by_date: dict[pd.Timestamp, int] = {}
    for index, row in bars.iterrows():
        x = int(plot_left + (index + 0.5) * step)
        date = pd.Timestamp(row["datetime"]).normalize()
        x_by_date[date] = x
        open_price = float(row["open"])
        close_price = float(row["close"])
        color = (
            UP_COLOR
            if close_price > open_price
            else DOWN_COLOR
            if close_price < open_price
            else FLAT_COLOR
        )
        draw.line(
            (x, price_y(float(row["high"])), x, price_y(float(row["low"]))),
            fill=color,
            width=1,
        )
        body_top = min(price_y(open_price), price_y(close_price))
        body_bottom = max(price_y(open_price), price_y(close_price))
        if body_bottom == body_top:
            draw.line(
                (x - candle_width // 2, body_top, x + candle_width // 2, body_top),
                fill=color,
                width=2,
            )
        else:
            draw.rectangle(
                (x - candle_width // 2, body_top, x + candle_width // 2, body_bottom),
                fill=color,
                outline=color,
            )
        volume_height = int(
            float(row["volume"]) / volume_max * (volume_bottom - volume_top - 3)
        )
        draw.rectangle(
            (
                x - candle_width // 2,
                volume_bottom - volume_height,
                x + candle_width // 2,
                volume_bottom,
            ),
            fill=color,
        )

    if score_top is not None and score_bottom is not None:
        _draw_score_track(
            draw,
            bars,
            states,
            plot_left,
            plot_right,
            score_top,
            score_bottom,
            step,
            fonts,
        )
    _draw_trade_markers(
        draw, markers, x_by_date, price_y, price_top, price_bottom, fonts
    )
    tick_indexes = sorted({0, max(0, count // 2), count - 1})
    for index in tick_indexes:
        row = bars.iloc[index]
        x = int(plot_left + (index + 0.5) * step)
        label = pd.Timestamp(row["datetime"]).strftime("%Y-%m-%d")
        anchor = "la" if index == 0 else "ra" if index == count - 1 else "ma"
        draw.text((x, bottom - 10), label, fill=MUTED, font=fonts.tiny, anchor=anchor)
    _draw_state_legend(draw, states, left, top, right, fonts)
    if score_top is not None:
        _draw_score_legend(draw, plot_left, price_bottom, fonts)


def _normalize_state_scores(
    state_scores: pd.DataFrame | None,
    *,
    require_scores: bool,
) -> pd.DataFrame | None:
    if state_scores is None or state_scores.empty:
        return None
    if not isinstance(state_scores, pd.DataFrame):
        raise ValueError("state_scores must be a DataFrame")
    required = {"datetime", "state_3d", "state_color"}
    if require_scores:
        required.update({"score_1d", "score_3d"})
    missing = required - set(state_scores.columns)
    if missing:
        raise ValueError(f"state_scores missing columns: {sorted(missing)}")

    states = state_scores.copy()
    states["datetime"] = pd.to_datetime(
        states["datetime"], errors="coerce"
    ).dt.normalize()
    if require_scores:
        for column in ("score_1d", "score_3d"):
            states[column] = pd.to_numeric(states[column], errors="coerce")
    return states


def _draw_state_backgrounds(
    draw: ImageDraw.ImageDraw,
    bars: pd.DataFrame,
    states: pd.DataFrame | None,
    plot_left: int,
    price_top: int,
    plot_right: int,
    price_bottom: int,
    step: float,
) -> None:
    if states is None:
        return
    color_by_date: dict[pd.Timestamp, str] = {}
    for _, row in states.iterrows():
        date = row["datetime"]
        color = row["state_color"]
        if pd.isna(date) or pd.isna(color) or not str(color).strip():
            continue
        color_by_date[pd.Timestamp(date).normalize()] = str(color)

    segments: list[tuple[int, int, str]] = []
    segment_start = 0
    segment_color: str | None = None
    for index, row in bars.iterrows():
        date = pd.Timestamp(row["datetime"]).normalize()
        color = color_by_date.get(date)
        if color == segment_color:
            continue
        if segment_color is not None:
            segments.append((segment_start, index - 1, segment_color))
        segment_start = index
        segment_color = color
    if segment_color is not None:
        segments.append((segment_start, len(bars) - 1, segment_color))

    for start, end, color in segments:
        segment_left = max(plot_left, int(plot_left + start * step))
        segment_right = min(plot_right, int(plot_left + (end + 1) * step))
        draw.rectangle(
            (segment_left, price_top, segment_right, price_bottom),
            fill=color,
        )


def _draw_score_track(
    draw: ImageDraw.ImageDraw,
    bars: pd.DataFrame,
    states: pd.DataFrame | None,
    plot_left: int,
    plot_right: int,
    score_top: int,
    score_bottom: int,
    step: float,
    fonts: _Fonts,
) -> None:
    draw.rectangle((plot_left, score_top, plot_right, score_bottom), outline="#cabfa9")

    def score_y(value: float) -> int:
        return int(score_top + (3.0 - value) / 6.0 * (score_bottom - score_top))

    for reference in (-2, -1, 0, 1, 2):
        y = score_y(float(reference))
        color = MUTED if reference == 0 else GRID
        draw.line((plot_left, y, plot_right, y), fill=color, width=1)
        draw.text(
            (plot_left - 5, y),
            str(reference),
            fill=MUTED,
            font=fonts.tiny,
            anchor="rm",
        )
    draw.text(
        (plot_left - 5, score_top),
        "3",
        fill=MUTED,
        font=fonts.tiny,
        anchor="ra",
    )
    draw.text(
        (plot_left - 5, score_bottom),
        "-3",
        fill=MUTED,
        font=fonts.tiny,
        anchor="rd",
    )
    if states is None:
        return

    scores_by_date: dict[pd.Timestamp, tuple[object, object]] = {}
    for _, row in states.iterrows():
        date = row["datetime"]
        if pd.isna(date):
            continue
        scores_by_date[pd.Timestamp(date).normalize()] = (
            row["score_1d"],
            row["score_3d"],
        )

    for score_index, color in (
        (0, SCORE_1D_COLOR),
        (1, SCORE_3D_COLOR),
    ):
        previous: tuple[int, int] | None = None
        for index, row in bars.iterrows():
            date = pd.Timestamp(row["datetime"]).normalize()
            scores = scores_by_date.get(date)
            if scores is None:
                previous = None
                continue
            score = scores[score_index]
            if pd.isna(score) or not math.isfinite(float(score)):
                previous = None
                continue
            x = int(plot_left + (index + 0.5) * step)
            point = (x, score_y(float(score)))
            if previous is None:
                draw.ellipse(
                    (point[0] - 1, point[1] - 1, point[0] + 1, point[1] + 1),
                    fill=color,
                )
            else:
                draw.line((*previous, *point), fill=color, width=2)
            previous = point


def _draw_state_legend(
    draw: ImageDraw.ImageDraw,
    states: pd.DataFrame | None,
    left: int,
    top: int,
    right: int,
    fonts: _Fonts,
) -> None:
    if states is None:
        return
    entries: list[tuple[str, str]] = []
    seen: set[tuple[str, str]] = set()
    for _, row in states.iterrows():
        state = row["state_3d"]
        color = row["state_color"]
        if pd.isna(state) or pd.isna(color):
            continue
        entry = (str(state), str(color))
        if entry not in seen:
            entries.append(entry)
            seen.add(entry)
    if not entries:
        return

    x = left + 330
    y = top + 12
    draw.text((x, y), "State:", fill=MUTED, font=fonts.tiny)
    x += 38
    for state, color in entries:
        text_width = int(draw.textlength(state, font=fonts.tiny))
        if x + text_width + 22 > right - 8:
            break
        draw.rectangle((x, y + 1, x + 11, y + 11), fill=color, outline="#cabfa9")
        draw.text((x + 15, y), state, fill=INK, font=fonts.tiny)
        x += text_width + 28


def _draw_score_legend(
    draw: ImageDraw.ImageDraw,
    plot_left: int,
    price_bottom: int,
    fonts: _Fonts,
) -> None:
    y = price_bottom + 3
    x = plot_left
    for label, color in (
        ("score_1d", SCORE_1D_COLOR),
        ("score_3d", SCORE_3D_COLOR),
    ):
        draw.line((x, y + 6, x + 18, y + 6), fill=color, width=3)
        draw.text((x + 23, y), label, fill=INK, font=fonts.tiny)
        x += 102


def _draw_trade_markers(
    draw: ImageDraw.ImageDraw,
    markers: pd.DataFrame,
    x_by_date: dict[pd.Timestamp, int],
    price_y: Any,
    price_top: int,
    price_bottom: int,
    fonts: _Fonts,
) -> None:
    visible_markers: list[tuple[pd.Series, int]] = []
    for _, marker in markers.iterrows():
        date = pd.Timestamp(marker["datetime"]).normalize()
        x = x_by_date.get(date)
        if x is None:
            continue
        visible_markers.append((marker, x))

    label_levels = _assign_marker_label_levels([x for _, x in visible_markers])
    for (marker, x), label_level in zip(visible_markers, label_levels, strict=True):
        side = str(marker["side"])
        color = BUY_COLOR if side == "buy" else SELL_COLOR
        draw.line((x, price_top, x, price_bottom), fill=color, width=2)
        y = max(
            price_top + 6,
            min(price_bottom - 6, int(price_y(float(marker["fill_price"])))),
        )
        if side == "buy":
            points = [(x, y - 7), (x - 6, y + 5), (x + 6, y + 5)]
        else:
            points = [(x, y + 7), (x - 6, y - 5), (x + 6, y - 5)]
        draw.polygon(points, fill=color)
        label_y = price_top + 4 + label_level * 13
        draw.text((x + 3, label_y), str(marker["label"]), fill=color, font=fonts.tiny)


def _assign_marker_label_levels(
    x_positions: list[int],
    *,
    level_count: int = 8,
    min_spacing: int = 24,
) -> list[int]:
    """Stagger nearby marker labels across deterministic vertical levels."""
    last_x_by_level = [-(10**9)] * level_count
    levels: list[int] = []
    for x in x_positions:
        available = [
            level
            for level, last_x in enumerate(last_x_by_level)
            if x - last_x >= min_spacing
        ]
        level = (
            available[0]
            if available
            else min(
                range(level_count),
                key=last_x_by_level.__getitem__,
            )
        )
        last_x_by_level[level] = x
        levels.append(level)
    return levels


def render_all_trade_charts(
    *,
    daily_csv: Path,
    metadata_csv: Path,
    trades_csv: Path,
    positions_csv: Path,
    output_dir: Path,
    report_start: object,
    report_end: object,
    limit: int | None = None,
    symbols: Sequence[object] | None = None,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render traded or explicitly requested ETF cards and audit files."""
    daily = pd.read_csv(daily_csv, parse_dates=["datetime"])
    metadata = pd.read_csv(metadata_csv)
    trades = pd.read_csv(trades_csv, parse_dates=["datetime"])
    positions = pd.read_csv(positions_csv, parse_dates=["datetime"])
    output_dir.mkdir(parents=True, exist_ok=True)
    for frame in (daily, metadata, trades, positions):
        frame["symbol"] = frame["symbol"].astype(str)

    for column in (
        "fill_price",
        "quantity",
        "commission",
        "slippage_cost",
        "realized_pnl",
    ):
        if column in trades:
            trades[column] = pd.to_numeric(trades[column], errors="coerce").fillna(0.0)
    metadata_by_symbol = metadata.drop_duplicates("symbol", keep="last").set_index(
        "symbol"
    )
    daily_groups = {
        symbol: group.copy() for symbol, group in daily.groupby("symbol", sort=False)
    }
    latest_positions: dict[str, pd.Series] = {}
    if not positions.empty:
        last_position_date = min(
            pd.Timestamp(report_end),
            pd.Timestamp(daily["datetime"].max()),
        )
        latest_positions = {
            symbol: group.iloc[-1]
            for symbol, group in positions.loc[
                positions["datetime"] == last_position_date
            ].groupby("symbol")
        }

    grouped_rows: list[dict[str, Any]] = []
    if symbols is None:
        requested_symbols = list(trades.groupby("symbol", sort=True).groups)
    else:
        requested_symbols = []
        for value in symbols:
            symbol = str(value).strip()
            if not symbol:
                raise ValueError("symbols must not contain empty values")
            if symbol not in requested_symbols:
                requested_symbols.append(symbol)
    for symbol in requested_symbols:
        symbol_trades = trades.loc[trades["symbol"] == symbol]
        realized_pnl = float(symbol_trades["realized_pnl"].sum())
        grouped_rows.append(
            {
                "symbol": str(symbol),
                "realized_pnl": realized_pnl,
                "trade_count": int(len(symbol_trades)),
                "buy_count": int((symbol_trades["side"] == "buy").sum()),
                "sell_count": int((symbol_trades["side"] == "sell").sum()),
                "first_trade_date": symbol_trades["datetime"].min(),
                "last_trade_date": symbol_trades["datetime"].max(),
            }
        )
    if symbols is None:
        grouped_rows.sort(
            key=lambda row: (-float(row["realized_pnl"]), str(row["symbol"]))
        )
    if limit is not None:
        grouped_rows = grouped_rows[: max(limit, 0)]

    summary: dict[str, Any] = {
        "daily_csv": str(daily_csv),
        "metadata_csv": str(metadata_csv),
        "trades_csv": str(trades_csv),
        "positions_csv": str(positions_csv),
        "output_dir": str(output_dir),
        "report_start": str(pd.Timestamp(report_start).date()),
        "report_end": str(pd.Timestamp(report_end).date()),
        "input_symbols": len(grouped_rows),
        "input_trades": int(sum(int(row["trade_count"]) for row in grouped_rows)),
        "rendered_images": 0,
        "existing_images": 0,
        "missing_daily_data": 0,
        "missing_names": 0,
    }
    index_rows: list[dict[str, Any]] = []
    for rank, row in enumerate(grouped_rows, start=1):
        symbol = str(row["symbol"])
        if symbol in metadata_by_symbol.index:
            meta = metadata_by_symbol.loc[symbol]
            name, missing_name = _metadata_text(meta.get("name"), symbol)
            fund_type, _ = _metadata_text(meta.get("fund_type"), "")
            if missing_name:
                summary["missing_names"] += 1
        else:
            name = symbol
            fund_type = ""
            summary["missing_names"] += 1
        filename = make_chart_filename(rank, symbol, name)
        image_path = output_dir / filename
        render_status = "rendered"
        symbol_daily = daily_groups.get(symbol)
        if symbol_daily is None or symbol_daily.empty:
            render_status = "missing_daily_data"
            summary["missing_daily_data"] += 1
        elif image_path.exists() and not overwrite:
            render_status = "existing"
            summary["existing_images"] += 1
        else:
            daily_window = select_daily_window(
                symbol_daily,
                report_start,
                report_end,
                pre_bars=20,
            )
            weekly = aggregate_weekly_bars(symbol_daily)
            symbol_trades = trades.loc[trades["symbol"] == symbol].copy()
            image = render_symbol_card(
                symbol=symbol,
                name=name,
                fund_type=fund_type,
                daily_bars=daily_window,
                weekly_bars=weekly,
                trades=symbol_trades,
                latest_position=latest_positions.get(symbol),
                report_start=report_start,
                report_end=report_end,
            )
            image.save(image_path)
            summary["rendered_images"] += 1
        index_rows.append(
            {
                "rank": rank,
                "symbol": symbol,
                "name": name,
                "fund_type": fund_type,
                **row,
                "is_open": symbol in latest_positions,
                "render_status": render_status,
                "image_path": filename if render_status != "missing_daily_data" else "",
            }
        )

    pd.DataFrame(index_rows, columns=INDEX_COLUMNS).to_csv(
        output_dir / "index.csv",
        index=False,
    )
    (output_dir / "render_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True),
        encoding="utf-8",
    )
    return summary


def build_parser() -> argparse.ArgumentParser:
    """Build the standalone trade chart CLI parser."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--daily-csv", type=Path, required=True)
    parser.add_argument("--metadata-csv", type=Path, required=True)
    parser.add_argument("--trades-csv", type=Path, required=True)
    parser.add_argument("--positions-csv", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--report-start", required=True)
    parser.add_argument("--report-end", required=True)
    parser.add_argument("--limit", type=int)
    parser.add_argument("--symbols", nargs="*")
    parser.add_argument("--overwrite", action="store_true")
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Render ETF trade cards from CLI arguments."""
    args = build_parser().parse_args(argv)
    summary = render_all_trade_charts(
        daily_csv=args.daily_csv,
        metadata_csv=args.metadata_csv,
        trades_csv=args.trades_csv,
        positions_csv=args.positions_csv,
        output_dir=args.output_dir,
        report_start=args.report_start,
        report_end=args.report_end,
        limit=args.limit,
        symbols=args.symbols,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
