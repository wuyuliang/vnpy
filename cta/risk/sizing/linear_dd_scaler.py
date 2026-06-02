"""Linear drawdown lots scaler（子系统③ 的 sizing 面）。

规则：
    dd <= trigger_pct                     → mult = 1.0（无影响）
    dd in [trigger, trigger + step)       → mult = 1 - 1*step_mult = 0.9
    dd in [trigger + step, trigger + 2*step) → mult = 0.8
    ...
    封底 floor_mult（默认 0.10）

dd 输入：来自 ``ctx.portfolio["effective_dd_pct"]``（DdSignalProvider 注入）。
"""
from __future__ import annotations

import logging
from math import floor

from cta.risk.base import PositionScaler, SignalContext

logger = logging.getLogger(__name__)


class LinearDdScaler(PositionScaler):
    """按 dd 线性缩 lots。"""

    def __init__(
        self,
        *,
        trigger_pct: float = 0.01,
        step_pct: float = 0.01,
        step_mult: float = 0.10,
        floor_mult: float = 0.10,
        hysteresis_pct: float = 0.0,
    ) -> None:
        if step_pct <= 0:
            raise ValueError(f"step_pct must be > 0, got {step_pct}")
        if not 0.0 <= step_mult <= 1.0:
            raise ValueError(f"step_mult must be in [0,1], got {step_mult}")
        if not 0.0 < floor_mult <= 1.0:
            raise ValueError(f"floor_mult must be in (0,1], got {floor_mult}")
        if hysteresis_pct < 0.0:
            raise ValueError(f"hysteresis_pct must be >= 0, got {hysteresis_pct}")
        self.trigger_pct = float(trigger_pct)
        self.step_pct = float(step_pct)
        self.step_mult = float(step_mult)
        self.floor_mult = float(floor_mult)
        self.hysteresis_pct = float(hysteresis_pct)
        # 滞后态：触发缩仓后，dd 需回落到 release 线以下才解除。
        self._locked_mult: float | None = None

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]:
        lots = int(lots_so_far)
        if lots <= 0:
            return 0, "linear_dd:lots_already_zero"
        dd = self._dd_from_ctx(ctx)
        mult = self.compute_mult(dd)
        new_lots = max(0, floor(lots * mult))
        if new_lots == 0 and lots >= 1 and mult > 0:
            new_lots = 1
        return new_lots, f"linear_dd:dd={dd:.4f}:mult={mult:.2f}"

    def compute_mult(self, dd: float) -> float:
        """单独可调：给一个 dd 算 mult。"""
        try:
            v = float(dd)
        except (TypeError, ValueError):
            return 1.0
        if self.hysteresis_pct <= 0.0:
            return self._compute_base_mult(v)
        release_level = max(0.0, self.trigger_pct - self.hysteresis_pct)
        if self._locked_mult is not None:
            # 回撤继续恶化时允许继续收紧；回撤修复不足 release 时保持原档。
            if v > self.trigger_pct:
                self._locked_mult = min(self._locked_mult, self._compute_base_mult(v))
                return self._locked_mult
            if v < release_level:
                self._locked_mult = None
                return self._compute_base_mult(v)
            return self._locked_mult
        current = self._compute_base_mult(v)
        if v > self.trigger_pct and current < 1.0:
            self._locked_mult = current
        return current

    def _compute_base_mult(self, v: float) -> float:
        """基于当前 dd 的连续线性缩仓倍率（无滞后状态）。"""
        if v <= self.trigger_pct:
            return 1.0
        excess = v - self.trigger_pct
        steps = excess / self.step_pct
        reduction = steps * self.step_mult
        mult = 1.0 - reduction
        return max(self.floor_mult, mult)

    @staticmethod
    def _dd_from_ctx(ctx: SignalContext) -> float:
        portfolio = ctx.portfolio or {}
        for k in ("effective_dd_pct", "cumulative_dd_pct", "drawdown_pct"):
            if k in portfolio:
                try:
                    return float(portfolio[k])
                except (TypeError, ValueError):
                    continue
        return 0.0


__all__ = ["LinearDdScaler"]
