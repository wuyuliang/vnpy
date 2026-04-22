"""组合层风控:按实时回撤分级降仓。

`PortfolioRiskManager.update(equity)` 每根 bar 喂入当前 equity(已平仓 PnL + 浮动)
跟踪 peak_equity + current_dd,返回 leverage_mult ∈ {1.0, 0.5, 0.25}:

- current_dd <= -dd_threshold_quarter(-5%): leverage = 0.25
- current_dd <= -dd_threshold_half(-2%):    leverage = 0.5
- 否则:                                     leverage = 1.0

`recover_to_full_at_new_high=True` 时,equity 创新高立即恢复 1.0x;否则只有
`current_dd` 回到阈值以上才回升一档。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


@dataclass
class LeverageChange:
    ts: str                 # 触发时刻(caller 提供,便于日志)
    prev: float
    new: float
    current_dd: float
    equity: float


@dataclass
class PortfolioRiskManager:
    dd_threshold_half: float = 0.02
    dd_threshold_quarter: float = 0.05
    recover_to_full_at_new_high: bool = True

    peak_equity: float = 0.0
    current_leverage: float = 1.0
    history: list[LeverageChange] = field(default_factory=list)

    def update(self, equity: float, ts: str = "") -> float:
        """喂入当前 equity,返回当前 leverage_mult。"""
        if equity <= 0:
            return self.current_leverage

        if equity > self.peak_equity:
            self.peak_equity = equity
            if self.recover_to_full_at_new_high and self.current_leverage < 1.0:
                self._set_leverage(1.0, ts=ts, equity=equity, dd=0.0,
                                   reason="new_peak")

        dd = (equity - self.peak_equity) / self.peak_equity if self.peak_equity > 0 else 0.0
        target = self._target_leverage(dd)
        if target < self.current_leverage:
            # 降仓是立即生效的
            self._set_leverage(target, ts=ts, equity=equity, dd=dd,
                               reason=f"dd={dd:.2%}")
        elif not self.recover_to_full_at_new_high and target > self.current_leverage:
            # 回升一档(仅当不依赖新高恢复时)
            self._set_leverage(target, ts=ts, equity=equity, dd=dd,
                               reason=f"dd={dd:.2%}_recover")

        return self.current_leverage

    def _target_leverage(self, dd: float) -> float:
        if dd <= -self.dd_threshold_quarter:
            return 0.25
        if dd <= -self.dd_threshold_half:
            return 0.5
        return 1.0

    def _set_leverage(self, new: float, ts: str, equity: float, dd: float,
                      reason: str) -> None:
        if new == self.current_leverage:
            return
        self.history.append(LeverageChange(
            ts=ts, prev=self.current_leverage, new=new,
            current_dd=dd, equity=equity,
        ))
        logger.info(
            "portfolio leverage %.2f → %.2f @ %s equity=%.2f dd=%.2%% reason=%s",
            self.current_leverage, new, ts or "?", equity, dd * 100, reason,
        )
        self.current_leverage = new


__all__ = ["PortfolioRiskManager", "LeverageChange"]
