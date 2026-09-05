"""Render one daily/1-hour/5-minute audit chart per strategy opportunity."""
from __future__ import annotations

from collections.abc import Mapping
from functools import lru_cache
import math
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd
from PIL import Image, ImageDraw, ImageFont

from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSpec,
    aggregate_completed_bars,
)
from cta.strategy.brooks.scalp.report import (
    CHART_BACKGROUND,
    CHART_INK,
    CHART_MUTED,
    _draw_candlestick_panel,
)
from cta.config.futures_display_names import labelled_symbol
from .aggregation_cache import AggregationCache, cached_aggregate_completed_bars


_CARD_SIZE = (1680, 1240)
# 三行表头按要求比原来的 PIL 位图默认字体放大一倍
_HEADER_FONT_SIZE = 22
_HEADER_LINE_HEIGHT = 32
_PANEL_LABEL_FONT_SIZE = 14
# 图上叫得出名字的形态，比 always_in LONG 直观
_SETUP_LABELS = {
    ("ALWAYS_IN", 1): "趋势回调",
    ("ALWAYS_IN", -1): "趋势反弹",
}
# 中文名要有能画汉字的字体，否则只会画出一排豆腐块；找不到就退回纯代码
_CJK_FONT_CANDIDATES = (
    "/usr/share/fonts/opentype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/opentype/noto/NotoSansCJKsc-Regular.otf",
    "/usr/share/fonts/truetype/noto/NotoSansCJK-Regular.ttc",
    "/usr/share/fonts/truetype/wqy/wqy-microhei.ttc",
    "/usr/share/fonts/truetype/arphic/uming.ttc",
    "/System/Library/Fonts/PingFang.ttc",
    "/System/Library/Fonts/Hiragino Sans GB.ttc",
    "/Library/Fonts/Arial Unicode.ttf",
    "C:/Windows/Fonts/msyh.ttc",
    "C:/Windows/Fonts/simhei.ttf",
)
_PANEL_RECTS = (
    (42, 124, 1638, 452),
    (42, 472, 1638, 800),
    (42, 820, 1638, 1148),
)


