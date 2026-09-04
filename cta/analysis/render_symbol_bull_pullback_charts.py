"""Render symbol-level bull pullback summary charts.

Each output image covers one symbol and one calendar half-year. It overlays all
``bull_pullback_continuation`` trade points on weekly, daily, and hourly K-line
panels.
"""

from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from collections import defaultdict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pandas as pd
from PIL import Image, ImageDraw, ImageFont

if __package__ is None or __package__ == "":
    sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from cta.analysis.render_trade_opportunity_charts import (
    aggregate_weekly_bars,
    load_symbol_bars,
    parse_datetime,
    parse_float,
    read_trade_rows,
)


TARGET_SIGNAL_TYPE = "bull_pullback_continuation"
MARKER_STYLES: dict[str, dict[str, str]] = {
    "long_buy": {"color": "#16a34a", "label": "long buy"},
    "long_sell": {"color": "#dc2626", "label": "long sell"},
    "short_buy": {"color": "#2563eb", "label": "short buy"},
    "short_sell": {"color": "#f97316", "label": "short sell"},
}
CHART_PANELS: tuple[dict[str, Any], ...] = (
    {"key": "weekly", "title": "Weekly K", "date_only": True},
    {"key": "daily", "title": "Daily K", "date_only": True},
    {"key": "hourly", "title": "Hourly K", "date_only": False},
)
BACKGROUND = "#f7f4ed"
PANEL_BG = "#fffdf7"
INK = "#1f2937"
MUTED = "#64748b"
GRID = "#d8d2c4"
UP_COLOR = "#c7362f"
DOWN_COLOR = "#16835f"
FLAT_COLOR = "#6b7280"
WIDTH = 1800
HEIGHT = 1640
EXECUTED_MARKER_Y_OFFSET = 18
DEFAULT_TRADE_CSV = Path(
    "cta/backtest/20260627_ab_bigger_01_allowlist/"
    "oot_20260628_164308_bigger_01_allowlist/raw/all_trade_details.csv"
)
DEFAULT_OUTPUT_DIR = Path("cta/analysis/20260628_bigger_01_allowlist_trade_charts/symbols")


def filter_bull_pullback_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Return only bull pullback continuation rows."""
    return [row for row in rows if row.get("signal_type") == TARGET_SIGNAL_TYPE]


def half_year_key(value: object) -> str:
    """Return YYYYH1 or YYYYH2 for a datetime-like value."""
    dt = parse_datetime(value)
    if dt is None:
        raise ValueError(f"invalid datetime for half-year key: {value!r}")
    half = "H1" if dt.month <= 6 else "H2"
    return f"{dt.year}{half}"


def build_trade_markers(row: Mapping[str, Any]) -> list[dict[str, Any]]:
    """Build long/short buy/sell marker dictionaries from one trade row."""
    markers: list[dict[str, Any]] = []
    entry_dt = parse_datetime(row.get("entry_fill_datetime")) or parse_datetime(row.get("entry_datetime"))
    entry_price = parse_float(row.get("entry_fill_price")) or parse_float(row.get("entry_price"))
    exit_dt = parse_datetime(row.get("final_exit_datetime")) or parse_datetime(row.get("exit_datetime"))
    exit_price = parse_float(row.get("final_exit_price")) or parse_float(row.get("exit_price_ref"))
    side = row.get("side")

    if entry_dt is not None and entry_price is not None:
        entry_kind = _marker_kind(side, row.get("entry_action"))
        markers.append(
            {
                "datetime": entry_dt,
                "price": entry_price,
                "kind": entry_kind,
                "color": MARKER_STYLES[entry_kind]["color"],
                "label": MARKER_STYLES[entry_kind]["label"],
                "status": row.get("execution_status", ""),
            }
        )

    if exit_dt is not None and exit_price is not None:
        exit_kind = _marker_kind(side, row.get("exit_action"))
        markers.append(
            {
                "datetime": exit_dt,
                "price": exit_price,
                "kind": exit_kind,
                "color": MARKER_STYLES[exit_kind]["color"],
                "label": MARKER_STYLES[exit_kind]["label"],
                "status": row.get("execution_status", ""),
            }
        )
    return markers


def symbol_expected_returns(rows: Sequence[Mapping[str, Any]]) -> dict[str, float]:
    """Sum expected return by symbol using each row's ``net_pnl`` value."""
    returns: dict[str, float] = defaultdict(float)
    for row in rows:
        symbol = str(row.get("symbol") or "UNKNOWN").strip()
        returns[symbol] += trade_rows_expected_return([row])
    return dict(returns)


