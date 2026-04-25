"""§08-05 trade log summary and report writer."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd


@dataclass
class ReportConfig:
    out_dir: str
    include_plots: bool = True
    per_symbol: bool = True
    per_year: bool = True
    periods_per_year: int = 252


def _max_drawdown(equity: pd.Series) -> float:
    eq = equity.astype(float)
    peak = eq.cummax().replace(0, np.nan)
    dd = ((peak - eq) / peak).fillna(0.0)
    return float(dd.max()) if len(dd) else 0.0


def summarize_trades(
    trade_log: pd.DataFrame,
    equity: pd.Series,
    periods_per_year: int = 252,
) -> dict:
    """Return a compact metrics dictionary."""
    if periods_per_year <= 0:
        raise ValueError(f"periods_per_year 应 > 0，got {periods_per_year}")
    tl = trade_log.copy()
    if "net_pnl" not in tl.columns:
        tl["net_pnl"] = tl.get("gross_pnl", pd.Series([0.0] * len(tl))).astype(float)
    pnl = tl["net_pnl"].astype(float)
    total_pnl = float(pnl.sum())
    winrate = float((pnl > 0).mean()) if len(pnl) else 0.0
    gross_pos = float(pnl[pnl > 0].sum())
    gross_neg = float(-pnl[pnl < 0].sum())
    pf = gross_pos / max(gross_neg, 1e-9)

    eq = equity.astype(float)
    rets = eq.diff().fillna(0.0)
    ret_std = float(rets.std(ddof=0))
    sharpe = (
        0.0
        if ret_std == 0
        else float(rets.mean() / ret_std * np.sqrt(float(periods_per_year)))
    )
    mdd = _max_drawdown(eq)
    annualized = 0.0
    if len(eq) > 1 and eq.iloc[0] != 0:
        n_periods = max(len(eq) - 1, 1)
        years = max(n_periods / float(periods_per_year), 1.0 / float(periods_per_year))
        annualized = float((eq.iloc[-1] / eq.iloc[0]) ** (1.0 / years) - 1.0)
    calmar = annualized / max(mdd, 1e-9) if mdd > 0 else 0.0

    return {
        "total_pnl": total_pnl,
        "annualized": annualized,
        "sharpe": sharpe,
        "mdd": mdd,
        "calmar": calmar,
        "winrate": winrate,
        "pf": pf,
        "trade_count": int(len(tl)),
    }


def write_report(
    trade_log: pd.DataFrame,
    equity: pd.Series,
    cfg: ReportConfig,
) -> str:
    """Write markdown report and return path."""
    out_dir = Path(cfg.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ts = pd.Timestamp.now(tz="UTC").strftime("%Y%m%d_%H%M%S")
    path = out_dir / f"summary_{ts}.md"
    s = summarize_trades(
        trade_log,
        equity,
        periods_per_year=int(cfg.periods_per_year),
    )

    lines = [
        "# Backtest Summary",
        "",
        f"- total_pnl: {s['total_pnl']:.6f}",
        f"- annualized: {s['annualized']:.6f}",
        f"- sharpe: {s['sharpe']:.6f}",
        f"- mdd: {s['mdd']:.6f}",
        f"- calmar: {s['calmar']:.6f}",
        f"- winrate: {s['winrate']:.6f}",
        f"- pf: {s['pf']:.6f}",
        f"- trade_count: {s['trade_count']}",
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return str(path)
