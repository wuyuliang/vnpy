"""Execution quality feedback scaler (W10)."""
from __future__ import annotations

from math import floor

from cta.risk.base import PositionScaler, SignalContext, normalize_symbol
from cta.risk.sizing.config import ExecutionQualityConfig
from cta.risk.state.execution_quality_tracker import ExecutionQualityTracker


class ExecutionQualityScaler(PositionScaler):
    """Scale down lots when rolling execution quality deteriorates."""

    def __init__(
        self,
        cfg: ExecutionQualityConfig | None = None,
        *,
        tracker: ExecutionQualityTracker | None = None,
    ) -> None:
        self.cfg = cfg or ExecutionQualityConfig()
        self.tracker = tracker or ExecutionQualityTracker(self.cfg)

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]:
        lots = int(lots_so_far)
        if lots <= 0:
            return 0, "exec_quality:lots_already_zero"
        symbol = normalize_symbol(ctx.candidate.get("symbol"))
        if not symbol:
            return lots, "exec_quality:no_symbol"
        metrics = self.tracker.metrics(symbol, now=ctx.bar_dt)
        if metrics.sample_count < self.cfg.min_trades_for_assessment:
            return (
                lots,
                (
                    "exec_quality:sample_insufficient:"
                    f"{metrics.sample_count}<{self.cfg.min_trades_for_assessment}"
                ),
            )
        triggered: list[tuple[str, float]] = []
        if metrics.slippage_ratio > self.cfg.slippage_ratio_threshold:
            triggered.append(("slippage", float(self.cfg.mults[0])))
        if metrics.reject_rate > self.cfg.reject_rate_threshold:
            triggered.append(("reject", float(self.cfg.mults[1])))
        if metrics.avg_price_deviation_pct > self.cfg.avg_price_deviation_threshold:
            triggered.append(("deviation", float(self.cfg.mults[2])))
        if not triggered:
            return lots, "exec_quality:ok"
        mult = min(m for _, m in triggered)
        new_lots = max(0, floor(lots * mult))
        if new_lots == 0 and lots >= 1 and mult > 0:
            new_lots = 1
        trigger_names = ",".join(name for name, _ in triggered)
        return new_lots, f"exec_quality:{trigger_names}:mult={mult:.2f}"


__all__ = ["ExecutionQualityScaler"]
