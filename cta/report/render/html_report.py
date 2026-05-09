"""综合 HTML 评估报告生成入口。

用法:
    >>> from cta.report.render.html_report import HtmlReportConfig, write_html_report
    >>> path = write_html_report(
    ...     trade_log=trade_log, equity=equity,
    ...     bars=bars, dates=bars["datetime"],
    ...     cfg=HtmlReportConfig(out_dir="cta/report/backtest/20260509_demo",
    ...                          title="DemoStrategy / RB0 / day"),
    ... )
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Sequence

import numpy as np
import pandas as pd
import plotly.graph_objects as go
import plotly.io as pio

from cta.report.render.capacity import capacity_curve
from cta.report.render.metrics import extended_metrics
from cta.report.render.monte_carlo import block_bootstrap
from cta.report.render.plots import (
    drawdown_fig,
    equity_curve_fig,
    exposure_fig,
    monthly_heatmap_fig,
    trade_pnl_hist_fig,
)


@dataclass
class HtmlReportConfig:
    out_dir: str
    title: str = "Backtest Report"
    periods_per_year: int = 252
    monte_carlo_iter: int = 1000
    monte_carlo_block: int = 5
    monte_carlo_seed: int | None = 42
    capacity_levels: Sequence[float] = field(
        default_factory=lambda: (1e5, 5e5, 1e6, 5e6, 1e7)
    )
    capacity_multiplier: float = 1.0
    capacity_risk_per_trade: float = 0.01
    capacity_liquidity_ratio: float = 0.1


def _fmt_metrics(m: dict) -> str:
    rows = []
    for k, v in m.items():
        if k == "monthly_pnl":
            continue
        if isinstance(v, float):
            rows.append(f"<tr><td>{k}</td><td>{v:.6f}</td></tr>")
        elif v is None:
            rows.append(f"<tr><td>{k}</td><td>-</td></tr>")
        else:
            rows.append(f"<tr><td>{k}</td><td>{v}</td></tr>")
    return (
        "<table class='metrics'>"
        "<thead><tr><th>metric</th><th>value</th></tr></thead>"
        "<tbody>" + "".join(rows) + "</tbody></table>"
    )


def _fig_html(fig: go.Figure | None, div_id: str) -> str:
    if fig is None:
        return f"<p class='note'>(skipped: {div_id})</p>"
    return pio.to_html(fig, include_plotlyjs="cdn", full_html=False, div_id=div_id)


def _capacity_section(
    trade_log: pd.DataFrame, bars: pd.DataFrame, cfg: HtmlReportConfig
) -> str:
    if trade_log.empty or "volume" not in bars.columns:
        return "<p class='note'>(capacity skipped: no trades or no volume column)</p>"
    try:
        cap = capacity_curve(
            trade_log,
            bars,
            capital_levels=list(cfg.capacity_levels),
            multiplier=cfg.capacity_multiplier,
            risk_per_trade=cfg.capacity_risk_per_trade,
            liquidity_ratio=cfg.capacity_liquidity_ratio,
        )
    except (KeyError, ValueError) as e:
        return f"<p class='note'>(capacity error: {e})</p>"
    if cap.empty:
        return "<p class='note'>(capacity empty)</p>"

    fig = go.Figure()
    fig.add_trace(
        go.Scatter(
            x=cap["capital"].tolist(),
            y=cap["total_pnl"].tolist(),
            mode="lines+markers",
            name="total_pnl",
        )
    )
    fig.add_trace(
        go.Scatter(
            x=cap["capital"].tolist(),
            y=cap["fill_ratio"].tolist(),
            mode="lines+markers",
            name="fill_ratio",
            yaxis="y2",
        )
    )
    fig.update_layout(
        title="Capacity curve",
        xaxis={"title": "capital", "type": "log"},
        yaxis={"title": "total_pnl"},
        yaxis2={"title": "fill_ratio", "overlaying": "y", "side": "right", "range": [0, 1.05]},
    )
    table_html = cap.to_html(index=False, float_format=lambda x: f"{x:.4f}", classes="capacity")
    return _fig_html(fig, "capacity-curve") + table_html


def _monte_carlo_section(equity: pd.Series, cfg: HtmlReportConfig) -> str:
    rets = equity.astype(float).diff().fillna(0.0).to_numpy()
    if rets.size == 0:
        return "<p class='note'>(monte carlo skipped: no returns)</p>"
    res = block_bootstrap(
        rets,
        n_iter=cfg.monte_carlo_iter,
        block_size=cfg.monte_carlo_block,
        seed=cfg.monte_carlo_seed,
    )
    if res.n_iter == 0:
        return "<p class='note'>(monte carlo skipped: empty)</p>"

    fe_fig = go.Figure(data=go.Histogram(x=res.samples_final_equity.tolist(), nbinsx=40))
    fe_fig.update_layout(title="Bootstrapped final equity", xaxis_title="final equity")
    dd_fig = go.Figure(data=go.Histogram(x=res.samples_max_dd.tolist(), nbinsx=40))
    dd_fig.update_layout(title="Bootstrapped max drawdown", xaxis_title="max drawdown")

    summary = pd.DataFrame(
        {
            "metric": ["final_equity"] * len(res.final_equity) + ["max_drawdown"] * len(res.max_drawdown),
            "quantile": list(res.final_equity.keys()) + list(res.max_drawdown.keys()),
            "value": list(res.final_equity.values()) + list(res.max_drawdown.values()),
        }
    )
    return (
        _fig_html(fe_fig, "mc-final-equity")
        + _fig_html(dd_fig, "mc-max-dd")
        + summary.to_html(index=False, float_format=lambda x: f"{x:.4f}", classes="mc-summary")
    )


def write_html_report(
    *,
    trade_log: pd.DataFrame,
    equity: pd.Series,
    bars: pd.DataFrame,
    dates: Sequence[pd.Timestamp] | None = None,
    cfg: HtmlReportConfig,
) -> str:
    """Render comprehensive HTML report and return its path."""
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_dir / f"report_{ts}.html"

    if dates is None and "datetime" in bars.columns:
        dates = pd.to_datetime(bars["datetime"]).tolist()

    metrics = extended_metrics(
        trade_log,
        equity,
        periods_per_year=cfg.periods_per_year,
        dates=dates,
    )

    eq_html = _fig_html(equity_curve_fig(equity, dates=dates), "equity-curve")
    dd_html = _fig_html(drawdown_fig(equity, dates=dates), "drawdown")
    heat_html = _fig_html(monthly_heatmap_fig(metrics.get("monthly_pnl", {})), "monthly-heatmap")
    hist_html = _fig_html(trade_pnl_hist_fig(trade_log), "trade-hist")
    expo_html = _fig_html(exposure_fig(trade_log, n_bars=len(equity)), "exposure")

    metrics_html = _fmt_metrics(metrics)
    cap_html = _capacity_section(trade_log, bars, cfg)
    mc_html = _monte_carlo_section(equity, cfg)

    body = f"""
