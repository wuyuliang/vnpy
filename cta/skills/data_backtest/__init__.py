"""§08 data_and_backtest_infra implementations."""
from __future__ import annotations

from .continuous_contract import ContinuousSeries, build_continuous, detect_rollover
from .event_driven_backtest import EngineConfig, run_backtest, simulate_fill
from .rollover_rules import RolloverAction, decide_rollover, execute_rollover
from .trade_evaluation import ReportConfig, summarize_trades, write_report
from .transaction_cost import CostComponents, apply_cost_to_pnl, estimate_cost

__all__ = [
    "ContinuousSeries",
    "CostComponents",
    "EngineConfig",
    "ReportConfig",
    "RolloverAction",
    "apply_cost_to_pnl",
    "build_continuous",
    "decide_rollover",
    "detect_rollover",
    "estimate_cost",
    "execute_rollover",
    "run_backtest",
    "simulate_fill",
    "summarize_trades",
    "write_report",
]

