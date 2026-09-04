"""Render profit-sorted trade opportunity K-line review cards.

The script reads OOT trade details, sorts all opportunities by profit, and
creates one PNG card per row with weekly, daily, and hourly OHLCV context.
It intentionally uses Pillow instead of matplotlib/mplfinance so it can run in
the current project environment without adding dependencies.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


OHLCV_COLUMNS: list[str] = ["datetime", "open", "high", "low", "close", "volume"]
ALL_SYMBOL_COLUMNS: list[str] = OHLCV_COLUMNS + ["symbol"]
CARD_WIDTH = 1680
CARD_HEIGHT = 1240
LEFT_WIDTH = 430
HEADER_HEIGHT = 82
PANEL_GAP = 18
BACKGROUND = "#f7f4ed"
PANEL_BG = "#fffdf7"
INK = "#1f2933"
MUTED = "#64748b"
GRID = "#d8d2c4"
UP_COLOR = "#c7362f"
DOWN_COLOR = "#16835f"
FLAT_COLOR = "#6b7280"
ENTRY_COLOR = "#2563eb"
EXIT_COLOR = "#d97706"


def parse_float(value: object) -> float | None:
    """Parse a numeric CSV value, returning None for blanks and NaN."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    try:
        result = float(text)
    except ValueError:
        return None
    if math.isnan(result) or math.isinf(result):
        return None
    return result


def parse_datetime(value: object) -> pd.Timestamp | None:
    """Parse a datetime-like value into a pandas Timestamp."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed)


def sort_trade_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Sort all trade opportunity rows by profit descending."""
    ranked: list[dict[str, Any]] = []
    for index, row in enumerate(rows, start=1):
        copied = dict(row)
        copied.setdefault("source_row", index)
        net_pnl = parse_float(copied.get("net_pnl"))
        return_pct = parse_float(copied.get("trade_return_pct"))
        sort_value = net_pnl if net_pnl is not None else return_pct
        copied["sort_value"] = sort_value if sort_value is not None else float("-inf")
        ranked.append(copied)

    ranked.sort(
        key=lambda row: (
            -float(row["sort_value"]),
            str(row.get("entry_datetime") or ""),
            int(row.get("source_row") or 0),
        )
    )

    for rank, row in enumerate(ranked, start=1):
        row["sort_rank"] = rank
    return ranked


def make_chart_filename(row: Mapping[str, object]) -> str:
    """Build a stable, filesystem-safe chart filename."""
    rank = int(row.get("sort_rank") or 0)
    symbol = _safe_slug(row.get("symbol") or "UNKNOWN")
    side = _safe_slug(row.get("side") or "na")
    status = _safe_slug(row.get("execution_status") or "unknown")
    dt = parse_datetime(row.get("entry_datetime"))
    dt_text = dt.strftime("%Y%m%d_%H%M%S") if dt is not None else "unknown_time"
    return f"{rank:06d}_{symbol}_{dt_text}_{side}_{status}.png"


def aggregate_weekly_bars(day_bars: pd.DataFrame) -> pd.DataFrame:
    """Aggregate daily OHLCV rows into week-ending bars."""
    if day_bars.empty:
        return _empty_bars()

    bars = _normalize_bars(day_bars)
    if bars.empty:
        return _empty_bars()

    weekly = (
        bars.set_index("datetime")
        .resample("W-FRI")
        .agg(
            {
                "open": "first",
                "high": "max",
                "low": "min",
                "close": "last",
                "volume": "sum",
            }
        )
        .dropna(subset=["open", "high", "low", "close"])
        .reset_index()
    )
    return _normalize_bars(weekly)