def render_opportunity_charts(
    report_dir: str | Path,
    *,
    candidates: pd.DataFrame,
    daily_bars: pd.DataFrame,
    five_minute_bars: pd.DataFrame,
    trades: pd.DataFrame,
    orders: pd.DataFrame,
    rejections: pd.DataFrame,
    render_outcomes: str = "traded",
    daily_equity: pd.DataFrame | None = None,
    hourly_bars: pd.DataFrame | None = None,
    minute_bars_by_symbol: Mapping[str, pd.DataFrame] | None = None,
    sessions_by_symbol: Mapping[str, tuple[SessionSpec, ...]] | None = None,
    aggregation_cache: AggregationCache | None = None,
) -> pd.DataFrame:
    """Write review cards for the selected outcomes; index every candidate.

    ``render_outcomes`` controls which candidates get a PNG:

    - ``traded``  只给成交的机会出图（默认）
    - ``all``     每个候选都出图（旧行为，很慢：一次 40 品种回测约 1.5 万张）
    - ``none``    完全不出图

    `index.csv` 始终包含**全部**候选及其 outcome_code，未出图的行 `chart_path` 为空，
    所以漏斗审计不会因为少出图而缺失。
    """
    allowed_modes = {"traded", "all", "none"}
    if render_outcomes not in allowed_modes:
        raise ValueError(
            f"render_outcomes must be one of {sorted(allowed_modes)}, "
            f"got {render_outcomes!r}"
        )
    report = Path(report_dir)
    output = report / "opportunity_charts"
    output.mkdir(parents=True, exist_ok=False)
    if candidates.empty:
        index = pd.DataFrame(
            columns=["sequence", "candidate_id", "signal_time", "chart_path"]
        )
        index.to_csv(output / "index.csv", index=False)
        (report / "OPPORTUNITY_CHARTS.md").write_text(
            "# 每次机会图表\n\n本次回测没有生成策略机会。\n",
            encoding="utf-8",
        )
        return index

    required = {"candidate_id", "signal_time", "setup_type", "direction"}
    missing = sorted(required.difference(candidates.columns))
    if missing:
        raise ValueError("chart candidates are missing: " + ",".join(missing))
    outcomes = _candidate_outcomes(
        candidates,
        trades=trades,
        orders=orders,
        rejections=rejections,
    )
    traded_exit_prices = _traded_exit_prices(trades)
    trade_facts = _traded_trade_facts(trades, daily_equity=daily_equity)
    rows: list[dict[str, Any]] = []
    ordered = candidates.copy()
    ordered["signal_time"] = pd.to_datetime(ordered["signal_time"], errors="raise")
    ordered = ordered.sort_values(["signal_time", "candidate_id"], kind="stable")
    # 每个品种的三条上下文只切一次。原本放在候选循环里，
    # 一次 40 品种回测会对同一批 K 线重复过滤上万遍。
    context_cache: dict[str, dict[str, pd.DataFrame]] = {}

    def _hourly_for(symbol: str) -> pd.DataFrame:
        if hourly_bars is not None:
            return _symbol_bars(hourly_bars, symbol)
        if minute_bars_by_symbol is None or sessions_by_symbol is None:
            raise ValueError(
                "chart hourly bars require either hourly_bars or minute/session mappings"
            )
        if symbol not in minute_bars_by_symbol or symbol not in sessions_by_symbol:
            raise ValueError(f"chart hourly source is missing for {symbol}")
        minute = minute_bars_by_symbol[symbol]
        sessions = sessions_by_symbol[symbol]
        if aggregation_cache is None:
            return aggregate_completed_bars(
                minute,
                minutes=60,
                sessions=sessions,
            )
        return cached_aggregate_completed_bars(
            aggregation_cache,
            minute,
            minutes=60,
            sessions=sessions,
            compute=aggregate_completed_bars,
        )

    def _contexts_for(symbol: str) -> dict[str, pd.DataFrame]:
        cached = context_cache.get(symbol)
        if cached is None:
            cached = {
                "daily": _chart_frame(_symbol_bars(daily_bars, symbol)),
                "hourly": _chart_frame(_hourly_for(symbol)),
                "five": _chart_frame(_symbol_bars(five_minute_bars, symbol)),
            }
            context_cache[symbol] = cached
        return cached

    for sequence, candidate in enumerate(ordered.to_dict("records"), start=1):
        signal = _aware_timestamp(candidate["signal_time"])
        direction = "LONG" if int(candidate["direction"]) > 0 else "SHORT"
        symbol = _safe_component(str(candidate.get("symbol", "UNKNOWN")))
        outcome = outcomes[str(candidate["candidate_id"])]
        should_render = render_outcomes == "all" or (
            render_outcomes == "traded" and outcome == "TRADED"
        )
        chart_candidate = {**candidate, "outcome_code": outcome}
        candidate_id = str(candidate["candidate_id"])
        chart_candidate["target"] = _numeric_level(
            chart_candidate,
            "target_price_virtual",
            "target_price",
            "target",
        )
        if candidate_id in traded_exit_prices:
            chart_candidate["target"] = traded_exit_prices[candidate_id]
        chart_candidate.update(trade_facts.get(candidate_id, {}))
        chart_path = ""
        if should_render:
            outcome_dir = output / outcome
            outcome_dir.mkdir(parents=True, exist_ok=True)
            filename = (
                f"{sequence:04d}_{symbol}_{signal:%Y%m%d_%H%M%S}_"
                f"{candidate['setup_type']}_{direction}.png"
            )
            image = _render_card(
                chart_candidate, sequence=sequence, contexts=_contexts_for(symbol)
            )
            image.save(outcome_dir / filename, format="PNG")
            chart_path = (
                Path("opportunity_charts") / outcome / filename
            ).as_posix()
        rows.append(
            {
                **chart_candidate,
                "sequence": sequence,
                "outcome_code": outcome,
                "chart_path": chart_path,
            }
        )
    index = pd.DataFrame(rows)
    index.to_csv(output / "index.csv", index=False)
    (report / "OPPORTUNITY_CHARTS.md").write_text(
        _chart_guide(index),
        encoding="utf-8",
    )
    return index


