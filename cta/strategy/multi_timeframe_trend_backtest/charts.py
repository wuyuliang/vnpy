"""Render one daily/1-hour/5-minute audit chart per strategy opportunity."""
from __future__ import annotations

from collections.abc import Mapping
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
from .aggregation_cache import AggregationCache, cached_aggregate_completed_bars


_CARD_SIZE = (1680, 1240)
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
        ),
        (
            "Completed 1-hour actual-contract OHLC",
            _event_window(contexts["hourly"], signal, radius=24),
            True,
        ),
        (
            "Completed 5-minute actual-contract OHLC",
            _event_window(contexts["five"], signal, radius=80),
            True,
        ),
    )
    if any(frame.empty for _, frame, _ in panels):
        raise ValueError("opportunity chart requires non-empty daily, 1h, and 5m data")
    image = Image.new("RGBA", _CARD_SIZE, CHART_BACKGROUND)
    draw = ImageDraw.Draw(image)
    font = ImageFont.load_default()
    direction = "LONG" if int(candidate["direction"]) > 0 else "SHORT"
    draw.text(
        (42, 18),
        (
            f"Opportunity {sequence:04d} | {candidate.get('symbol', '')} "
            f"{candidate.get('contract_code', '')} | "
            f"{candidate['setup_type']} {direction}"
        ),
        fill=CHART_INK,
        font=font,
    )
    draw.text(
        (42, 42),
        f"candidate_id={candidate['candidate_id']} | signal={signal.isoformat()}",
        fill=CHART_MUTED,
        font=font,
    )
    draw.text(
        (42, 64),
        (
            f"entry={_level(candidate, 'entry', 'trigger')} | "
            f"stop={_level(candidate, 'stop', 'stop_price')} | "
            f"target={_level(candidate, 'target', 'target_price')} | "
            f"outcome={candidate.get('outcome_code', 'UNKNOWN')}"
        ),
        fill="#9f2d24",
        font=font,
    )
    draw.text(
        (42, 86),
        "Bars right of SIGNAL are post-event review only.",
        fill="#8a5a12",
        font=font,
    )
    levels = (
        _numeric_level(candidate, "entry", "trigger"),
        _numeric_level(candidate, "stop", "stop_price"),
        _numeric_level(candidate, "target", "target_price"),
    )
    for rect, (label, frame, show_time) in zip(_PANEL_RECTS, panels, strict=True):
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
        _draw_overlay(image, rect, frame, signal=signal, candidate=candidate)
        draw = ImageDraw.Draw(image)
    return image.convert("RGB")


def _draw_overlay(
    image: Image.Image,
    rect: tuple[int, int, int, int],
    frame: pd.DataFrame,
    *,
    signal: pd.Timestamp,
    candidate: Mapping[str, Any],
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
    draw.text((signal_x + 5, chart[1] + 4), "SIGNAL", fill="#2563eb")

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
    return f"{value:,.4f}" if math_isfinite(value) else "N/A"


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