@lru_cache(maxsize=256)
def load_symbol_bars(data_root: Path, interval: str, symbol: str) -> pd.DataFrame:
    """Load all OHLCV parquet bars for one symbol and interval."""
    all_bars = _load_all_symbols_bars(data_root, interval)
    if not all_bars.empty:
        symbol_bars = all_bars.loc[all_bars["symbol"] == symbol, ALL_SYMBOL_COLUMNS].copy()
        if not symbol_bars.empty:
            return _normalize_bars(symbol_bars)

    symbol_dir = data_root / interval / symbol
    if not symbol_dir.exists():
        return _empty_bars()

    frames: list[pd.DataFrame] = []
    for path in sorted(symbol_dir.glob("*.parquet")):
        try:
            frame = pd.read_parquet(path, columns=OHLCV_COLUMNS)
        except (KeyError, ValueError):
            frame = pd.read_parquet(path)
            missing = [column for column in OHLCV_COLUMNS if column not in frame.columns]
            if missing:
                continue
            frame = frame[OHLCV_COLUMNS]
        if not frame.empty:
            frames.append(frame)

    if not frames:
        return _empty_bars()

    combined = pd.concat(frames, ignore_index=True)
    return _normalize_bars(combined)


@lru_cache(maxsize=16)
def _load_all_symbols_bars(data_root: Path, interval: str) -> pd.DataFrame:
    """Load an interval-level merged parquet when available."""
    path = data_root / interval / "_all_symbols.parquet"
    if not path.exists():
        return pd.DataFrame(columns=ALL_SYMBOL_COLUMNS)
    try:
        frame = pd.read_parquet(path, columns=ALL_SYMBOL_COLUMNS)
    except (KeyError, ValueError):
        return pd.DataFrame(columns=ALL_SYMBOL_COLUMNS)
    if frame.empty:
        return pd.DataFrame(columns=ALL_SYMBOL_COLUMNS)
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    frame["symbol"] = frame["symbol"].astype(str)
    frame = frame.dropna(subset=["datetime", "open", "high", "low", "close", "symbol"])
    frame["volume"] = frame["volume"].fillna(0.0)
    return frame.sort_values(["symbol", "datetime"]).reset_index(drop=True)


