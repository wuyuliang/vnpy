"""演示策略 / 工厂函数集合。

提供 ``cta/cli.py backtest --strategy module:factory`` 路径上能直接 import 的
工厂函数。这里的策略实现极简，仅用于 CLI smoke test 与文档示例（见 ``run.md`` §5.2）。
真实策略放在 ``cta/strategy/skill_*`` 与 ``cta/strategy/baseline_skill_suite.py``。
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


class DoubleMaStrategy:
    """经典双均线：金叉做多、死叉平仓。

    参数
    ----
    fast : 快线窗口
    slow : 慢线窗口
    lots : 单笔下单手数
    """

    def __init__(self, fast: int = 5, slow: int = 20, lots: int = 1) -> None:
        if fast >= slow:
            raise ValueError(f"fast={fast} 必须 < slow={slow}")
        self.fast = int(fast)
        self.slow = int(slow)
        self.lots = int(lots)
        self._closes: list[float] = []

    def _ma(self, n: int) -> float:
        if len(self._closes) < n:
            return float("nan")
        return float(np.mean(self._closes[-n:]))

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict[str, Any]]:
        self._closes.append(float(bar["close"]))
        if len(self._closes) < self.slow + 1:
            return []
        fast_now = self._ma(self.fast)
        slow_now = self._ma(self.slow)
        prev_closes = self._closes[:-1]
        fast_prev = float(np.mean(prev_closes[-self.fast:])) if len(prev_closes) >= self.fast else float("nan")
        slow_prev = float(np.mean(prev_closes[-self.slow:])) if len(prev_closes) >= self.slow else float("nan")
        if not np.isfinite(fast_prev) or not np.isfinite(slow_prev):
            return []

        golden = fast_prev <= slow_prev and fast_now > slow_now
        dead = fast_prev >= slow_prev and fast_now < slow_now

        if position == 0 and golden:
            return [{"side": "long", "lots": self.lots, "order_type": "market"}]
        if position > 0 and dead:
            return [{"side": "flat", "lots": abs(position), "order_type": "market"}]
        return []


def make_double_ma(fast: int = 5, slow: int = 20, lots: int = 1) -> DoubleMaStrategy:
    """工厂：``cta.cli backtest --strategy cta.strategy.demos:make_double_ma`` 入口。"""
    return DoubleMaStrategy(fast=fast, slow=slow, lots=lots)


__all__ = ["DoubleMaStrategy", "make_double_ma"]
