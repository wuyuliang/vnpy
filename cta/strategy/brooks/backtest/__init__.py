"""v3 回测层: vnpy BacktestingEngine 的批量入口。"""

from cta.strategy.brooks.backtest.runner import run_batch, run_single

__all__ = ["run_single", "run_batch"]
