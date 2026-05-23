"""Trailing take-profit evaluator."""
from __future__ import annotations

from dataclasses import dataclass

from cta.config.trailing_take_profit_config import TrailingTakeProfitConfig
from cta.portfolio_logic.position_trend_state import PositionTrendState


@dataclass(frozen=True)
class ExitDecision:
    """Exit decision payload from trailing take-profit evaluator."""

    reason: str
    price: float
    trigger_price: float
    trail_distance_pct: float
    highwater_price: float


class TrailingTakeProfitEvaluator:
    """Tiered trailing take-profit logic."""

    def __init__(self, cfg: TrailingTakeProfitConfig) -> None:
        self.cfg = cfg

    def _resolve_trail_pct(self, pnl_pct: float) -> float:
        trail = float(self.cfg.trail_distance_pct_initial)
        for threshold, tier_trail in sorted(self.cfg.tiers):
            if float(pnl_pct) >= float(threshold):
                trail = float(tier_trail)
        if float(pnl_pct) >= float(self.cfg.huge_gain_threshold):
            trail = min(trail, float(self.cfg.trail_distance_pct_after_huge_gain))
        return float(trail)

    def update(
        self,
        state: PositionTrendState,
        *,
        highwater_price: float,
        cluster: str,
        interval: str,
    ) -> ExitDecision | None:
        """Evaluate whether trailing take-profit should exit this bar."""
        if not bool(self.cfg.use_trailing_take_profit):
            return None
        if not self.cfg.is_enabled(cluster, interval):
            return None
        if not state.is_valid():
            return None
        side = str(state.side).strip().lower()
        if side == "short":
            max_pnl_pct = (float(state.entry_price) - float(highwater_price)) / float(state.entry_price)
        else:
            max_pnl_pct = (float(highwater_price) - float(state.entry_price)) / float(state.entry_price)
        if float(max_pnl_pct) < float(self.cfg.activation_pnl_pct):
            return None
        if bool(self.cfg.require_trend_confirmed) and float(state.trend_score) < 0.0:
            return None

        trail_pct = self._resolve_trail_pct(float(max_pnl_pct))
        if side == "short":
            trigger = float(highwater_price) * (1.0 + trail_pct)
            if float(state.current_price) >= trigger:
                return ExitDecision(
                    reason="trailing_take_profit",
                    price=float(state.current_price),
                    trigger_price=float(trigger),
                    trail_distance_pct=float(trail_pct),
                    highwater_price=float(highwater_price),
                )
            return None

        trigger = float(highwater_price) * (1.0 - trail_pct)
        if float(state.current_price) <= trigger:
            return ExitDecision(
                reason="trailing_take_profit",
                price=float(state.current_price),
                trigger_price=float(trigger),
                trail_distance_pct=float(trail_pct),
                highwater_price=float(highwater_price),
            )
        return None


__all__ = ["ExitDecision", "TrailingTakeProfitEvaluator"]
