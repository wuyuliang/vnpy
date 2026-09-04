"""Render symbol-level stock opportunity charts with EMA and volume."""
from __future__ import annotations

import csv
import json
import re
import shutil
from collections import defaultdict
from collections.abc import Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont


OHLCV_COLUMNS: list[str] = ["datetime", "open", "high", "low", "close", "volume"]
EMA_WINDOWS: tuple[int, ...] = (5, 10, 20)
CARD_WIDTH = 2200
CARD_HEIGHT = 1280
BACKGROUND = "#f7f4ed"
PANEL_BG = "#fffdf7"
INK = "#1f2937"
MUTED = "#64748b"
GRID = "#d8d2c4"
TIMELINE_COLOR = "#cbd5e1"
UP_COLOR = "#c7362f"
DOWN_COLOR = "#16835f"
BUY_COLOR = "#2563eb"
SELL_COLOR = "#dc2626"
SIGNAL_TYPE_COLORS: dict[str, str] = {
    "bull_pullback_continuation": "#2563eb",
    "breakout_pullback_continuation": "#be185d",
    "volume_spike_up": "#f59e0b",
    "ma5_ma10_big_bull": "#16a34a",
}
SIGNAL_TYPE_LABELS: dict[str, str] = {
    "bull_pullback_continuation": "Bull Pullback",
    "breakout_pullback_continuation": "Breakout Pullback",
    "volume_spike_up": "Volume Spike Up",
    "ma5_ma10_big_bull": "MA5/MA10 Big Bull",
}
EMA_COLORS: dict[str, str] = {
    "ema5": "#7c3aed",
    "ema10": "#0f766e",
    "ema20": "#ea580c",
}


def parse_datetime(value: object) -> pd.Timestamp | None:
    """Parse a datetime-like value into a timestamp."""
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    parsed = pd.to_datetime(text, errors="coerce")
    if pd.isna(parsed):
        return None
    return pd.Timestamp(parsed)


def add_ema_columns(frame: pd.DataFrame, windows: Sequence[int] = EMA_WINDOWS) -> pd.DataFrame:
    """Return a copy with EMA columns for close price."""
    out = frame.copy()
    close = pd.to_numeric(out["close"], errors="coerce")
    for window in windows:
        out[f"ema{window}"] = close.ewm(span=window, adjust=False).mean()
    return out


def clip_bars_at_date(frame: pd.DataFrame, end_date: str | None) -> pd.DataFrame:
    """Return bars available no later than a point-in-time report date."""
    if frame.empty or not end_date:
        return frame.copy()
    parsed_end = pd.to_datetime(end_date, errors="coerce")
    if pd.isna(parsed_end):
        return frame.iloc[0:0].copy()
    datetimes = pd.to_datetime(frame["datetime"], errors="coerce")
    return frame.loc[(datetimes <= pd.Timestamp(parsed_end)).fillna(False)].reset_index(drop=True)


def _build_bimonthly_ticks(bars: pd.DataFrame) -> list[tuple[int, str]]:
    """Return first trading-bar indices for two-month timeline intervals."""
    if bars.empty or "datetime" not in bars.columns:
        return []
    datetimes = pd.to_datetime(bars["datetime"], errors="coerce")
    if datetimes.isna().all():
        return []
    valid = datetimes.dropna().sort_values()
    first_month = valid.iloc[0].to_period("M").to_timestamp()
    last_date = valid.iloc[-1]
    ticks: list[tuple[int, str]] = []
    for target in pd.date_range(first_month, last_date, freq="2MS"):
        candidates = datetimes[datetimes >= target]
        if candidates.empty:
            continue
        index = int(candidates.index[0])
        if ticks and ticks[-1][0] == index:
            continue
        ticks.append((index, target.strftime("%Y-%m")))
    return ticks


def _empty_bars() -> pd.DataFrame:
    return pd.DataFrame(columns=OHLCV_COLUMNS)


