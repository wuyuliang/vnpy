from __future__ import annotations

from cta.config.profit_aware_horizon_config import ProfitAwareHorizonConfig
from cta.portfolio_logic.position_trend_state import PositionTrendState
from cta.portfolio_logic.profit_aware_horizon import resolve_max_holding_bars


def _state(pnl: float, trend: float) -> PositionTrendState:
    return PositionTrendState(
        symbol="AU0",
        side="long",
        entry_price=100.0,
        current_price=100.0 * (1.0 + pnl),
        current_pnl_pct=pnl,
        bars_held=12,
        ma_alignment=2,
        regime_label="trend_up",
        realized_vol_20d=0.2,
        trend_score=trend,
    )


def test_profit_aware_horizon_default_returns_base() -> None:
    cfg = ProfitAwareHorizonConfig()
    bars = resolve_max_holding_bars(_state(0.10, 0.8), interval="day", cfg=cfg, cluster="precious")
    assert bars == 60


def test_profit_aware_horizon_extends_when_profit_and_trend_confirmed() -> None:
    cfg = ProfitAwareHorizonConfig(
        use_profit_aware_horizon=True,
        enabled_by_cluster_interval={"precious|day": True},
        base_max_holding_bars_by_interval={"day": 60},
        extended_max_holding_bars_by_interval={"day": 90},
    )
    bars = resolve_max_holding_bars(_state(0.08, 0.7), interval="day", cfg=cfg, cluster="precious")
    assert bars == 90


def test_profit_aware_horizon_keeps_base_when_trend_reversed() -> None:
    cfg = ProfitAwareHorizonConfig(
        use_profit_aware_horizon=True,
        enabled_by_cluster_interval={"precious|day": True},
    )
    bars = resolve_max_holding_bars(_state(0.08, -0.1), interval="day", cfg=cfg, cluster="precious")
    assert bars == 60

