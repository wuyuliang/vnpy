"""多 symbol × 多 interval 批量回测与汇总。

基于 ``cta.run.cta_backtester.run_via_event_driven``：对每个 ``(symbol, interval)``
组合各跑一遍回测，输出：

- ``summary.csv``      每个组合的 sharpe/sortino/calmar/mdd/total_pnl/trades_count/error
- ``monthly_pnl.csv``  long-form 月度 PnL（列：symbol, interval, ym, pnl）
- ``per_combo/<sym>_<itv>/report_*.html``  各组合的综合 HTML 报告
- ``MultiRunResult``    程序内可消费的 dataclass（含 portfolio 等权聚合）

CLI 见 ``run.md`` §6.4。
"""
from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable

import numpy as np
import pandas as pd

from cta.run.cta_backtester import run_via_event_driven

logger = logging.getLogger(__name__)


@dataclass
class MultiRunSpec:
    strategy_class: type
    combos: list[tuple[str, str]]
    get_bars: Callable[[str, str], pd.DataFrame]
    get_setting: Callable[[str, str], dict] | None = None
    out_dir: str = "."
    title_fmt: str = "{symbol} / {interval}"
    monte_carlo_iter: int = 200       # 多组合时降一些 默认 1000 太慢
    capital_base: float = 1_000_000.0


@dataclass
class MultiRunResult:
    summary: pd.DataFrame
    monthly_pnl: pd.DataFrame
    portfolio_equity: pd.Series
    portfolio_metrics: dict[str, Any]
    out_paths: list[str]


def _safe_dir(s: str) -> str:
    return re.sub(r"[^A-Za-z0-9._-]", "_", s)


def _vt_symbol(symbol: str) -> str:
    """缺省给个 SHFE 后缀；调用方真实场景应在 get_setting 里直接覆盖。"""
    return symbol if "." in symbol else f"{symbol}.SHFE"


def aggregate_portfolio(equities: list[pd.Series]) -> pd.Series:
    """等权聚合多个净值曲线。

    对齐策略：截到最短长度，避免不同 interval 时间轴不对齐导致虚假外推。
    """
    if not equities:
        return pd.Series(dtype=float, name="equity")
    min_len = min(len(s) for s in equities)
    if min_len == 0:
        return pd.Series(dtype=float, name="equity")
    arr = np.stack([s.iloc[:min_len].astype(float).to_numpy() for s in equities])
    return pd.Series(arr.mean(axis=0), name="equity")


def _build_monthly_long(
    rows: list[tuple[str, str, dict]],
) -> pd.DataFrame:
    """rows = [(symbol, interval, monthly_pnl_dict), ...]"""
    out: list[dict] = []
    for sym, itv, monthly in rows:
        for ym, pnl in (monthly or {}).items():
            out.append({"symbol": sym, "interval": itv, "ym": ym, "pnl": float(pnl)})
    return pd.DataFrame(out, columns=["symbol", "interval", "ym", "pnl"])


def run_multi(spec: MultiRunSpec) -> MultiRunResult:
    """对 ``spec.combos`` 中每个 ``(symbol, interval)`` 跑回测并汇总。"""
    out_root = Path(spec.out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    summary_rows: list[dict] = []
    monthly_rows: list[tuple[str, str, dict]] = []
    equities: list[pd.Series] = []
    out_paths: list[str] = []

    for symbol, interval in spec.combos:
        combo_dir = out_root / "per_combo" / f"{_safe_dir(symbol)}_{_safe_dir(interval)}"
        title = spec.title_fmt.format(symbol=symbol, interval=interval)
        try:
            bars = spec.get_bars(symbol, interval)
            if bars is None or len(bars) < 2:
                raise ValueError("bars empty / too short")
            setting = (spec.get_setting(symbol, interval) if spec.get_setting else {}) or {}
            res = run_via_event_driven(
                strategy_class=spec.strategy_class,
                vt_symbol=_vt_symbol(symbol),
                setting=setting,
                bars=bars,
                out_dir=str(combo_dir),
                title=title,
                monte_carlo_iter=spec.monte_carlo_iter,
            )
            stats = dict(res.stats or {})
            row = {
                "symbol": symbol,
                "interval": interval,
                "sharpe": float(stats.get("sharpe", 0.0)),
                "sortino": float(stats.get("sortino", 0.0)),
                "calmar": float(stats.get("calmar", 0.0)),
                "mdd": float(stats.get("mdd", 0.0)),
                "winrate": float(stats.get("winrate", 0.0)),
                "pf": float(stats.get("pf", 0.0)),
                "total_pnl": float(stats.get("total_pnl", 0.0)),
                "annualized": float(stats.get("annualized", 0.0)),
                "trades_count": int(stats.get("trade_count", 0)),
                "report_path": res.report_path,
                "error": "",
            }
            summary_rows.append(row)
            monthly_rows.append((symbol, interval, stats.get("monthly_pnl", {}) or {}))
            equities.append(res.equity_curve.astype(float).reset_index(drop=True))
            out_paths.append(res.report_path)
        except Exception as e:  # noqa: BLE001
            logger.exception("run_multi combo failed: %s/%s", symbol, interval)
            summary_rows.append({
                "symbol": symbol, "interval": interval,
                "sharpe": 0.0, "sortino": 0.0, "calmar": 0.0, "mdd": 0.0,
                "winrate": 0.0, "pf": 0.0, "total_pnl": 0.0, "annualized": 0.0,
                "trades_count": 0, "report_path": "", "error": str(e),
            })

    summary = pd.DataFrame(summary_rows)
    monthly = _build_monthly_long(monthly_rows)

    summary_path = out_root / "summary.csv"
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    monthly_path = out_root / "monthly_pnl.csv"
    monthly.to_csv(monthly_path, index=False, encoding="utf-8-sig")

    # Sharpe pivot 和 monthly heatmap 透视表，方便人工查看
    if not summary.empty:
        sharpe_pivot = summary.pivot_table(index="symbol", columns="interval", values="sharpe")
        sharpe_pivot.to_csv(out_root / "sharpe_pivot.csv", encoding="utf-8-sig")
    if not monthly.empty:
        monthly_pivot = monthly.pivot_table(
            index="ym", columns=["symbol", "interval"], values="pnl", aggfunc="sum"
        ).sort_index()
        monthly_pivot.to_csv(out_root / "monthly_pnl_pivot.csv", encoding="utf-8-sig")

    portfolio_equity = aggregate_portfolio(equities)
    portfolio_metrics: dict[str, Any] = {}
    if len(portfolio_equity) > 1:
        from cta.report.render.metrics import extended_metrics
        # 用空 trade_log 计算（仅 equity 维度的指标可用）
        portfolio_metrics = extended_metrics(
            trade_log=pd.DataFrame(columns=["entry_i", "exit_i", "side", "lots",
                                            "entry_price", "exit_price",
                                            "gross_pnl", "cost", "net_pnl"]),
            equity=portfolio_equity,
        )

    return MultiRunResult(
        summary=summary,
        monthly_pnl=monthly,
        portfolio_equity=portfolio_equity,
        portfolio_metrics=portfolio_metrics,
        out_paths=out_paths,
    )


__all__ = ["MultiRunResult", "MultiRunSpec", "aggregate_portfolio", "run_multi"]
