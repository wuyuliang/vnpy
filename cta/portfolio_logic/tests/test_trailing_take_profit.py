from __future__ import annotations

from cta.config.trailing_take_profit_config import TrailingTakeProfitConfig
from cta.portfolio_logic.position_trend_state import PositionTrendState
from cta.portfolio_logic.trailing_take_profit import TrailingTakeProfitEvaluator


def _state(
    *,
    side: str = "long",
    current_price: float = 110.0,
    current_pnl_pct: float = 0.10,
    trend_score: float = 0.5,
) -> PositionTrendState:
    return PositionTrendState(
        symbol="AU0",
        side=side,
        entry_price=100.0 if side == "long" else 120.0,
        current_price=current_price,
        current_pnl_pct=current_pnl_pct,
        bars_held=10,
        ma_alignment=2 if side == "long" else -2,
        regime_label="trend_up" if side == "long" else "trend_down",
        realized_vol_20d=0.2,
        trend_score=trend_score,
    )


def test_trailing_take_profit_default_disabled() -> None:
    cfg = TrailingTakeProfitConfig()
    ev = TrailingTakeProfitEvaluator(cfg)
    assert ev.update(_state(), highwater_price=112.0, cluster="precious", interval="day") is None


def test_trailing_take_profit_triggers_for_long() -> None:
    cfg = TrailingTakeProfitConfig(
        use_trailing_take_profit=True,
        enabled_by_cluster_interval={"precious|day": True},
    )
    ev = TrailingTakeProfitEvaluator(cfg)
    st = _state(current_price=103.1, current_pnl_pct=0.12)
    # pnl=12% => trail=8%, highwater=112 => trigger at 103.04; current=103.1 -> not hit
    assert ev.update(st, highwater_price=112.0, cluster="precious", interval="day") is None
    st2 = _state(current_price=101.5, current_pnl_pct=0.12)
    out = ev.update(st2, highwater_price=112.0, cluster="precious", interval="day")
    assert out is not None
    assert out.reason == "trailing_take_profit"


def test_trailing_take_profit_trend_reversal_blocks() -> None:
    cfg = TrailingTakeProfitConfig(
        use_trailing_take_profit=True,
        enabled_by_cluster_interval={"precious|day": True},
        require_trend_confirmed=True,
    )
    ev = TrailingTakeProfitEvaluator(cfg)
    st = _state(current_price=100.0, current_pnl_pct=0.15, trend_score=-0.2)
    assert ev.update(st, highwater_price=115.0, cluster="precious", interval="day") is None