def select_window(
    bars: pd.DataFrame,
    entry_dt: pd.Timestamp | None,
    exit_dt: pd.Timestamp | None,
    target_count: int,
) -> pd.DataFrame:
    """Select a compact chart window around entry and exit."""
    if bars.empty:
        return _empty_bars()

    normalized = _normalize_bars(bars)
    if normalized.empty:
        return _empty_bars()

    anchor = exit_dt or entry_dt
    if anchor is None:
        return normalized.tail(target_count).reset_index(drop=True)

    times = normalized["datetime"]
    pos = int(times.searchsorted(anchor, side="right")) - 1
    pos = max(0, min(pos, len(normalized) - 1))
    post_count = max(6, target_count // 8)
    end = min(len(normalized), pos + post_count + 1)
    start = max(0, end - target_count)
    return normalized.iloc[start:end].reset_index(drop=True)


def read_trade_rows(trade_csv: Path) -> list[dict[str, Any]]:
    """Read trade opportunity rows and attach 1-based source row numbers."""
    rows: list[dict[str, Any]] = []
    with trade_csv.open(newline="", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        for source_row, row in enumerate(reader, start=1):
            copied: dict[str, Any] = dict(row)
            copied["source_row"] = source_row
            rows.append(copied)
    return rows


def render_all(
    *,
    trade_csv: Path,
    output_dir: Path,
    data_root: Path,
    limit: int | None = None,
    start_rank: int = 1,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render chart cards and write index/summary files."""
    rows = read_trade_rows(trade_csv)
    sorted_rows = sort_trade_rows(rows)
    selected_rows = [row for row in sorted_rows if int(row["sort_rank"]) >= start_rank]
    if limit is not None:
        selected_rows = selected_rows[:limit]

    charts_dir = output_dir / "charts"
    charts_dir.mkdir(parents=True, exist_ok=True)

    summary: dict[str, Any] = {
        "trade_csv": str(trade_csv),
        "output_dir": str(output_dir),
        "data_root": str(data_root),
        "total_input_rows": len(rows),
        "processed_rows": len(selected_rows),
        "rendered_images": 0,
        "skipped_images": 0,
        "existing_images": 0,
        "missing_weekly_panels": 0,
        "missing_daily_panels": 0,
        "missing_hourly_panels": 0,
        "missing_all_chart_data": 0,
    }
    index_rows: list[dict[str, Any]] = []

    for row in selected_rows:
        filename = make_chart_filename(row)
        image_path = charts_dir / filename
        rel_image_path = image_path.relative_to(output_dir)

        entry_dt = parse_datetime(row.get("entry_datetime"))
        exit_dt = parse_datetime(row.get("final_exit_datetime")) or parse_datetime(row.get("exit_datetime"))
        symbol = str(row.get("symbol") or "").strip()
        render_status = "rendered"
        missing_panels: list[str] = []

        if image_path.exists() and not overwrite:
            summary["existing_images"] += 1
            render_status = "existing"
        else:
            day_bars = load_symbol_bars(data_root, "day", symbol)
            hourly_bars = load_symbol_bars(data_root, "minute60", symbol)
            weekly_bars = aggregate_weekly_bars(day_bars)

            chart_data = {
                "weekly": select_window(weekly_bars, entry_dt, exit_dt, 80),
                "daily": select_window(day_bars, entry_dt, exit_dt, 160),
                "hourly": select_window(hourly_bars, entry_dt, exit_dt, 240),
            }
            missing_panels = [name for name, frame in chart_data.items() if frame.empty]
            for panel_name in missing_panels:
                summary[f"missing_{panel_name}_panels"] += 1

            if len(missing_panels) == len(chart_data):
                summary["missing_all_chart_data"] += 1
                summary["skipped_images"] += 1
                render_status = "missing_all_chart_data"
            else:
                image = render_trade_card(row, chart_data, entry_dt, exit_dt)
                image.save(image_path)
                summary["rendered_images"] += 1

        index_rows.append(_build_index_row(row, rel_image_path, render_status, missing_panels))

    _write_index(output_dir / "index.csv", index_rows)
    (output_dir / "render_summary.json").write_text(
        json.dumps(summary, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    return summary


def render_trade_card(
    row: Mapping[str, Any],
    chart_data: Mapping[str, pd.DataFrame],
    entry_dt: pd.Timestamp | None,
    exit_dt: pd.Timestamp | None,
) -> Image.Image:
    """Render one trade review card as a Pillow image."""
    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts = _Fonts()

    rank = row.get("sort_rank")
    symbol = _text(row.get("symbol"))
    exchange = _text(row.get("exchange"))
    side = _text(row.get("side"))
    status = _text(row.get("execution_status"))
    signal_type = _text(row.get("signal_type"))
    title = f"Rank #{rank}  {symbol}.{exchange}  {side.upper()}  {signal_type}"
    subtitle = f"Entry {_text(row.get('entry_datetime'))} | Status {status} | Sort PnL {_fmt(row.get('net_pnl'))}"

    draw.rectangle((0, 0, CARD_WIDTH, HEADER_HEIGHT), fill="#222f3e")
    draw.text((24, 16), title, fill="#fff8e8", font=fonts.title)
    draw.text((24, 50), subtitle, fill="#dbe7ef", font=fonts.small)

    _draw_metadata(draw, row, fonts)

    chart_left = LEFT_WIDTH + 28
    chart_right = CARD_WIDTH - 24
    chart_top = HEADER_HEIGHT + 22
    panel_height = (CARD_HEIGHT - chart_top - 28 - PANEL_GAP * 2) // 3
    panels = [
        ("Weekly K", chart_data["weekly"], 0),
        ("Daily K", chart_data["daily"], 1),
        ("Hourly K", chart_data["hourly"], 2),
    ]
    for title_text, bars, panel_index in panels:
        top = chart_top + panel_index * (panel_height + PANEL_GAP)
        rect = (chart_left, top, chart_right, top + panel_height)
        _draw_chart_panel(draw, rect, title_text, bars, entry_dt, exit_dt, fonts)

    return image


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--trade-csv",
        type=Path,
        default=Path(
            "cta/backtest/20260627_ab_bigger_01_allowlist/"
            "oot_20260628_164308_bigger_01_allowlist/raw/all_trade_details.csv"
        ),
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("cta/analysis/20260628_bigger_01_allowlist_trade_charts"),
    )
    parser.add_argument("--data-root", type=Path, default=Path("cta/data/feature"))
    parser.add_argument("--limit", type=int, default=None)
    parser.add_argument("--start-rank", type=int, default=1)
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    summary = render_all(
        trade_csv=args.trade_csv,
        output_dir=args.output_dir,
        data_root=args.data_root,
        limit=args.limit,
        start_rank=args.start_rank,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, indent=2, ensure_ascii=False))
    return 0


def _normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty_bars()
    optional_columns = ["symbol"] if "symbol" in frame.columns else []
    wanted_columns = OHLCV_COLUMNS + optional_columns
    bars = frame.loc[:, [column for column in wanted_columns if column in frame.columns]].copy()
    if set(OHLCV_COLUMNS) - set(bars.columns):
        return _empty_bars()
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    if "symbol" in bars.columns:
        bars["symbol"] = bars["symbol"].astype(str)
    bars = bars.dropna(subset=["datetime", "open", "high", "low", "close"])
    bars["volume"] = bars["volume"].fillna(0.0)
    bars = bars.sort_values("datetime").drop_duplicates("datetime", keep="last")
    return bars.reset_index(drop=True)


def _empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)


def _safe_slug(value: object) -> str:
    text = str(value).strip() or "na"
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_") or "na"


def _text(value: object) -> str:
    if value is None:
        return ""
    return str(value)


def _fmt(value: object, digits: int = 4) -> str:
    parsed = parse_float(value)
    if parsed is None:
        return _text(value)
    if abs(parsed) >= 1000:
        return f"{parsed:,.2f}"
    return f"{parsed:.{digits}f}"


class _Fonts:
    def __init__(self) -> None:
        self.title = _load_font(24)
        self.heading = _load_font(16)
        self.body = _load_font(13)
        self.small = _load_font(11)


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


def _draw_metadata(draw: ImageDraw.ImageDraw, row: Mapping[str, Any], fonts: _Fonts) -> None:
    x = 24
    y = HEADER_HEIGHT + 24
    draw.rounded_rectangle((16, HEADER_HEIGHT + 14, LEFT_WIDTH, CARD_HEIGHT - 28), radius=14, fill="#fffaf0", outline="#e2d6bd")

    blocks = [
        (
            "Trade",
            [
                ("source_row", row.get("source_row")),
                ("entry", row.get("entry_datetime")),
                ("signal", row.get("signal_datetime")),
                ("exit", row.get("final_exit_datetime") or row.get("exit_datetime")),
                ("interval", row.get("interval")),
                ("side", row.get("side")),
                ("signal_type", row.get("signal_type")),
            ],
        ),
        (
            "Execution",
            [
                ("status", row.get("execution_status")),
                ("block_reason", row.get("block_reason")),
                ("exit_reason", row.get("exit_reason")),
                ("entry_action", row.get("entry_action")),
                ("exit_action", row.get("exit_action")),
            ],
        ),
        (
            "Price / PnL",
            [
                ("entry_fill", row.get("entry_fill_price") or row.get("entry_price")),
                ("final_exit", row.get("final_exit_price")),
                ("stop_loss", row.get("stop_loss_price")),
                ("planned_exit", row.get("planned_exit_price")),
                ("net_pnl", _fmt(row.get("net_pnl"))),
                ("gross_pnl", _fmt(row.get("gross_pnl"))),
                ("trade_ret", _fmt(row.get("trade_return_pct"))),
                ("gross_ret", _fmt(row.get("gross_return_pct"))),
                ("cost_pct", _fmt(row.get("cost_pct"))),
            ],
        ),
        (
            "Model / Risk",
            [
                ("filter_prob", _fmt(row.get("trade_filter_prob"))),
                ("filter_pctl", _fmt(row.get("trade_filter_prob_pctl"))),
                ("decision", _fmt(row.get("final_decision_score"))),
                ("gate_score", _fmt(row.get("trade_filter_gate_score"))),
                ("gate_threshold", _fmt(row.get("trade_filter_gate_threshold"))),
                ("position_scale", _fmt(row.get("position_scale"))),
                ("notional", _fmt(row.get("position_notional"))),
            ],
        ),
    ]

    for heading, pairs in blocks:
        draw.text((x, y), heading, fill=INK, font=fonts.heading)
        y += 24
        for key, value in pairs:
            line = f"{key}: {_truncate(_text(value), 38)}"
            fill = "#b42318" if key == "status" else INK
            draw.text((x, y), line, fill=fill, font=fonts.body)
            y += 18
        y += 13


def _draw_chart_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    title: str,
    bars: pd.DataFrame,
    entry_dt: pd.Timestamp | None,
    exit_dt: pd.Timestamp | None,
    fonts: _Fonts,
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=12, fill=PANEL_BG, outline="#e1d8c8")
    draw.text((left + 14, top + 8), title, fill=INK, font=fonts.heading)

    if bars.empty:
        draw.text((left + 20, top + 52), "No local OHLCV data for this panel", fill=MUTED, font=fonts.body)
        return

    normalized = _normalize_bars(bars)
    if normalized.empty:
        draw.text((left + 20, top + 52), "No valid OHLCV rows for this panel", fill=MUTED, font=fonts.body)
        return

    price_rect = (left + 54, top + 38, right - 18, bottom - 82)
    volume_rect = (left + 54, bottom - 66, right - 18, bottom - 24)
    _draw_grid(draw, price_rect, volume_rect)
    _draw_candles(draw, normalized, price_rect)
    _draw_volume(draw, normalized, volume_rect)
    _draw_markers(draw, normalized, price_rect, volume_rect, entry_dt, exit_dt, fonts)
    _draw_axis_labels(draw, normalized, price_rect, volume_rect, fonts)


def _draw_grid(
    draw: ImageDraw.ImageDraw,
    price_rect: tuple[int, int, int, int],
    volume_rect: tuple[int, int, int, int],
) -> None:
    left, top, right, bottom = price_rect
    for i in range(5):
        y = top + int((bottom - top) * i / 4)
        draw.line((left, y, right, y), fill=GRID)
    draw.rectangle(price_rect, outline="#cdbfaaa0")
    draw.rectangle(volume_rect, outline="#cdbfaaa0")


def _draw_candles(draw: ImageDraw.ImageDraw, bars: pd.DataFrame, rect: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = rect
    high = float(bars["high"].max())
    low = float(bars["low"].min())
    if high == low:
        high += 1.0
        low -= 1.0
    count = len(bars)
    step = (right - left) / max(count, 1)
    body_width = max(2, min(9, int(step * 0.62)))

    for i, bar in bars.iterrows():
        x = int(left + step * (i + 0.5))
        open_y = _scale_price(float(bar["open"]), low, high, top, bottom)
        high_y = _scale_price(float(bar["high"]), low, high, top, bottom)
        low_y = _scale_price(float(bar["low"]), low, high, top, bottom)
        close_y = _scale_price(float(bar["close"]), low, high, top, bottom)
        color = UP_COLOR if bar["close"] >= bar["open"] else DOWN_COLOR
        if bar["close"] == bar["open"]:
            color = FLAT_COLOR
        draw.line((x, high_y, x, low_y), fill=color, width=1)
        y1, y2 = sorted((open_y, close_y))
        if y1 == y2:
            draw.line((x - body_width // 2, y1, x + body_width // 2, y1), fill=color, width=2)
        else:
            draw.rectangle((x - body_width // 2, y1, x + body_width // 2, y2), fill=color, outline=color)


def _draw_volume(draw: ImageDraw.ImageDraw, bars: pd.DataFrame, rect: tuple[int, int, int, int]) -> None:
    left, top, right, bottom = rect
    max_volume = float(bars["volume"].max() or 0)
    if max_volume <= 0:
        return
    count = len(bars)
    step = (right - left) / max(count, 1)
    width = max(1, int(step * 0.72))
    for i, bar in bars.iterrows():
        x = int(left + step * (i + 0.5))
        height = int((float(bar["volume"]) / max_volume) * (bottom - top))
        color = UP_COLOR if bar["close"] >= bar["open"] else DOWN_COLOR
        draw.rectangle((x - width // 2, bottom - height, x + width // 2, bottom), fill=color)


def _draw_markers(
    draw: ImageDraw.ImageDraw,
    bars: pd.DataFrame,
    price_rect: tuple[int, int, int, int],
    volume_rect: tuple[int, int, int, int],
    entry_dt: pd.Timestamp | None,
    exit_dt: pd.Timestamp | None,
    fonts: _Fonts,
) -> None:
    for label, dt, color in [("ENTRY", entry_dt, ENTRY_COLOR), ("EXIT", exit_dt, EXIT_COLOR)]:
        marker_x = _marker_x(bars, dt, price_rect)
        if marker_x is None:
            continue
        draw.line((marker_x, price_rect[1], marker_x, volume_rect[3]), fill=color, width=2)
        draw.text((marker_x + 4, price_rect[1] + 4), label, fill=color, font=fonts.small)


def _draw_axis_labels(
    draw: ImageDraw.ImageDraw,
    bars: pd.DataFrame,
    price_rect: tuple[int, int, int, int],
    volume_rect: tuple[int, int, int, int],
    fonts: _Fonts,
) -> None:
    left, top, right, bottom = price_rect
    high = float(bars["high"].max())
    low = float(bars["low"].min())
    last_close = float(bars.iloc[-1]["close"])
    draw.text((left - 50, top - 2), _fmt(high), fill=MUTED, font=fonts.small)
    draw.text((left - 50, bottom - 12), _fmt(low), fill=MUTED, font=fonts.small)
    draw.text((right - 84, top - 2), f"C {_fmt(last_close)}", fill=MUTED, font=fonts.small)

    first_dt = pd.Timestamp(bars.iloc[0]["datetime"]).strftime("%Y-%m-%d")
    last_dt = pd.Timestamp(bars.iloc[-1]["datetime"]).strftime("%Y-%m-%d")
    draw.text((left, volume_rect[3] + 4), first_dt, fill=MUTED, font=fonts.small)
    draw.text((right - 82, volume_rect[3] + 4), last_dt, fill=MUTED, font=fonts.small)
    draw.text((left - 48, volume_rect[1] + 10), "Vol", fill=MUTED, font=fonts.small)


def _scale_price(value: float, low: float, high: float, top: int, bottom: int) -> int:
    return int(bottom - (value - low) / (high - low) * (bottom - top))


def _marker_x(
    bars: pd.DataFrame,
    dt: pd.Timestamp | None,
    rect: tuple[int, int, int, int],
) -> int | None:
    if dt is None or bars.empty:
        return None
    times = bars["datetime"]
    if dt < times.iloc[0] or dt > times.iloc[-1]:
        return None
    pos = int(times.searchsorted(dt, side="left"))
    if pos >= len(bars):
        pos = len(bars) - 1
    if pos > 0:
        prev_delta = abs(dt - pd.Timestamp(times.iloc[pos - 1]))
        cur_delta = abs(pd.Timestamp(times.iloc[pos]) - dt)
        if prev_delta <= cur_delta:
            pos -= 1
    left, _, right, _ = rect
    step = (right - left) / max(len(bars), 1)
    return int(left + step * (pos + 0.5))


def _truncate(text: str, max_len: int) -> str:
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "…"


def _build_index_row(
    row: Mapping[str, Any],
    rel_image_path: Path,
    render_status: str,
    missing_panels: Sequence[str],
) -> dict[str, Any]:
    fields = [
        "sort_rank",
        "source_row",
        "symbol",
        "exchange",
        "entry_datetime",
        "signal_datetime",
        "final_exit_datetime",
        "exit_datetime",
        "interval",
        "side",
        "signal_type",
        "execution_status",
        "block_reason",
        "exit_reason",
        "net_pnl",
        "gross_pnl",
        "trade_return_pct",
        "gross_return_pct",
        "trade_filter_prob",
        "final_decision_score",
        "sort_value",
    ]
    result = {field: row.get(field, "") for field in fields}
    result["image_path"] = str(rel_image_path) if render_status != "missing_all_chart_data" else ""
    result["render_status"] = render_status
    result["missing_panels"] = "|".join(missing_panels)
    return result


def _write_index(path: Path, rows: list[dict[str, Any]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fieldnames = [
        "sort_rank",
        "source_row",
        "symbol",
        "exchange",
        "entry_datetime",
        "signal_datetime",
        "final_exit_datetime",
        "exit_datetime",
        "interval",
        "side",
        "signal_type",
        "execution_status",
        "block_reason",
        "exit_reason",
        "net_pnl",
        "gross_pnl",
        "trade_return_pct",
        "gross_return_pct",
        "trade_filter_prob",
        "final_decision_score",
        "sort_value",
        "image_path",
        "render_status",
        "missing_panels",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


if __name__ == "__main__":
    raise SystemExit(main())
