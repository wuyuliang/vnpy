"""聚合多品种回测结果,输出 summary.csv + report.md。"""
from __future__ import annotations

import logging
from pathlib import Path

import pandas as pd

from cta.strategy.brooks.backtest.engine import SingleRunResult

logger = logging.getLogger(__name__)


def write_summary(results: list[SingleRunResult], report_dir: Path) -> None:
    rows = []
    for r in results:
        s = dict(r.stats)
        s["vt_symbol"] = r.vt_symbol
        rows.append(s)
    df = pd.DataFrame(rows)
    if df.empty:
        logger.warning("write_summary: results empty")
        return
    cols_order = [
        "vt_symbol", "n_trades", "total_return", "total_pnl_net",
        "win_rate", "profit_factor", "avg_win", "avg_loss",
        "max_drawdown", "sharpe", "annual_return",
    ]
    cols = [c for c in cols_order if c in df.columns] + \
           [c for c in df.columns if c not in cols_order]
    df = df[cols]
    csv_path = report_dir / "summary.csv"
    df.to_csv(csv_path, index=False)
    md_path = report_dir / "report.md"
    _write_markdown(df, results, md_path)
    logger.info("wrote %s and %s", csv_path, md_path)


def _write_markdown(summary: pd.DataFrame, results: list[SingleRunResult],
                    md_path: Path) -> None:
    lines = ["# Brooks v3 回测报告", ""]
    lines.append(f"- 品种数: {len(summary)}")
    lines.append(f"- 有成交组合: "
                 f"{int((summary.get('n_trades', pd.Series()) > 0).sum())}")
    lines.append("")
    lines.append("## 汇总表")
    lines.append(summary.to_markdown(index=False, floatfmt=".4f"))
    lines.append("")
    lines.append("## 每品种路径")
    for r in results:
        lines.append(f"- **{r.vt_symbol}**:")
        lines.append(f"  - trades: `{r.trades_path}`")
        lines.append(f"  - daily_equity: `{r.daily_equity_path}`")
    md_path.write_text("\n".join(lines), encoding="utf-8")


__all__ = ["write_summary"]
