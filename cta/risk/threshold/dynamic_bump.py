"""DD-driven dynamic threshold bump（子系统③ 的阈值面）。

规则（参数化）：
    dd in [0, trigger)                   → +0pp
    dd in [trigger, trigger+step)        → +pp_per_step
    dd in [trigger+step, trigger+2*step) → +2*pp_per_step
    ...
    封顶 cap_pp（累计抬高上限）；
    输出再封顶 threshold_cap_pp（pctl 100pp 不能超）。

例：trigger=1%, step=1%, pp_per_step=5, cap_pp=25, threshold_cap_pp=95
    dd=0.5% → bump=0 → base
    dd=1.5% → bump=5 → base+5
    dd=2.5% → bump=10
    dd=10%+ → bump=25 (cap)
    base=80 dd=5% → 80+20=100 → cap 95 → 95

dd 输入：来自 DdSignalProvider.effective_dd_pct()（max of cumulative + weekly），
但本 adjuster 不直接持有 provider，由 caller 在 ctx.portfolio["effective_dd_pct"] 注入。
"""
from __future__ import annotations

import logging

from cta.risk.base import SignalContext

logger = logging.getLogger(__name__)


class DynamicBumpAdjuster:
    """按 ctx.portfolio["effective_dd_pct"] 抬高阈值。"""

    def __init__(
        self,
        *,
        trigger_pct: float = 0.01,
        step_pct: float = 0.01,
        pp_per_step: float = 5.0,
        cap_pp: float = 25.0,
        threshold_cap_pp: float = 95.0,
    ) -> None:
        if step_pct <= 0:
            raise ValueError(f"step_pct must be > 0, got {step_pct}")
        self.trigger_pct = float(trigger_pct)
        self.step_pct = float(step_pct)
        self.pp_per_step = float(pp_per_step)
        self.cap_pp = float(cap_pp)
        self.threshold_cap_pp = float(threshold_cap_pp)

    def resolve(self, ctx: SignalContext, base_threshold: float) -> float:
        dd = self._dd_from_ctx(ctx)
        bump = self.compute_bump(dd)
        new_thr = float(base_threshold) + bump
        # cap
        if new_thr > self.threshold_cap_pp:
            new_thr = float(self.threshold_cap_pp)
        return new_thr

    # ── helpers ─────────────────────────────────────────────────────

    def compute_bump(self, dd: float) -> float:
        """单独可调：给一个 dd 算 bump pp（用于 OOT 批处理向量化）。"""
        try:
            v = float(dd)
        except (TypeError, ValueError):
            return 0.0
        if v <= self.trigger_pct:
            return 0.0
        excess = v - self.trigger_pct
        steps = excess / self.step_pct
        bump = steps * self.pp_per_step
        return min(bump, self.cap_pp)

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


__all__ = ["DynamicBumpAdjuster"]
