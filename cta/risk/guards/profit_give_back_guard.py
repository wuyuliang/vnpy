"""Profit give-back guard (W9)."""
from __future__ import annotations

import pandas as pd

from cta.live.risk import RiskContext, RiskDecision, _BaseRule
from cta.risk.guards.config import ProfitGiveBackConfig
from cta.risk.state.intraday_profit_tracker import IntradayProfitTracker


class ProfitGiveBackGuard(_BaseRule):
    """After intraday give-back trigger, block new opens for the session."""

    name: str = "profit_give_back"

    def __init__(
        self,
        cfg: ProfitGiveBackConfig | None = None,
        *,
        tracker: IntradayProfitTracker | None = None,
    ) -> None:
        self.cfg = cfg or ProfitGiveBackConfig()
        self.tracker = tracker or IntradayProfitTracker(self.cfg)

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        now = pd.Timestamp(getattr(ctx, "now", pd.Timestamp.now()))
        pnl_pct = _to_pnl_pct(
            daily_pnl=float(getattr(ctx, "daily_pnl", 0.0)),
            capital=float(getattr(ctx, "capital", 0.0)),
        )
        state = self.tracker.update(now, pnl_pct)
        offset = str(order.get("offset", "")).strip().lower()
        if state.triggered and offset != "close" and self.cfg.block_new_opens_after_trigger:
            return RiskDecision(
                False,
                (
                    f"{self.name}:triggered:peak={state.peak_pnl_pct:.4f}:"
                    f"current={state.current_pnl_pct:.4f}"
                ),
            )
        return RiskDecision(True, "")


def _to_pnl_pct(*, daily_pnl: float, capital: float) -> float:
    if capital <= 0.0:
        return 0.0
    return float(daily_pnl) / float(capital)


__all__ = ["ProfitGiveBackGuard"]
