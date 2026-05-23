"""Profit-aware horizon extension helpers."""
from __future__ import annotations

from cta.config.profit_aware_horizon_config import ProfitAwareHorizonConfig
from cta.portfolio_logic.config import normalize_portfolio_interval
from cta.portfolio_logic.position_trend_state import PositionTrendState


def resolve_max_holding_bars(
    state: PositionTrendState,
    *,
    interval: str,
    cfg: ProfitAwareHorizonConfig,
    cluster: str | None = None,
) -> int:
    """Resolve max holding bars under profit-aware horizon config."""
    iv = normalize_portfolio_interval(interval)
    base = int(cfg.base_max_holding_bars_by_interval.get(iv, 60))
    if not bool(cfg.use_profit_aware_horizon):
        return base
    if not cfg.is_enabled(cluster, iv):
        return base
    if float(state.current_pnl_pct) < float(cfg.activation_pnl_pct):
        return base
    if bool(cfg.require_trend_confirmed) and float(state.trend_score) <= 0.0:
        return base
    ext = int(cfg.extended_max_holding_bars_by_interval.get(iv, base))
    return min(ext, int(cfg.cap_total_holding_bars))


__all__ = ["resolve_max_holding_bars"]

