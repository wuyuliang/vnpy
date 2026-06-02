"""Profit give-back sizer (W9)."""
from __future__ import annotations

from cta.risk.base import PositionScaler, SignalContext
from cta.risk.sizing.config import ProfitGiveBackConfig
from cta.risk.state.intraday_profit_tracker import IntradayProfitTracker


class ProfitGiveBackSizer(PositionScaler):
    """Scale lots to zero after give-back trigger to force flatting path."""

    def __init__(
        self,
        cfg: ProfitGiveBackConfig | None = None,
        *,
        tracker: IntradayProfitTracker | None = None,
    ) -> None:
        self.cfg = cfg or ProfitGiveBackConfig()
        self.tracker = tracker or IntradayProfitTracker(self.cfg)

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]:
        lots = int(lots_so_far)
        if lots <= 0:
            return 0, "profit_give_back:lots_already_zero"
        pnl_pct = _extract_pnl_pct(ctx)
        state = self.tracker.update(ctx.bar_dt, pnl_pct)
        if state.triggered and self.cfg.block_new_opens_after_trigger:
            return 0, "profit_give_back:triggered"
        return lots, "profit_give_back:ok"


def _extract_pnl_pct(ctx: SignalContext) -> float:
    portfolio = dict(ctx.portfolio or {})
    if "daily_pnl_pct" in portfolio:
        try:
            return float(portfolio.get("daily_pnl_pct", 0.0))
        except (TypeError, ValueError):
            return 0.0
    try:
        daily_pnl = float(portfolio.get("daily_pnl", 0.0))
        equity = float(portfolio.get("equity", 0.0))
    except (TypeError, ValueError):
        return 0.0
    if equity <= 0.0:
        return 0.0
    return daily_pnl / equity


__all__ = ["ProfitGiveBackSizer"]