<!DOCTYPE html>
<html lang="zh-CN">
<head>
<meta charset="utf-8" />
<title>{cfg.title}</title>
<style>
body {{ font-family: -apple-system, "Helvetica Neue", Arial, sans-serif; margin: 24px; }}
h1 {{ font-size: 20px; }}
h2 {{ font-size: 16px; margin-top: 28px; border-bottom: 1px solid #ccc; padding-bottom: 4px; }}
table.metrics, table.capacity, table.mc-summary {{
    border-collapse: collapse; margin: 8px 0;
}}
table.metrics td, table.metrics th,
table.capacity td, table.capacity th,
table.mc-summary td, table.mc-summary th {{
    border: 1px solid #ccc; padding: 4px 10px;
}}
.note {{ color: #888; }}
</style>
</head>
<body>
<h1>{cfg.title}</h1>
<p class='note'>generated at {datetime.now().isoformat(timespec='seconds')}</p>

<h2>Metrics</h2>
{metrics_html}

<h2>Equity curve</h2>
{eq_html}

<h2>Drawdown</h2>
{dd_html}

<h2>Monthly PnL heatmap</h2>
{heat_html}

<h2>Trade PnL distribution</h2>
{hist_html}

<h2>Exposure</h2>
{expo_html}

<h2>Capacity</h2>
{cap_html}

<h2>Monte Carlo (block bootstrap)</h2>
{mc_html}
</body>
</html>
"""
    out_path.write_text(body, encoding="utf-8")

    metrics_to_dump = {k: v for k, v in metrics.items() if k != "monthly_pnl"}
    metrics_to_dump["monthly_pnl"] = metrics.get("monthly_pnl", {})
    metrics_path = out_dir / f"metrics_{ts}.json"
    metrics_path.write_text(
        json.dumps(metrics_to_dump, ensure_ascii=False, indent=2, default=_json_default),
        encoding="utf-8",
    )
    return str(out_path)


def _json_default(o):
    if isinstance(o, (np.integer,)):
        return int(o)
    if isinstance(o, (np.floating,)):
        return float(o)
    if isinstance(o, (np.ndarray,)):
        return o.tolist()
    raise TypeError(f"unsupported type: {type(o)}")


__all__ = ["HtmlReportConfig", "write_html_report"]
