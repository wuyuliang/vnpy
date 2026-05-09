"""每日交易日终对账报告。

输入实盘 / 仿真的 ``trade_log`` 与 ``equity``，可选地附带同期回测 ``trade_log``
做 parity 对账，输出 markdown 报告并返回 ``DailyReport`` 摘要。

典型用法
--------
    write_daily_report(
        live_trade_log=live_tl,
        live_equity=live_eq,
        backtest_trade_log=bt_tl,                 # 可选
        dates=bars["datetime"],
        out_dir="cta/report/live/2024-01-09",
        title="rb888 / 2024-01-09",
    )

该函数适合放进 cron / vnpy event loop 收盘后调用。
"""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Sequence

import pandas as pd

from cta.report.render.metrics import extended_metrics
from cta.sim.parity_check import compare_signals, trade_log_to_signals


@dataclass
class DailyReport:
    title: str
    trades_count: int
    pnl: float
    metrics: dict
    parity_mismatch_rate: float | None
    report_path: str


def _format_metrics(metrics: dict) -> str:
    rows: list[str] = []
    for k, v in metrics.items():
        if k == "monthly_pnl":
            continue
        if isinstance(v, float):
            rows.append(f"| {k} | {v:.6f} |")
        elif v is None:
            rows.append(f"| {k} | - |")
        else:
            rows.append(f"| {k} | {v} |")
    return "\n".join(rows)


def write_daily_report(
    *,
    live_trade_log: pd.DataFrame,
    live_equity: pd.Series,
    dates: Sequence[pd.Timestamp],
    out_dir: str,
    title: str = "Daily report",
    backtest_trade_log: pd.DataFrame | None = None,
    parity_tolerance: pd.Timedelta = pd.Timedelta("1min"),
    periods_per_year: int = 252,
) -> DailyReport:
    """生成 markdown 每日报告并返回摘要。"""
    out_path_dir = Path(out_dir)
    out_path_dir.mkdir(parents=True, exist_ok=True)
    ts = datetime.now().strftime("%Y%m%d_%H%M%S")
    out_path = out_path_dir / f"daily_{ts}.md"

    metrics = extended_metrics(
        live_trade_log,
        live_equity,
        periods_per_year=periods_per_year,
        dates=list(dates),
    )

    parity_rate: float | None = None
    parity_md = ""
    if backtest_trade_log is not None:
        live_sigs = trade_log_to_signals(live_trade_log, list(dates))
        bt_sigs = trade_log_to_signals(backtest_trade_log, list(dates))
        res = compare_signals(live_sigs, bt_sigs, time_tolerance=parity_tolerance)
        parity_rate = float(res.mismatch_rate)
        parity_md = (
            "\n## Parity (live vs backtest)\n"
            f"- matched: {res.matched}\n"
            f"- mismatched: {res.mismatched}\n"
            f"- only_in_live: {res.only_in_a}\n"
            f"- only_in_bt: {res.only_in_b}\n"
            f"- mismatch_rate: {res.mismatch_rate:.4f}\n"
        )
        if not res.details.empty:
            parity_md += "\n<details><summary>diff details (top 50)</summary>\n\n"
            parity_md += res.details.head(50).to_markdown(index=False)
            parity_md += "\n\n</details>\n"

    pnl = float(metrics.get("total_pnl", 0.0))
    body = f"""# {title}

generated at {datetime.now().isoformat(timespec='seconds')}

## Summary

- trades_count: {len(live_trade_log)}
- pnl: {pnl:.4f}

## Metrics

| metric | value |
| --- | --- |
{_format_metrics(metrics)}
{parity_md}
"""
    out_path.write_text(body, encoding="utf-8")

    return DailyReport(
        title=title,
        trades_count=int(len(live_trade_log)),
        pnl=pnl,
        metrics=metrics,
        parity_mismatch_rate=parity_rate,
        report_path=str(out_path),
    )


__all__ = ["DailyReport", "write_daily_report"]