def trade_rows_expected_return(rows: Sequence[Mapping[str, Any]]) -> float:
    """Sum expected return for the provided trade rows."""
    return sum(parse_float(row.get("net_pnl")) or 0.0 for row in rows)


def render_symbol_bull_pullback_charts(
    *,
    trade_csv: Path,
    output_dir: Path,
    data_root: Path,
    overwrite: bool = False,
) -> dict[str, Any]:
    """Render all symbol half-year bull pullback summary charts."""
    rows = filter_bull_pullback_rows(read_trade_rows(trade_csv))
    groups: dict[tuple[str, str, str], list[dict[str, Any]]] = defaultdict(list)
    for row in rows:
        trade_dt = parse_datetime(row.get("entry_fill_datetime")) or parse_datetime(row.get("entry_datetime"))
        if trade_dt is None:
            continue
        symbol = str(row.get("symbol") or "UNKNOWN").strip()
        exchange = str(row.get("exchange") or "").strip()
        groups[(symbol, exchange, half_year_key(trade_dt))].append(row)

    if overwrite and output_dir.exists():
        _clear_generated_outputs(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    index_rows: list[dict[str, Any]] = []
    rendered = 0
    skipped = 0
    returns_by_symbol = symbol_expected_returns(rows)
    ranked_symbols = sorted(returns_by_symbol, key=lambda symbol: (-returns_by_symbol[symbol], symbol))
    symbol_ranks = {symbol: rank for rank, symbol in enumerate(ranked_symbols, start=1)}
    sorted_groups = sorted(
        groups.items(),
        key=lambda item: (symbol_ranks.get(item[0][0], 999999), item[0][2], item[0][1]),
    )

    for (symbol, exchange, half_key), group_rows in sorted_groups:
        group_rows.sort(key=lambda row: str(row.get("entry_fill_datetime") or row.get("entry_datetime") or ""))
        half_year_expected_return = trade_rows_expected_return(group_rows)
        start, end = _half_year_bounds(half_key)
        day_bars = load_symbol_bars(data_root, "day", symbol)
        panel_bars = {
            "weekly": _bars_for_period(aggregate_weekly_bars(day_bars), start, end),
            "daily": _bars_for_period(day_bars, start, end),
            "hourly": _bars_for_period(load_symbol_bars(data_root, "minute60", symbol), start, end),
        }
        rank = symbol_ranks.get(symbol, 999999)
        symbol_dir = f"{rank:03d}_{_safe_slug(symbol)}"
        rel_path = Path(symbol_dir) / f"{rank:03d}_{_safe_slug(symbol)}_{half_key}_{TARGET_SIGNAL_TYPE}.png"
        image_path = output_dir / rel_path
        image_path.parent.mkdir(parents=True, exist_ok=True)

        if all(panel_bars[panel["key"]].empty for panel in CHART_PANELS):
            skipped += 1
            render_status = "missing_bars"
        elif image_path.exists() and not overwrite:
            render_status = "existing"
        else:
            markers = [marker for row in group_rows for marker in build_trade_markers(row)]
            image = render_symbol_half_year_card(
                symbol,
                exchange,
                half_key,
                panel_bars,
                group_rows,
                markers,
                symbol_rank=rank,
                symbol_expected_return=half_year_expected_return,
            )
            image.save(image_path)
            rendered += 1
            render_status = "rendered"

        index_rows.append(
            {
                "symbol_rank": rank,
                "symbol": symbol,
                "exchange": exchange,
                "half_year": half_key,
                "symbol_expected_return": f"{half_year_expected_return:.6f}",
                "trade_rows": len(group_rows),
                "executed_rows": sum(1 for row in group_rows if row.get("execution_status") == "executed"),
                "image_path": str(rel_path) if render_status != "missing_bars" else "",
                "render_status": render_status,
            }
        )

    _write_index(output_dir / "index.csv", index_rows)
    summary = {
        "trade_csv": str(trade_csv),
        "output_dir": str(output_dir),
        "data_root": str(data_root),
        "signal_type": TARGET_SIGNAL_TYPE,
        "input_rows": len(rows),
        "groups": len(groups),
        "rendered_images": rendered,
        "skipped_images": skipped,
        "symbols": len({key[0] for key in groups}),
    }
    (output_dir / "render_summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return summary


def render_symbol_half_year_card(
    symbol: str,
    exchange: str,
    half_key: str,
    panel_bars: Mapping[str, pd.DataFrame],
    rows: list[Mapping[str, Any]],
    markers: list[Mapping[str, Any]],
    *,
    symbol_rank: int,
    symbol_expected_return: float,
) -> Image.Image:
    """Render one symbol half-year chart image."""
    image = Image.new("RGB", (WIDTH, HEIGHT), BACKGROUND)
    draw = ImageDraw.Draw(image)
    fonts = _Fonts()

    executed = sum(1 for row in rows if row.get("execution_status") == "executed")
    title = f"{symbol}.{exchange} {half_key} {TARGET_SIGNAL_TYPE}"
    subtitle = (
        f"symbol_rank={symbol_rank:03d} symbol_expected_return={symbol_expected_return:,.2f} "
        f"opportunities={len(rows)} executed={executed}"
    )
    draw.rectangle((0, 0, WIDTH, 78), fill="#223044")
    draw.text((24, 16), title, fill="#fff8e8", font=fonts.title)
    draw.text((24, 50), subtitle, fill="#dbe7ef", font=fonts.small)

    top = 104
    panel_height = 455
    gap = 20
    for i, panel in enumerate(CHART_PANELS):
        panel_top = top + i * (panel_height + gap)
        rect = (42, panel_top, WIDTH - 42, panel_top + panel_height)
        _draw_chart_panel(
            draw,
            rect,
            str(panel["title"]),
            panel_bars.get(str(panel["key"]), pd.DataFrame()),
            markers,
            fonts,
            date_only=bool(panel["date_only"]),
        )
    _draw_legend(draw, fonts)
    return image


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--trade-csv", type=Path, default=DEFAULT_TRADE_CSV)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--data-root", type=Path, default=Path("cta/data/feature"))
    parser.add_argument("--overwrite", action="store_true")
    args = parser.parse_args(argv)

    summary = render_symbol_bull_pullback_charts(
        trade_csv=args.trade_csv,
        output_dir=args.output_dir,
        data_root=args.data_root,
        overwrite=args.overwrite,
    )
    print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


def _marker_kind(side: object, action: object) -> str:
    side_text = str(side or "").lower()
    text = str(action or "").lower()
    if side_text == "short":
        return "short_buy" if text in {"buy", "cover"} else "short_sell"
    if side_text == "long":
        return "long_buy" if text in {"buy", "cover"} else "long_sell"
    if text == "cover":
        return "short_buy"
    if text == "short":
        return "short_sell"
    if text == "buy":
        return "long_buy"
    return "long_sell"


def _half_year_bounds(half_key: str) -> tuple[pd.Timestamp, pd.Timestamp]:
    year = int(half_key[:4])
    if half_key.endswith("H1"):
        return pd.Timestamp(year=year, month=1, day=1), pd.Timestamp(year=year, month=6, day=30, hour=23)
    return pd.Timestamp(year=year, month=7, day=1), pd.Timestamp(year=year, month=12, day=31, hour=23)


def _bars_for_period(bars: pd.DataFrame, start: pd.Timestamp, end: pd.Timestamp) -> pd.DataFrame:
    if bars.empty:
        return bars
    mask = (bars["datetime"] >= start) & (bars["datetime"] <= end)
    return bars.loc[mask].reset_index(drop=True)


def _draw_chart_panel(
    draw: ImageDraw.ImageDraw,
    rect: tuple[int, int, int, int],
    title: str,
    bars: pd.DataFrame,
    markers: list[Mapping[str, Any]],
    fonts: "_Fonts",
    *,
    date_only: bool,
) -> None:
    left, top, right, bottom = rect
    draw.rounded_rectangle(rect, radius=14, fill=PANEL_BG, outline="#e1d8c8")
    draw.text((left + 16, top + 10), title, fill=INK, font=fonts.body)

    required = {"datetime", "open", "high", "low", "close", "volume"}
    if bars.empty or not required.issubset(bars.columns):
        draw.text((left + 18, top + 52), "No local OHLCV data for this panel", fill=MUTED, font=fonts.body)
        return

    normalized = bars.sort_values("datetime").reset_index(drop=True)
    price_rect = (left + 64, top + 42, right - 20, bottom - 94)
    volume_rect = (left + 64, bottom - 76, right - 20, bottom - 30)
    _draw_grid(draw, price_rect, volume_rect)
    _draw_candles(draw, normalized, price_rect, markers)
    _draw_volume(draw, normalized, volume_rect)
    _draw_markers(draw, normalized, price_rect, volume_rect, markers, date_only=date_only)
    _draw_labels(draw, normalized, price_rect, volume_rect, fonts, date_only=date_only)


def _draw_grid(
    draw: ImageDraw.ImageDraw,
    price_rect: tuple[int, int, int, int],
    volume_rect: tuple[int, int, int, int],
) -> None:
    left, top, right, bottom = price_rect
    draw.rectangle(price_rect, fill=PANEL_BG, outline="#cdbfaaa0")
    for i in range(6):
        y = top + int((bottom - top) * i / 5)
        draw.line((left, y, right, y), fill=GRID)
    draw.rectangle(volume_rect, fill=PANEL_BG, outline="#cdbfaaa0")


def _draw_candles(
    draw: ImageDraw.ImageDraw,
    bars: pd.DataFrame,
    rect: tuple[int, int, int, int],
    markers: list[Mapping[str, Any]],
) -> None:
    low, high = _price_bounds(bars, markers)
    left, top, right, bottom = rect
    step = (right - left) / max(len(bars), 1)
    body_width = max(2, min(8, int(step * 0.62)))
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
    step = (right - left) / max(len(bars), 1)
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
    markers: list[Mapping[str, Any]],
    *,
    date_only: bool,
) -> None:
    low, high = _price_bounds(bars, markers)
    for marker in markers:
        dt = marker.get("datetime")
        price = marker.get("price")
        if not isinstance(dt, pd.Timestamp) or price is None:
            continue
        x = _marker_x(bars, dt, price_rect, date_only=date_only)
        if x is None:
            continue
        y = _marker_y(marker, price=float(price), low=low, high=high, price_rect=price_rect)
        color = str(marker["color"])
        if str(marker.get("kind", "")).endswith("_buy"):
            points = [(x, y - 10), (x - 7, y + 6), (x + 7, y + 6)]
        else:
            points = [(x, y + 10), (x - 7, y - 6), (x + 7, y - 6)]
        draw.polygon(points, fill=color)
        width = 2 if marker.get("status") == "executed" else 1
        line_top = min(y, price_rect[1])
        draw.line((x, line_top, x, volume_rect[3]), fill=color, width=width)


