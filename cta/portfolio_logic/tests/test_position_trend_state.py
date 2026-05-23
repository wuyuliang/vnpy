from __future__ import annotations

import pandas as pd

from cta.portfolio_logic.position_trend_state import (
    PositionTrendState,
    compute_position_trend_state,
    compute_trend_score,
)


def test_compute_trend_score_increases_with_alignment_and_profit() -> None:
    weak = compute_trend_score(ma_alignment=1, regime_label="range", current_pnl_pct=0.0)
    strong = compute_trend_score(ma_alignment=2, regime_label="trend_up", current_pnl_pct=0.08)
    assert strong > weak
    assert -1.0 <= strong <= 1.0


def test_compute_position_trend_state_long() -> None:
    position = {
        "symbol": "AU0",
        "side": "long",
        "entry_price": 100.0,
        "bars_held": 12,
    }
    bar = {"datetime": pd.Timestamp("2025-01-01"), "close": 108.0}

    st = compute_position_trend_state(
        position,
        bar,
        ma_alignment_lookup=lambda _s, _dt: 2,
        regime_label_lookup=lambda _s, _dt: "trend_up",
        realized_vol_lookup=lambda _s, _dt: 0.2,
    )
    assert isinstance(st, PositionTrendState)
    assert abs(st.current_pnl_pct - 0.08) < 1e-12
    assert st.is_valid()


def test_compute_position_trend_state_short_sign() -> None:
    position = {"symbol": "RB0", "side": "short", "entry_price": 4000.0}
    bar = {"datetime": pd.Timestamp("2025-01-02"), "close": 3800.0}
    st = compute_position_trend_state(
        position,
        bar,
        ma_alignment_lookup=lambda _s, _dt: -2,
        regime_label_lookup=lambda _s, _dt: "trend_down",
        realized_vol_lookup=lambda _s, _dt: 0.3,
    )
    assert st.current_pnl_pct > 0.0