def _normalize_bars(frame: pd.DataFrame) -> pd.DataFrame:
    if frame.empty:
        return _empty_bars()
    bars = frame.loc[:, [column for column in OHLCV_COLUMNS if column in frame.columns]].copy()
    if set(OHLCV_COLUMNS) - set(bars.columns):
        return _empty_bars()
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="coerce")
    for column in ["open", "high", "low", "close", "volume"]:
        bars[column] = pd.to_numeric(bars[column], errors="coerce")
    bars = bars.dropna(subset=["datetime", "open", "high", "low", "close"])
    bars["volume"] = bars["volume"].fillna(0.0)
    bars = bars.sort_values("datetime").drop_duplicates("datetime", keep="last")
    return add_ema_columns(bars.reset_index(drop=True))


def _safe_slug(value: object) -> str:
    text = str(value).strip() or "na"
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_") or "na"


@lru_cache(maxsize=8192)
def load_symbol_bars(data_root: Path, symbol: str) -> pd.DataFrame:
    """Load one symbol day csv from ``stock/data/origin/day``."""
    path = Path(data_root) / "day" / f"{symbol.replace('.', '_')}.csv"
    if not path.exists():
        return _empty_bars()
    return _normalize_bars(pd.read_csv(path))


def read_opportunity_rows(opportunity_csv: Path) -> list[dict[str, Any]]:
    """Read opportunity rows from csv."""
    rows: list[dict[str, Any]] = []
    with Path(opportunity_csv).open("r", encoding="utf-8-sig", newline="") as fh:
        reader = csv.DictReader(fh)
        for row in reader:
            rows.append(dict(row))
    return rows