def _draw_labels(
    draw: ImageDraw.ImageDraw,
    bars: pd.DataFrame,
    price_rect: tuple[int, int, int, int],
    volume_rect: tuple[int, int, int, int],
    fonts: "_Fonts",
    *,
    date_only: bool,
) -> None:
    high = float(bars["high"].max())
    low = float(bars["low"].min())
    draw.text((price_rect[0] - 66, price_rect[1] - 4), f"{high:,.2f}", fill=MUTED, font=fonts.small)
    draw.text((price_rect[0] - 66, price_rect[3] - 12), f"{low:,.2f}", fill=MUTED, font=fonts.small)
    date_format = "%Y-%m-%d" if date_only else "%m-%d %H:%M"
    first_dt = pd.Timestamp(bars.iloc[0]["datetime"]).strftime(date_format)
    last_dt = pd.Timestamp(bars.iloc[-1]["datetime"]).strftime(date_format)
    draw.text((price_rect[0], volume_rect[3] + 12), first_dt, fill=MUTED, font=fonts.small)
    draw.text((price_rect[2] - 86, volume_rect[3] + 12), last_dt, fill=MUTED, font=fonts.small)
    draw.text((price_rect[0] - 48, volume_rect[1] + 44), "Vol", fill=MUTED, font=fonts.small)


