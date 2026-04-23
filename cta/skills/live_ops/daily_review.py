"""§10-04 daily review report."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

import pandas as pd


@dataclass
class ReviewConfig:
    out_dir: str = "cta/report/daily"
    bt_pnl_loader: Callable | None = None
    include_charts: bool = True


def compare_live_vs_backtest(
    live_trades: pd.DataFrame,
    bt_trades: pd.DataFrame,
) -> pd.DataFrame:
    """Return per-trade pnl difference table."""
    if "trade_id" not in live_trades.columns or "trade_id" not in bt_trades.columns:
        live = live_trades.reset_index().rename(columns={"index": "trade_id", "net_pnl": "live_net_pnl"})
        bt = bt_trades.reset_index().rename(columns={"index": "trade_id", "net_pnl": "bt_net_pnl"})
    else:
        live = live_trades.rename(columns={"net_pnl": "live_net_pnl"})
        bt = bt_trades.rename(columns={"net_pnl": "bt_net_pnl"})
    merged = live[["trade_id", "live_net_pnl"]].merge(bt[["trade_id", "bt_net_pnl"]], on="trade_id", how="outer").fillna(0.0)
    merged["diff"] = merged["live_net_pnl"] - merged["bt_net_pnl"]
    return merged


def build_daily_review(
    date: pd.Timestamp,
    trade_log_today: pd.DataFrame,
    equity_today: pd.Series,
    alerts_today: pd.DataFrame,
    cfg: ReviewConfig,
) -> str:
    """Render markdown daily review and return path."""
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    d = pd.Timestamp(date)
    path = out_dir / f"{d.strftime('%Y%m%d')}_daily_review.md"

    daily_pnl = float(equity_today.iloc[-1] - equity_today.iloc[0]) if len(equity_today) >= 2 else 0.0
    alert_count = int(len(alerts_today))

    bt_compare_block = "N/A"
    if callable(cfg.bt_pnl_loader):
        bt_trades = cfg.bt_pnl_loader(d)
        diff = compare_live_vs_backtest(trade_log_today, bt_trades)
        bt_compare_block = diff.head(20).to_markdown(index=False)

    lines = [
        f"# Daily Review {d.date()}",
        "",
        "## Summary",
        f"- daily_pnl: {daily_pnl:.6f}",
        f"- trade_count: {len(trade_log_today)}",
        f"- alert_count: {alert_count}",
        "",
        "## Live vs Backtest",
        bt_compare_block,
        "",
        "## Alerts",
        alerts_today.to_markdown(index=False) if len(alerts_today) else "- no alerts",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)