def _render_card(
    candidate: Mapping[str, Any],
    *,
    sequence: int,
    contexts: Mapping[str, pd.DataFrame],
) -> Image.Image:
    signal = _aware_timestamp(candidate["signal_time"])
    panels = (
        (
            "Daily actual-contract OHLC",
            _event_window(contexts["daily"], signal, radius=20),
            False,
            "%m%d",
        ),
        (
            "Completed 1-hour actual-contract OHLC",
            _event_window(contexts["hourly"], signal, radius=24),
            True,
            "%m-%d %H",
        ),
        (
            "Completed 5-minute actual-contract OHLC",
            _event_window(contexts["five"], signal, radius=80),
            True,
            "%m-%d %H:%M",
        ),
    )
    if any(frame.empty for _, frame, _, _ in panels):
        raise ValueError("opportunity chart requires non-empty daily, 1h, and 5m data")
    image = Image.new("RGBA", _CARD_SIZE, CHART_BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = _font(_HEADER_FONT_SIZE)
    entry_time = str(candidate.get("trade_entry_time", "") or "")
    exit_time = str(candidate.get("trade_exit_time", "") or "")
    # 成交了就报真实成交时间：信号 10:45 触发、10:48 才成交是常态，
    # 把信号时间挂上"买入"两个字会读错一刻钟
    second_line = f"candidate_id={candidate['candidate_id']}"
    second_line += (
        f" | 买入={entry_time}" if entry_time else f" | signal={signal.isoformat()}"
    )
    if exit_time:
        second_line += f" | 卖出={exit_time}"
    third_line = (
        f"entry={_level(candidate, 'entry', 'trigger')} | "
        f"stop={_level(candidate, 'stop', 'stop_price')} | "
        f"target={_level(candidate, 'target', 'target_price')}"
    )
    trade_summary = _trade_summary(candidate)
    if trade_summary:
        third_line += f" | {trade_summary}"
    header = (
        (
            f"Opportunity {sequence:04d} | "
            f"{_symbol_label(candidate.get('symbol', ''))} "
            f"{candidate.get('contract_code', '')} | "
            f"{_setup_label(candidate['setup_type'], candidate['direction'])}",
            CHART_INK,
        ),
        (second_line, CHART_MUTED),
        (third_line, "#9f2d24"),
    )
    for offset, (text, colour) in enumerate(header):
        draw.text(
            (42, 14 + offset * _HEADER_LINE_HEIGHT),
            text,
            fill=colour,
            font=font,
        )
    levels = (
        _numeric_level(candidate, "entry", "trigger"),
        _numeric_level(candidate, "stop", "stop_price"),
        _numeric_level(candidate, "target", "target_price"),
    )
    exit_moment = _aware_timestamp(exit_time) if exit_time else None
    for rect, (label, frame, show_time, time_format) in zip(
        _PANEL_RECTS, panels, strict=True
    ):
        _draw_candlestick_panel(
            draw,
            rect,
            frame,
            label=f"{label} ({len(frame)} bars)",
            show_time=show_time,
            plot_slots=frame["_plot_slot"].to_numpy(),
            plot_slot_count=int(frame.attrs["plot_slot_count"]),
            price_levels=levels,
        )
        _draw_overlay(
            image,
            rect,
            frame,
            signal=signal,
            candidate=candidate,
            exit_moment=exit_moment,
            time_format=time_format,
        )
        draw = ImageDraw.Draw(image)
    return image.convert("RGB")


def _draw_overlay(
    image: Image.Image,
    rect: tuple[int, int, int, int],
    frame: pd.DataFrame,
    *,
    signal: pd.Timestamp,
    candidate: Mapping[str, Any],
    exit_moment: pd.Timestamp | None = None,
    time_format: str = "%m-%d %H:%M",
) -> None:
    left, top, right, bottom = rect
    chart = (left + 68, top + 30, right - 18, bottom - 28)
    slot_count = int(frame.attrs["plot_slot_count"])
    signal_slot = int(frame.attrs["signal_slot"])
    signal_x = chart[0] + (signal_slot + 0.5) / slot_count * (
        chart[2] - chart[0]
    )
    overlay = Image.new("RGBA", image.size, (0, 0, 0, 0))
    shade = ImageDraw.Draw(overlay)
    shade.rectangle((signal_x, chart[1], chart[2], chart[3]), fill=(214, 166, 68, 32))
    image.alpha_composite(overlay)
    draw = ImageDraw.Draw(image)
    for y in range(chart[1], chart[3], 10):
        draw.line((signal_x, y, signal_x, min(y + 5, chart[3])), fill="#2563eb", width=2)
    label_font = _font(_PANEL_LABEL_FONT_SIZE)
    # SIGNAL 的名字和时刻贴在竖线**左**侧，EXIT 的贴在**右**侧：这样两条线即使
    # 落在同一根 K 上，两组标签也各据一边，不需要再纵向错行去躲。
    _draw_marker_label(
        draw,
        x=signal_x,
        top=chart[1],
        lines=("SIGNAL", f"{signal:{time_format}}"),
        colour="#2563eb",
        font=label_font,
        to_the_left=True,
    )
    if exit_moment is not None:
        exit_x = _slot_x(frame, exit_moment, chart)
        if exit_x is not None:
            for y in range(chart[1], chart[3], 10):
                draw.line(
                    (exit_x, y, exit_x, min(y + 5, chart[3])),
                    fill="#7c3aed",
                    width=2,
                )
            _draw_marker_label(
                draw,
                x=exit_x,
                top=chart[1],
                lines=("EXIT", f"{exit_moment:{time_format}}"),
                colour="#7c3aed",
                font=label_font,
                to_the_left=False,
            )

    levels = (
        (_numeric_level(candidate, "entry", "trigger"), "ENTRY", "#2563eb"),
        (_numeric_level(candidate, "stop", "stop_price"), "STOP", "#b42318"),
        (_numeric_level(candidate, "target", "target_price"), "TARGET", "#16835f"),
    )
    low, high = _panel_price_bounds(
        frame,
        tuple(value for value, _, _ in levels),
    )
    for value, label, color in levels:
        if not math_isfinite(value):
            continue
        y = chart[3] - (value - low) / (high - low) * (chart[3] - chart[1])
        draw.line((chart[0], y, chart[2], y), fill=color, width=1)
        draw.text((chart[2] - 100, y - 10), label, fill=color)


@lru_cache(maxsize=1)
def _cjk_font_path() -> str:
    """First installed CJK font, or ``""`` when this machine has none."""
    for path in _CJK_FONT_CANDIDATES:
        if Path(path).is_file():
            return path
    return ""


@lru_cache(maxsize=8)
def _font(size: int) -> ImageFont.ImageFont:
    path = _cjk_font_path()
    if path:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            pass
    try:
        return ImageFont.load_default(size=size)
    except TypeError:  # Pillow < 10.1 只有固定大小的位图字体
        return ImageFont.load_default()


def _symbol_label(symbol: str) -> str:
    """``AG`` -> ``白银 AG``，没有中文字体时退回 ``AG``。"""
    return labelled_symbol(symbol) if _cjk_font_path() else str(symbol)


def _trade_summary(candidate: Mapping[str, Any]) -> str:
    """The traded leg's size, money and account impact, for the header."""
    quantity = _numeric_level(candidate, "trade_quantity")
    net_pnl = _numeric_level(candidate, "trade_net_pnl")
    if not (math_isfinite(quantity) and math_isfinite(net_pnl)):
        return ""
    parts = [f"手数={quantity:,.0f}", f"净盈亏={net_pnl:+,.2f}"]
    notional_pct = _numeric_level(candidate, "trade_notional_return_pct")
    if math_isfinite(notional_pct):
        parts[-1] += f" ({notional_pct:+.2f}% 名义)"
    equity_pct = _numeric_level(candidate, "trade_prior_equity_return_pct")
    if math_isfinite(equity_pct):
        parts.append(f"占前一日权益={equity_pct:+.3f}%")
    else:
        parts.append("占前一日权益=N/A")
    return " | ".join(parts)


def _setup_label(setup_type: object, direction: int) -> str:
    """``always_in`` + LONG -> ``趋势回调``; unknown setups keep the raw code."""
    side = 1 if int(direction) > 0 else -1
    label = _SETUP_LABELS.get((str(setup_type).strip().upper(), side), "")
    if label and _cjk_font_path():
        return label
    return f"{setup_type} {'LONG' if side > 0 else 'SHORT'}"


def _draw_marker_label(
    draw: ImageDraw.ImageDraw,
    *,
    x: float,
    top: float,
    lines: tuple[str, ...],
    colour: str,
    font: Any,
    to_the_left: bool,
) -> None:
    """Stack a marker name over its timestamp, hugging one side of its line."""
    row = _PANEL_LABEL_FONT_SIZE + 2
    for offset, line in enumerate(lines):
        width = float(draw.textlength(line, font=font))
        anchor_x = x - width - 5 if to_the_left else x + 5
        draw.text(
            (anchor_x, top + 4 + offset * row),
            line,
            fill=colour,
            font=font,
        )


def _slot_x(
    frame: pd.DataFrame,
    moment: pd.Timestamp,
    chart: tuple[float, float, float, float],
) -> float | None:
    """X pixel for ``moment`` inside this panel, or None when off-window."""
    index = frame.index
    position = int(index.searchsorted(moment, side="left"))
    if position >= len(index):
        return None
    slot = float(frame["_plot_slot"].to_numpy()[position])
    slot_count = int(frame.attrs["plot_slot_count"])
    return chart[0] + (slot + 0.5) / slot_count * (chart[2] - chart[0])


def _chart_frame(frame: pd.DataFrame) -> pd.DataFrame:
    required = {"bar_end", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("chart bars are missing: " + ",".join(missing))
    result = frame.loc[:, ["bar_end", "open", "high", "low", "close", "volume"]].copy()
    result["bar_end"] = pd.to_datetime(result["bar_end"], errors="raise")
    if result["bar_end"].dt.tz is None:
        raise ValueError("chart bar_end must be timezone-aware")
    for column in ("open", "high", "low", "close", "volume"):
        result[column] = pd.to_numeric(result[column], errors="raise")
    return (
        result.sort_values("bar_end", kind="stable")
        .drop_duplicates("bar_end", keep="last")
        .set_index("bar_end")
    )


def _symbol_bars(frame: pd.DataFrame, symbol: str) -> pd.DataFrame:
    if "symbol" not in frame:
        return frame
    return frame.loc[frame["symbol"].astype(str).map(_safe_component).eq(symbol)]


def _candidate_outcomes(
    candidates: pd.DataFrame,
    *,
    trades: pd.DataFrame,
    orders: pd.DataFrame,
    rejections: pd.DataFrame,
) -> dict[str, str]:
    traded = (
        set(trades["candidate_id"].astype(str))
        if "candidate_id" in trades
        else set()
    )
    rejected: dict[str, str] = {}
    if {"candidate_id", "reason_code"}.issubset(rejections):
        ordered_rejections = rejections.copy()
        if "feature_asof" in ordered_rejections:
            ordered_rejections = ordered_rejections.sort_values(
                "feature_asof", kind="stable"
            )
        for row in ordered_rejections.to_dict("records"):
            rejected[str(row["candidate_id"])] = str(row["reason_code"])
    final_orders: dict[str, str] = {}
    if {"candidate_id", "reason"}.issubset(orders):
        ordered_orders = orders.copy()
        if "event_time" in ordered_orders:
            ordered_orders = ordered_orders.sort_values("event_time", kind="stable")
        for row in ordered_orders.to_dict("records"):
            reason = str(row.get("reason", "") or row.get("status", "")).strip()
            if reason:
                final_orders[str(row["candidate_id"])] = reason

    outcomes: dict[str, str] = {}
    for candidate in candidates.to_dict("records"):
        candidate_id = str(candidate["candidate_id"])
        if candidate_id in traded:
            reason = "TRADED"
        elif candidate_id in rejected:
            reason = rejected[candidate_id]
        elif candidate_id in final_orders:
            reason = final_orders[candidate_id]
        else:
            reason = str(candidate.get("filtered_reason", "") or "").strip()
            if not reason:
                reason = "NOT_FILLED_UNKNOWN"
        outcomes[candidate_id] = _safe_component(reason.upper())
    return outcomes


def _traded_exit_prices(trades: pd.DataFrame) -> dict[str, float]:
    if trades.empty:
        return {}
    required = {"candidate_id", "exit_price"}
    missing = sorted(required.difference(trades.columns))
    if missing:
        raise ValueError("chart trades are missing: " + ",".join(missing))
    if trades["candidate_id"].isna().any():
        raise ValueError("chart trade candidate_id must not be missing")
    duplicate_ids = trades.loc[
        trades["candidate_id"].duplicated(keep=False), "candidate_id"
    ].astype(str)
    if not duplicate_ids.empty:
        raise ValueError(
            "chart trades contain duplicate candidate_id: "
            + ",".join(sorted(set(duplicate_ids)))
        )
    prices: dict[str, float] = {}
    for row in trades.to_dict("records"):
        candidate_id = str(row["candidate_id"])
        try:
            value = float(row["exit_price"])
        except (TypeError, ValueError) as exc:
            raise ValueError(
                f"chart trade exit_price must be finite: {candidate_id}"
            ) from exc
        if not math_isfinite(value):
            raise ValueError(f"chart trade exit_price must be finite: {candidate_id}")
        prices[candidate_id] = value
    return prices


def _prior_equity_by_date(daily_equity: pd.DataFrame | None) -> pd.Series:
    """``date -> equity`` for the previous row, used as the header denominator.

    Indexed by the date whose *prior* session the equity belongs to, so a trade
    on 2026-01-26 reads the close of the session before it. Yesterday's equity
    is the only denominator that exists for every trade: ``margin_used`` is 0
    whenever nothing was held overnight.
    """
    if daily_equity is None or daily_equity.empty:
        return pd.Series(dtype=float)
    required = {"date", "equity"}
    if not required.issubset(daily_equity.columns):
        return pd.Series(dtype=float)
    frame = daily_equity.loc[:, ["date", "equity"]].copy()
    frame["date"] = pd.to_datetime(frame["date"], errors="coerce")
    frame["equity"] = pd.to_numeric(frame["equity"], errors="coerce")
    frame = frame.dropna().sort_values("date", kind="stable")
    if frame.empty:
        return pd.Series(dtype=float)
    return pd.Series(
        frame["equity"].shift(1).to_numpy(),
        index=frame["date"].dt.date.to_numpy(),
    )


def _traded_trade_facts(
    trades: pd.DataFrame,
    *,
    daily_equity: pd.DataFrame | None,
) -> dict[str, dict[str, Any]]:
    """Per-candidate exit time, size and P&L for the chart header."""
    if trades.empty:
        return {}
    prior_equity = _prior_equity_by_date(daily_equity)
    facts: dict[str, dict[str, Any]] = {}
    for row in trades.to_dict("records"):
        candidate_id = str(row.get("candidate_id", ""))
        if not candidate_id:
            continue
        entry = pd.to_datetime(row.get("entry_time"), errors="coerce")
        net_pnl = pd.to_numeric(row.get("net_pnl"), errors="coerce")
        quantity = pd.to_numeric(row.get("quantity"), errors="coerce")
        entry_price = pd.to_numeric(row.get("entry_price"), errors="coerce")
        multiplier = pd.to_numeric(row.get("contract_multiplier"), errors="coerce")
        exit_time = pd.to_datetime(row.get("exit_time"), errors="coerce")
        notional_pct = math.nan
        notional = float(entry_price or 0) * float(multiplier or 0) * float(
            quantity or 0
        )
        if pd.notna(net_pnl) and math_isfinite(notional) and notional > 0:
            notional_pct = float(net_pnl) / notional * 100.0
        equity_pct = math.nan
        if pd.notna(net_pnl) and pd.notna(entry) and not prior_equity.empty:
            base = prior_equity.get(entry.date(), math.nan)
            base = float(base) if pd.notna(base) else math.nan
            if math_isfinite(base) and base > 0:
                equity_pct = float(net_pnl) / base * 100.0
        facts[candidate_id] = {
            "trade_entry_time": "" if pd.isna(entry) else str(entry),
            "trade_exit_time": "" if pd.isna(exit_time) else str(exit_time),
            "trade_quantity": float(quantity) if pd.notna(quantity) else math.nan,
            "trade_net_pnl": float(net_pnl) if pd.notna(net_pnl) else math.nan,
            "trade_notional_return_pct": notional_pct,
            "trade_prior_equity_return_pct": equity_pct,
        }
    return facts


def _safe_component(value: str) -> str:
    cleaned = "".join(
        character if character.isalnum() or character in {"-", "_"} else "_"
        for character in value.strip()
    )
    return cleaned or "UNKNOWN"


def _event_window(
    frame: pd.DataFrame,
    signal: pd.Timestamp,
    *,
    radius: int,
) -> pd.DataFrame:
    if radius <= 0:
        raise ValueError("chart radius must be positive")
    insertion = int(frame.index.searchsorted(signal, side="left"))
    exact = insertion < len(frame) and frame.index[insertion] == signal
    if exact:
        start = max(0, insertion - radius)
        stop = min(len(frame), insertion + radius + 1)
        positions = np.arange(start, stop, dtype=int)
        slots = positions - insertion + radius
    else:
        start = max(0, insertion - radius)
        stop = min(len(frame), insertion + radius)
        positions = np.arange(start, stop, dtype=int)
        slots = positions - insertion + radius
        slots = slots + (positions >= insertion).astype(int)
    result = frame.iloc[start:stop].copy()
    result["_plot_slot"] = slots
    result.attrs["signal_slot"] = radius
    result.attrs["plot_slot_count"] = 2 * radius + 1
    return result


def _panel_price_bounds(
    frame: pd.DataFrame,
    price_levels: tuple[float, ...],
) -> tuple[float, float]:
    finite_levels = [float(value) for value in price_levels if math_isfinite(value)]
    low = min([float(frame["low"].min()), *finite_levels])
    high = max([float(frame["high"].max()), *finite_levels])
    if high == low:
        padding = max(abs(high) * 0.01, 1.0)
        low -= padding
        high += padding
    return low, high


def _level(candidate: Mapping[str, Any], *names: str) -> str:
    value = _numeric_level(candidate, *names)
    return f"{value:,.2f}" if math_isfinite(value) else "N/A"


def _numeric_level(
    candidate: Mapping[str, Any],
    *names: str,
) -> float:
    for name in names:
        try:
            value = float(candidate.get(name, np.nan))
        except (TypeError, ValueError):
            continue
        if math_isfinite(value):
            return value
    return math.nan


def math_isfinite(value: float) -> bool:
    return bool(np.isfinite(value))


def _aware_timestamp(value: object) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        raise ValueError("chart timestamp is invalid")
    if parsed.tzinfo is None:
        return parsed.tz_localize("Asia/Shanghai")
    return parsed.tz_convert("Asia/Shanghai")


def _chart_guide(index: pd.DataFrame) -> str:
    lines = [
        "# 每次机会图表",
        "",
        f"本次回测共记录 {len(index)} 次策略机会，并生成 {len(index)} 张图。",
        "",
        "图中包含实际合约日线、1 小时线和 5 分钟线。`SIGNAL` 右侧仅供事后复盘。",
        "默认只为 `TRADED` 的机会出图（`--chart-outcomes`）；"
        "未出图的候选在本表和 `index.csv` 里仍然完整列出，图表列显示 `—`。",
        "成交机会的 Target 使用 `trades.csv` 中的最终加权 `exit_price`；"
        "未成交机会继续使用虚拟 2R `target_price_virtual`。",
        "",
        "| # | 信号时间 | 策略 | 方向 | 状态 | 图表 |",
        "|---:|---|---|---|---|---|",
    ]
    for row in index.to_dict("records"):
        direction = "多" if int(row["direction"]) > 0 else "空"
        status = str(row.get("outcome_code", ""))
        path = str(row.get("chart_path", "") or "")
        link = f"[{Path(path).name}]({path})" if path else "—"
        lines.append(
            f"| {int(row['sequence'])} | {row['signal_time']} | "
            f"{row['setup_type']} | {direction} | `{status}` | {link} |"
        )
    lines.append("")
    return "\n".join(lines)


__all__ = ["render_opportunity_charts"]