def _draw_legend(draw: ImageDraw.ImageDraw, fonts: "_Fonts") -> None:
    y = HEIGHT - 58
    x = 28
    for kind in ("long_buy", "long_sell", "short_buy", "short_sell"):
        color = MARKER_STYLES[kind]["color"]
        label = MARKER_STYLES[kind]["label"]
        if kind.endswith("_buy"):
            points = [(x + 8, y - 9), (x, y + 7), (x + 16, y + 7)]
        else:
            points = [(x + 8, y + 9), (x, y - 7), (x + 16, y - 7)]
        draw.polygon(points, fill=color)
        draw.line((x + 8, y - 18, x + 8, y + 18), fill=color, width=2)
        draw.text((x + 26, y - 8), label, fill=INK, font=fonts.body)
        x += 178
    draw.text((x + 8, y - 8), "lifted marker + thicker line = executed", fill=MUTED, font=fonts.body)


def _price_bounds(bars: pd.DataFrame, markers: list[Mapping[str, Any]]) -> tuple[float, float]:
    low = float(bars["low"].min())
    high = float(bars["high"].max())
    marker_prices = [float(marker["price"]) for marker in markers if marker.get("price") is not None]
    if marker_prices:
        low = min(low, min(marker_prices))
        high = max(high, max(marker_prices))
    if high == low:
        high += 1
        low -= 1
    padding = (high - low) * 0.05
    return low - padding, high + padding


