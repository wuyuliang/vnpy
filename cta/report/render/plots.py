"""Plotly 图表生成（与 cta/report/render/html_report.py 配套）。"""
from __future__ import annotations

from typing import Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go


def equity_curve_fig(
    equity: pd.Series,
    *,
    dates: Sequence[pd.Timestamp] | None = None,
    title: str = "Equity Curve",
) -> go.Figure:
    eq = equity.astype(float).reset_index(drop=True)
    x = list(pd.DatetimeIndex(pd.to_datetime(list(dates)))) if dates is not None else list(range(len(eq)))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=eq.tolist(), mode="lines", name="equity"))
    fig.update_layout(title=title, xaxis_title="bar", yaxis_title="equity (PnL units)")
    return fig


def drawdown_fig(
    equity: pd.Series,
    *,
    dates: Sequence[pd.Timestamp] | None = None,
    title: str = "Drawdown",
) -> go.Figure:
    eq = equity.astype(float).reset_index(drop=True)
    peak = eq.cummax()
    dd = (eq - peak).astype(float)  # 负数（回撤金额）
    x = list(pd.DatetimeIndex(pd.to_datetime(list(dates)))) if dates is not None else list(range(len(eq)))
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=x, y=dd.tolist(), mode="lines", fill="tozeroy", name="drawdown"))
    fig.update_layout(title=title, xaxis_title="bar", yaxis_title="drawdown")
    return fig


def monthly_heatmap_fig(
    monthly_pnl: dict[str, float],
    *,
    title: str = "Monthly PnL",
) -> go.Figure | None:
    """Return None if not enough data to build a heatmap."""
    if not monthly_pnl:
        return None
    rows: list[tuple[int, int, float]] = []
    for ym, v in monthly_pnl.items():
        try:
            y, m = ym.split("-")
            rows.append((int(y), int(m), float(v)))
        except (ValueError, TypeError):
            continue
    if not rows:
        return None
    df = pd.DataFrame(rows, columns=["year", "month", "pnl"])
    pivot = df.pivot_table(index="year", columns="month", values="pnl", aggfunc="sum").sort_index()
    pivot = pivot.reindex(columns=range(1, 13))
    fig = go.Figure(
        data=go.Heatmap(
            z=pivot.values,
            x=[f"{m:02d}" for m in pivot.columns],
            y=[str(y) for y in pivot.index],
            colorscale="RdYlGn",
            zmid=0,
            colorbar={"title": "PnL"},
        )
    )
    fig.update_layout(title=title, xaxis_title="month", yaxis_title="year")
    return fig


def trade_pnl_hist_fig(
    trade_log: pd.DataFrame,
    *,
    title: str = "Trade PnL Histogram",
) -> go.Figure | None:
    if trade_log.empty:
        return None
    col = "net_pnl" if "net_pnl" in trade_log.columns else "gross_pnl"
    if col not in trade_log.columns:
        return None
    fig = go.Figure()
    fig.add_trace(go.Histogram(x=trade_log[col].astype(float).tolist(), nbinsx=40))
    fig.update_layout(title=title, xaxis_title=col, yaxis_title="count")
    return fig


def exposure_fig(
    trade_log: pd.DataFrame,
    n_bars: int,
    *,
    title: str = "Exposure (bars in market)",
) -> go.Figure | None:
    """简化敞口曲线：每根 bar 是否处于持仓状态（0/1）。多腿叠加按 lots 计。"""
    if trade_log.empty or n_bars <= 0 or not {"entry_i", "exit_i"}.issubset(trade_log.columns):
        return None
    expo = np.zeros(n_bars, dtype=float)
    for _, row in trade_log.iterrows():
        i0 = int(max(row["entry_i"], 0))
        i1 = int(min(row["exit_i"], n_bars))
        lots = float(row.get("lots", 1.0))
        if i1 > i0:
            expo[i0:i1] += lots
    fig = go.Figure()
    fig.add_trace(go.Scatter(x=list(range(n_bars)), y=expo.tolist(), mode="lines", name="lots in market"))
    fig.update_layout(title=title, xaxis_title="bar", yaxis_title="open lots")
    return fig


__all__ = [
    "equity_curve_fig",
    "drawdown_fig",
    "monthly_heatmap_fig",
    "trade_pnl_hist_fig",
    "exposure_fig",
]
