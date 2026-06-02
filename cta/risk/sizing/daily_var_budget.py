"""Daily VaR budget sizer (W10)."""
from __future__ import annotations

from math import floor

from cta.risk.base import PositionScaler, SignalContext, normalize_cluster
from cta.risk.sizing.config import DailyVaRBudgetConfig
from cta.risk.state.daily_var_tracker import DailyVaRTracker


class DailyVaRBudgetSizer(PositionScaler):
    """Scale lots by cluster/account daily loss vs VaR budget."""

    def __init__(
        self,
        cfg: DailyVaRBudgetConfig | None = None,
        *,
        tracker: DailyVaRTracker | None = None,
    ) -> None:
        self.cfg = cfg or DailyVaRBudgetConfig()
        self.tracker = tracker or DailyVaRTracker(
            reset_at_session_open=self.cfg.reset_at_session_open,
        )

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]:
        lots = int(lots_so_far)
        if lots <= 0:
            return 0, "daily_var:lots_already_zero"
        equity = _to_float(ctx.portfolio.get("equity", 0.0))
        if equity <= 0.0:
            return lots, "daily_var:no_equity"
        cluster = normalize_cluster(ctx.candidate.get("cluster")) or "other"
        account_pnl, cluster_pnl = self._resolve_daily_pnl(ctx, cluster)
        account_budget = equity * float(self.cfg.account_budget_bp) / 10_000.0
        cluster_budget_bp = float(
            self.cfg.budget_bp_by_cluster.get(cluster, self.cfg.default_budget_bp)
        )
        cluster_budget = equity * cluster_budget_bp / 10_000.0
        account_loss = max(0.0, -float(account_pnl))
        cluster_loss = max(0.0, -float(cluster_pnl))
        if account_budget > 0.0 and account_loss >= account_budget:
            return 0, "daily_var:account_hard_stop"
        if cluster_budget > 0.0 and cluster_loss >= cluster_budget:
            return 0, "daily_var:cluster_hard_stop"
        warning = False
        if account_budget > 0.0 and account_loss >= account_budget * float(self.cfg.warning_ratio):
            warning = True
        if cluster_budget > 0.0 and cluster_loss >= cluster_budget * float(self.cfg.warning_ratio):
            warning = True
        if warning:
            new_lots = max(0, floor(lots * float(self.cfg.warning_mult)))
            if new_lots == 0 and lots >= 1 and self.cfg.warning_mult > 0:
                new_lots = 1
            return new_lots, "daily_var:warning"
        return lots, "daily_var:ok"

    def _resolve_daily_pnl(self, ctx: SignalContext, cluster: str) -> tuple[float, float]:
        portfolio = dict(ctx.portfolio or {})
        account_pnl = _to_float(portfolio.get("daily_pnl", 0.0))
        by_cluster = portfolio.get("daily_pnl_by_cluster")
        if isinstance(by_cluster, dict):
            cluster_pnl = _to_float(by_cluster.get(cluster, 0.0))
        else:
            cluster_pnl = self.tracker.cluster_daily_pnl(cluster, ctx.bar_dt)
        if "daily_pnl" not in portfolio:
            account_pnl = self.tracker.account_daily_pnl(ctx.bar_dt)
        return account_pnl, cluster_pnl


def _to_float(value: object) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["DailyVaRBudgetSizer"]