def _scale_price(value: float, low: float, high: float, top: int, bottom: int) -> int:
    return int(bottom - (value - low) / (high - low) * (bottom - top))


def _marker_y(
    marker: Mapping[str, Any],
    *,
    price: float,
    low: float,
    high: float,
    price_rect: tuple[int, int, int, int],
) -> int:
    if marker.get("status") == "executed":
        return price_rect[1] - EXECUTED_MARKER_Y_OFFSET
    return _scale_price(price, low, high, price_rect[1], price_rect[3])


def _marker_x(
    bars: pd.DataFrame,
    dt: pd.Timestamp,
    rect: tuple[int, int, int, int],
    *,
    date_only: bool,
) -> int | None:
    times = bars["datetime"]
    point = pd.Timestamp(year=dt.year, month=dt.month, day=dt.day) if date_only else dt
    if point > times.iloc[-1]:
        return None
    pos = 0 if point < times.iloc[0] else int(times.searchsorted(point, side="left"))
    if pos >= len(bars):
        pos = len(bars) - 1
    left, _, right, _ = rect
    step = (right - left) / max(len(bars), 1)
    return int(left + step * (pos + 0.5))


def _write_index(path: Path, rows: list[dict[str, Any]]) -> None:
    fieldnames = [
        "symbol_rank",
        "symbol",
        "exchange",
        "half_year",
        "symbol_expected_return",
        "trade_rows",
        "executed_rows",
        "image_path",
        "render_status",
    ]
    with path.open("w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def _clear_generated_outputs(output_dir: Path) -> None:
    for path in output_dir.rglob("*.png"):
        path.unlink()
    for name in ("index.csv", "render_summary.json"):
        path = output_dir / name
        if path.exists():
            path.unlink()
    directories = [path for path in output_dir.rglob("*") if path.is_dir()]
    for directory in sorted(directories, key=lambda path: len(path.parts), reverse=True):
        try:
            directory.rmdir()
        except OSError:
            continue


def _safe_slug(value: object) -> str:
    text = str(value).strip() or "na"
    text = re.sub(r"[^A-Za-z0-9]+", "_", text)
    return text.strip("_") or "na"


class _Fonts:
    def __init__(self) -> None:
        self.title = _load_font(26)
        self.body = _load_font(14)
        self.small = _load_font(12)


def _load_font(size: int) -> ImageFont.ImageFont:
    candidates = [
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    ]
    for path in candidates:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default()


if __name__ == "__main__":
    raise SystemExit(main())