def render_all(
    *,
    opportunity_csv: Path,
    output_dir: Path,
    data_root: Path,
    bars_end_date: str | None = None,
) -> dict[str, Any]:
    """Render one summary PNG per symbol."""
    rows = read_opportunity_rows(opportunity_csv)
    grouped: dict[str, list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            grouped[symbol].append(row)

    charts_dir = Path(output_dir) / "charts"
    if charts_dir.exists():
        shutil.rmtree(charts_dir)
    charts_dir.mkdir(parents=True, exist_ok=True)

    index_rows: list[dict[str, Any]] = []
    rendered_images = 0
    missing_bars = 0

    for rank, symbol in enumerate(sorted(grouped), start=1):
        symbol_rows = grouped[symbol]
        bars = clip_bars_at_date(
            load_symbol_bars(Path(data_root), symbol),
            bars_end_date,
        )
        if bars.empty:
            missing_bars += 1
            index_rows.append(
                {
                    "rank": rank,
                    "symbol": symbol,
                    "opportunity_rows": len(symbol_rows),
                    "image_path": "",
                    "render_status": "missing_bars",
                }
            )
            continue

        image = render_symbol_card(
            symbol,
            symbol_rows,
            bars,
            display_end_date=bars_end_date,
        )
        filename = f"{rank:04d}_{_safe_slug(symbol)}_stock_signals_summary.png"
        image_path = charts_dir / filename
        image.save(image_path)
        rendered_images += 1
        index_rows.append(
            {
                "rank": rank,
                "symbol": symbol,
                "opportunity_rows": len(symbol_rows),
                "signal_types": ",".join(sorted({str(row.get("signal_type") or "") for row in symbol_rows if row.get("signal_type")})),
                "image_path": str(Path("charts") / filename),
                "render_status": "rendered",
            }
        )

    summary = {
        "opportunity_csv": str(opportunity_csv),
        "output_dir": str(output_dir),
        "data_root": str(data_root),
        "input_rows": len(rows),
        "symbols": len(grouped),
        "rendered_images": rendered_images,
        "missing_bars": missing_bars,
        "bars_end_date": bars_end_date,
    }

    pd.DataFrame(index_rows).to_csv(Path(output_dir) / "index.csv", index=False)
    (Path(output_dir) / "render_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def render_opportunity_rows_to_dir(
    rows: Sequence[Mapping[str, Any]],
    *,
    output_dir: Path,
    data_root: Path,
    render_cache: dict[str, Path] | None = None,
    cache_rows_by_key: Mapping[str, Sequence[Mapping[str, Any]]] | None = None,
    bars_end_date: str | None = None,
) -> dict[str, Any]:
    """Render one PNG per symbol for a bounded opportunity row set."""
    grouped: dict[str, list[Mapping[str, Any]]] = defaultdict(list)
    for row in rows:
        symbol = str(row.get("symbol") or "").strip()
        if symbol:
            grouped[symbol].append(row)

    Path(output_dir).mkdir(parents=True, exist_ok=True)
    rendered_images = 0
    missing_bars = 0
    for rank, symbol in enumerate(sorted(grouped), start=1):
        current_rows = grouped[symbol]
        signal_slug = "_".join(
            sorted(
                {
                    _safe_slug(row.get("signal_type") or "unknown_signal")
                    for row in current_rows
                }
            )
        )
        filename = f"{rank:04d}_{_safe_slug(symbol)}_{signal_slug}_summary.png"
        image_path = Path(output_dir) / filename
        if render_cache is not None and symbol in render_cache:
            _link_or_copy(render_cache[symbol], image_path)
            rendered_images += 1
            continue

        bars = clip_bars_at_date(
            load_symbol_bars(Path(data_root), symbol),
            bars_end_date,
        )
        if bars.empty:
            missing_bars += 1
            continue
        source_rows = (
            cache_rows_by_key.get(symbol, current_rows)
            if cache_rows_by_key is not None
            else current_rows
        )
        image = render_symbol_card(
            symbol,
            source_rows,
            bars,
            display_end_date=bars_end_date,
        )
        image.save(image_path)
        if render_cache is not None:
            render_cache[symbol] = image_path
        rendered_images += 1

    return {
        "symbols": len(grouped),
        "rendered_images": rendered_images,
        "missing_bars": missing_bars,
    }


def _link_or_copy(source: Path, destination: Path) -> None:
    if destination.exists():
        destination.unlink()
    try:
        destination.hardlink_to(source)
    except OSError:
        shutil.copy2(source, destination)


def render_symbol_card(
    symbol: str,
    rows: Sequence[Mapping[str, Any]],
    bars: pd.DataFrame,
    *,
    include_exit_markers: bool = False,
    display_end_date: str | None = None,
) -> Image.Image:
    """Render one symbol chart with buy markers."""
    image = Image.new("RGB", (CARD_WIDTH, CARD_HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts = _Fonts()

    exchange = str(rows[0].get("exchange") or "")
    name = str(rows[0].get("name") or "").strip()
    signal_summary = _format_signal_type_summary(rows)
    title = f"{symbol} {name}.{exchange} {signal_summary}" if name else f"{symbol}.{exchange} {signal_summary}"
    date_start = bars["datetime"].iloc[0].strftime("%Y-%m-%d")
    date_end = bars["datetime"].iloc[-1].strftime("%Y-%m-%d")
    requested_end = parse_datetime(display_end_date)
    chart_end = requested_end.strftime("%Y-%m-%d") if requested_end is not None else date_end
    total_mv = _format_number(rows[0].get("total_mv"))
    circ_mv = _format_number(rows[0].get("circ_mv"))
    limit_up = str(rows[0].get("limit_up_count_2y") or 0)
    limit_down = str(rows[0].get("limit_down_count_2y") or 0)
    subtitle = (
        f"opportunities={len(rows)} signal_types={signal_summary} data={date_start}..{date_end} chart_end={chart_end} "
        f"total_mv={total_mv} circ_mv={circ_mv} limit_up_2y={limit_up} limit_down_2y={limit_down} overlays=EMA5/EMA10/EMA20"
    )
    draw.rectangle((0, 0, CARD_WIDTH, 88), fill="#223044")
    draw.text((24, 18), title, fill="#fff8e8", font=fonts.title)
    draw.text((24, 54), subtitle, fill="#dbe7ef", font=fonts.small)

    price_rect = (36, 124, CARD_WIDTH - 36, 890)
    volume_rect = (36, 922, CARD_WIDTH - 36, 1198)
    _draw_price_panel(
        draw,
        price_rect,
        bars,
        rows,
        fonts,
        include_exit_markers=include_exit_markers,
    )
    _draw_volume_panel(draw, volume_rect, bars, fonts)
    _draw_legend(draw, (44, 96), fonts, include_exit_markers=include_exit_markers)
    return image


def _draw_price_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    bars: pd.DataFrame,
    rows: Sequence[Mapping[str, Any]],
    fonts: _Fonts,
    *,
    include_exit_markers: bool = False,
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=10, fill=PANEL_BG, outline=GRID, width=2)
    price_rect = (left + 18, top + 18, right - 18, bottom - 42)
    low = float(bars["low"].min())
    high = float(bars["high"].max())
    for ema_col in EMA_COLORS:
        if ema_col in bars.columns:
            low = min(low, float(bars[ema_col].min()))
            high = max(high, float(bars[ema_col].max()))

    xs = _bar_x_positions(len(bars), price_rect)
    for index, label in _build_bimonthly_ticks(bars):
        if index >= len(xs):
            continue
        x = xs[index]
        _draw_vertical_dashed_line(
            draw,
            x=x,
            top=price_rect[1],
            bottom=price_rect[3],
            fill=TIMELINE_COLOR,
            width=1,
            dash_length=5,
            gap_length=7,
        )
        label_box = draw.textbbox((0, 0), label, font=fonts.small)
        label_width = label_box[2] - label_box[0]
        label_x = max(price_rect[0], min(x - label_width // 2, price_rect[2] - label_width))
        draw.text(
            (label_x, price_rect[3] + 12),
            label,
            fill=MUTED,
            font=fonts.small,
        )
    candle_width = max(2, min(7, int((price_rect[2] - price_rect[0]) / max(len(bars) * 1.8, 1))))
    for idx, row in enumerate(bars.itertuples(index=False)):
        x = xs[idx]
        open_v = float(row.open)
        high_v = float(row.high)
        low_v = float(row.low)
        close_v = float(row.close)
        color = UP_COLOR if close_v >= open_v else DOWN_COLOR
        y_high = _scale_price(high_v, low, high, price_rect[1], price_rect[3])
        y_low = _scale_price(low_v, low, high, price_rect[1], price_rect[3])
        y_open = _scale_price(open_v, low, high, price_rect[1], price_rect[3])
        y_close = _scale_price(close_v, low, high, price_rect[1], price_rect[3])
        draw.line((x, y_high, x, y_low), fill=color, width=1)
        body_top = min(y_open, y_close)
        body_bottom = max(y_open, y_close)
        if body_top == body_bottom:
            body_bottom += 1
        draw.rectangle((x - candle_width, body_top, x + candle_width, body_bottom), fill=color, outline=color)

    for ema_col, color in EMA_COLORS.items():
        _draw_line(draw, bars, ema_col, xs, low, high, price_rect, color)

    markers = _build_markers(rows, include_exit_markers=include_exit_markers)
    marker_by_date = {pd.Timestamp(bar_dt).normalize(): idx for idx, bar_dt in enumerate(bars["datetime"])}
    for marker in markers:
        dt = marker["datetime"].normalize()
        if dt not in marker_by_date:
            continue
        idx = marker_by_date[dt]
        x = xs[idx]
        y = _scale_price(float(marker["price"]), low, high, price_rect[1], price_rect[3])
        is_sell = marker.get("kind") == "sell"
        marker_color = (
            SELL_COLOR
            if is_sell
            else SIGNAL_TYPE_COLORS.get(str(marker.get("signal_type") or ""), BUY_COLOR)
        )
        if is_sell:
            _draw_triangle(draw, x, y + 12, marker_color, up=False)
        else:
            _draw_vertical_dashed_line(
                draw,
                x=x,
                top=price_rect[1],
                bottom=price_rect[3],
                fill=marker_color,
                width=2,
                dash_length=10,
                gap_length=7,
            )
            _draw_buy_arrow(draw, x, y, marker_color)

    draw.text((price_rect[0] + 4, price_rect[1] + 4), f"H {high:.2f}", fill=MUTED, font=fonts.small)
    draw.text((price_rect[0] + 4, price_rect[3] - 18), f"L {low:.2f}", fill=MUTED, font=fonts.small)


def _draw_volume_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    bars: pd.DataFrame,
    fonts: _Fonts,
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=10, fill=PANEL_BG, outline=GRID, width=2)
    volume_rect = (left + 18, top + 22, right - 18, bottom - 34)
    max_volume = float(bars["volume"].max()) if not bars.empty else 0.0
    xs = _bar_x_positions(len(bars), volume_rect)
    for index, _ in _build_bimonthly_ticks(bars):
        if index >= len(xs):
            continue
        _draw_vertical_dashed_line(
            draw,
            x=xs[index],
            top=volume_rect[1],
            bottom=volume_rect[3],
            fill=TIMELINE_COLOR,
            width=1,
            dash_length=5,
            gap_length=7,
        )
    bar_width = max(1, min(6, int((volume_rect[2] - volume_rect[0]) / max(len(bars) * 1.8, 1))))
    for idx, row in enumerate(bars.itertuples(index=False)):
        volume = float(row.volume)
        x = xs[idx]
        y = _scale_volume(volume, max_volume, volume_rect[1], volume_rect[3])
        color = UP_COLOR if float(row.close) >= float(row.open) else DOWN_COLOR
        draw.rectangle((x - bar_width, y, x + bar_width, volume_rect[3]), fill=color)
    draw.text((volume_rect[0], volume_rect[3] + 8), "Volume", fill=MUTED, font=fonts.small)


def _draw_line(
    draw: ImageDraw.ImageDraw,
    bars: pd.DataFrame,
    column: str,
    xs: list[int],
    low: float,
    high: float,
    rect: tuple[int, int, int, int],
    color: str,
) -> None:
    points: list[tuple[int, int]] = []
    for idx, value in enumerate(pd.to_numeric(bars[column], errors="coerce")):
        if pd.isna(value):
            continue
        points.append((xs[idx], _scale_price(float(value), low, high, rect[1], rect[3])))
    if len(points) >= 2:
        draw.line(points, fill=color, width=2)


def _draw_legend(
    draw: ImageDraw.ImageDraw,
    origin: tuple[int, int],
    fonts: _Fonts,
    *,
    include_exit_markers: bool = False,
) -> None:
    x, y = origin
    items = [
        ("Bull", SIGNAL_TYPE_COLORS["bull_pullback_continuation"]),
        ("Breakout", SIGNAL_TYPE_COLORS["breakout_pullback_continuation"]),
        ("Vol Spike", SIGNAL_TYPE_COLORS["volume_spike_up"]),
        ("Big Bull", SIGNAL_TYPE_COLORS["ma5_ma10_big_bull"]),
        ("EMA5", EMA_COLORS["ema5"]),
        ("EMA10", EMA_COLORS["ema10"]),
        ("EMA20", EMA_COLORS["ema20"]),
    ]
    if include_exit_markers:
        items.append(("Exit", SELL_COLOR))
    for label, color in items:
        draw.rectangle((x, y + 4, x + 18, y + 16), fill=color)
        draw.text((x + 24, y), label, fill=INK, font=fonts.small)
        x += 150


def _build_markers(
    rows: Sequence[Mapping[str, Any]],
    *,
    include_exit_markers: bool = False,
) -> list[dict[str, Any]]:
    markers: list[dict[str, Any]] = []
    for row in rows:
        entry_dt = None
        for column in ["entry_datetime", "signal_datetime"]:
            entry_dt = parse_datetime(row.get(column))
            if entry_dt is not None:
                break
        entry_price = None
        for column in ["entry_price", "signal_price", "close_price"]:
            entry_price = _parse_float(row.get(column))
            if entry_price is not None:
                break
        untradeable = str(row.get("exit_rule", "")) in {
            "missing_bars",
            "missing_entry_bar",
            "entry_after_data",
            "invalid_entry_date",
            "invalid_entry_price",
        }
        if not untradeable and entry_dt is not None and entry_price is not None:
            markers.append(
                {
                    "datetime": entry_dt,
                    "price": entry_price,
                    "kind": "buy",
                    "signal_type": str(row.get("signal_type") or ""),
                }
            )
        if not include_exit_markers:
            continue
        exit_dt = None
        for column in ["exit_datetime", "exit_date"]:
            exit_dt = parse_datetime(row.get(column))
            if exit_dt is not None:
                break
        exit_price = _parse_float(row.get("exit_price"))
        if exit_dt is not None and exit_price is not None:
            markers.append(
                {
                    "datetime": exit_dt,
                    "price": exit_price,
                    "kind": "sell",
                    "signal_type": str(row.get("signal_type") or ""),
                }
            )
    return markers


def _format_signal_type_summary(rows: Sequence[Mapping[str, Any]]) -> str:
    counts: dict[str, int] = defaultdict(int)
    for row in rows:
        counts[str(row.get("signal_type") or "unknown_signal")] += 1
    if not counts:
        return "stock_signals"
    return " ".join(
        f"{SIGNAL_TYPE_LABELS.get(signal_type, signal_type)}:{count}"
        for signal_type, count in sorted(counts.items())
    )


def _format_number(value: object) -> str:
    parsed = _parse_float(value)
    if parsed is None:
        return "NA"
    if abs(parsed) >= 10000:
        return f"{parsed / 10000:.2f}e4"
    return f"{parsed:.2f}"


def _parse_float(value: object) -> float | None:
    if value is None:
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    if pd.isna(parsed):
        return None
    return parsed


def _bar_x_positions(count: int, rect: tuple[int, int, int, int]) -> list[int]:
    if count <= 1:
        return [(rect[0] + rect[2]) // 2]
    width = rect[2] - rect[0]
    return [int(rect[0] + idx * width / (count - 1)) for idx in range(count)]


def _draw_triangle(draw: ImageDraw.ImageDraw, x: int, y: int, color: str, *, up: bool) -> None:
    if up:
        points = [(x, y - 9), (x - 9, y + 9), (x + 9, y + 9)]
    else:
        points = [(x, y + 9), (x - 9, y - 9), (x + 9, y - 9)]
    draw.polygon(points, fill=color, outline="#ffffff")


def _draw_vertical_dashed_line(
    draw: ImageDraw.ImageDraw,
    *,
    x: int,
    top: int,
    bottom: int,
    fill: str,
    width: int,
    dash_length: int,
    gap_length: int,
) -> None:
    """Draw a vertical dashed line with deterministic dash spacing."""
    step = max(1, dash_length) + max(0, gap_length)
    for start in range(top, bottom + 1, step):
        draw.line(
            (x, start, x, min(start + max(1, dash_length) - 1, bottom)),
            fill=fill,
            width=width,
        )


def _draw_buy_arrow(draw: ImageDraw.ImageDraw, x: int, y: int, color: str) -> None:
    """Draw an upward buy arrow whose tip points at the entry price."""
    draw.line((x, y + 17, x, y + 32), fill=color, width=3)
    draw.polygon(
        [(x, y), (x - 10, y + 18), (x + 10, y + 18)],
        fill=color,
        outline="#ffffff",
    )


def _scale_price(price: float, low: float, high: float, top: int, bottom: int) -> int:
    if high <= low:
        return (top + bottom) // 2
    pct = (price - low) / (high - low)
    return int(bottom - pct * (bottom - top))


def _scale_volume(volume: float, max_volume: float, top: int, bottom: int) -> int:
    if max_volume <= 0:
        return bottom
    pct = max(0.0, min(1.0, volume / max_volume))
    return int(bottom - pct * (bottom - top))


class _Fonts:
    def __init__(self) -> None:
        self.title = _load_font(28)
        self.small = _load_font(18)


def _load_font(size: int) -> ImageFont.ImageFont:
    for font_path in [
        "/System/Library/Fonts/PingFang.ttc",
        "/System/Library/Fonts/Hiragino Sans GB.ttc",
        "/System/Library/Fonts/Supplemental/Songti.ttc",
        "/System/Library/Fonts/Supplemental/Arial Unicode.ttf",
    ]:
        path = Path(font_path)
        if path.exists():
            try:
                return ImageFont.truetype(str(path), size=size)
            except OSError:
                continue
    return ImageFont.load_default()


__all__ = [
    "add_ema_columns",
    "clip_bars_at_date",
    "load_symbol_bars",
    "parse_datetime",
    "read_opportunity_rows",
    "render_all",
    "render_opportunity_rows_to_dir",
    "render_symbol_card",
]
