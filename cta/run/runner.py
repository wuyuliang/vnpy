"""统一回测 runner：跑 event-driven 回测 + 写 HTML 报告。

设计目标
--------
统一入口让"策略 + 数据 + 引擎配置"一次性产出：
1. trade_log / equity_curve / stats 三件套
2. HTML 综合报告（含蒙特卡洛、容量、月度热力等）
3. metrics_*.json 副本

供两个上层调用：
- `cta/strategy/tests/test_*.py` 的回测入口（替代手写 ``run_backtest`` + ``write_report``）
- `cta/cli.py backtest` 子命令

不做的事
--------
- 不做 strategy 实例化（调用方完成 ``prepare_strategy_frame`` 等预处理）
- 不引入新的 config 文件格式（沿用 dataclass / 关键字参数）
- 不做多品种组合（属于 M2 vnpy_ctabacktester 范畴）
"""
from __future__ import annotations

import dataclasses
from dataclasses import dataclass
from typing import Any, Callable, Sequence

import pandas as pd

from cta.report.render.html_report import HtmlReportConfig, write_html_report
from cta.report.render.metrics import extended_metrics
from cta.skills.data_backtest.event_driven_backtest import EngineConfig, run_backtest


@dataclass
class RunnerResult:
    """One-shot backtest result bundle."""

    trade_log: pd.DataFrame
    equity_curve: pd.Series
    stats: dict[str, Any]
    report_path: str


def run_event_driven_backtest(
    strategy: Any,
    bars: pd.DataFrame,
    *,
    engine_cfg: EngineConfig | None = None,
    cost_fn: Callable | None = None,
    out_dir: str,
    title: str = "Backtest",
    periods_per_year: int = 252,
    monte_carlo_iter: int = 1000,
    capacity_levels: Sequence[float] = (1e5, 5e5, 1e6, 5e6, 1e7),
    capacity_multiplier: float = 1.0,
    capacity_risk_per_trade: float = 0.01,
    capacity_liquidity_ratio: float = 0.1,
) -> RunnerResult:
    """Run event-driven backtest and render HTML report.

    Parameters
    ----------
    strategy
        实现 ``on_bar(i, bar, position) -> list[order_dict]`` 的策略实例。
    bars
        含 ``open/high/low/close``（``volume`` 推荐）的行情 DataFrame。
        若有 ``datetime`` 列，会被用于月度统计与图表 X 轴。
    engine_cfg
        ``EngineConfig`` 实例；若同时传 ``cost_fn``，会覆盖 cfg.cost_fn。
    cost_fn
        手续费/滑点成本函数，签名兼容
        ``cta.skills.data_backtest.transaction_cost.estimate_cost``。
    out_dir
        报告输出目录（自动创建）。
    其它参数见 ``HtmlReportConfig``。
    """
    cfg = engine_cfg if engine_cfg is not None else EngineConfig()
    if cost_fn is not None:
        cfg = dataclasses.replace(cfg, cost_fn=cost_fn)

    out = run_backtest(bars, strategy, cfg)
    trade_log: pd.DataFrame = out["trade_log"]
    equity: pd.Series = out["equity_curve"]

    dates = pd.to_datetime(bars["datetime"]).tolist() if "datetime" in bars.columns else None
    stats = extended_metrics(trade_log, equity, periods_per_year=periods_per_year, dates=dates)

    report_cfg = HtmlReportConfig(
        out_dir=out_dir,
        title=title,
        periods_per_year=periods_per_year,
        monte_carlo_iter=monte_carlo_iter,
        capacity_levels=tuple(capacity_levels),
        capacity_multiplier=capacity_multiplier,
        capacity_risk_per_trade=capacity_risk_per_trade,
        capacity_liquidity_ratio=capacity_liquidity_ratio,
    )
    report_path = write_html_report(
        trade_log=trade_log,
        equity=equity,
        bars=bars,
        dates=dates,
        cfg=report_cfg,
    )

    return RunnerResult(
        trade_log=trade_log,
        equity_curve=equity,
        stats=stats,
        report_path=report_path,
    )


__all__ = ["RunnerResult", "run_event_driven_backtest"]
