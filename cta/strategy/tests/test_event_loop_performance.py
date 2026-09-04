from __future__ import annotations

from datetime import date, time
import inspect
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.strategy.multi_timeframe_trend_backtest import engine


def _position(*, quantity: int = 2) -> SimpleNamespace:
    metadata = SimpleNamespace(
        contract_size=10.0,
        stressed_round_trip_fee_cash=3.0,
    )
    pending = SimpleNamespace(
        candidate={"direction": 1},
        metadata=metadata,
    )
    return SimpleNamespace(
        pending=pending,
        quantity=quantity,
        entry_price=100.0,
    )


def _sessions() -> tuple[SimpleNamespace, ...]:
    return (
        SimpleNamespace(
            session_id="day",
            is_night=False,
            segments=(
                SimpleNamespace(
                    segment_id="afternoon",
                    start=time(13, 30),
                    end=time(15, 0),
                    bucket_anchor=time(13, 30),
                ),
            ),
        ),
    )


def test_portfolio_event_rows_do_not_use_iterrows() -> None:
    source = inspect.getsource(engine.replay_trend_portfolio)

    assert "event_rows.iterrows()" not in source


def test_incremental_equity_tracks_changes_and_reconciles() -> None:
    tracker = engine._IncrementalPortfolioEquity()
    positions = {"AG": _position()}
    marks = {"AG": 101.0}

    tracker.update_position("AG", positions["AG"], marks["AG"])
    assert tracker.marked_equity(1_000.0, positions) == 1_014.0
    assert tracker.marked_equity(1_000.0, positions) == 1_014.0

    marks["AG"] = 102.0
    tracker.update_position("AG", positions["AG"], marks["AG"])
    assert tracker.marked_equity(1_000.0, positions) == 1_034.0
    positions["AG"].quantity = 1
    tracker.update_position("AG", positions["AG"], marks["AG"])
    assert tracker.marked_equity(1_000.0, positions) == 1_017.0

    tracker.reconcile(1_000.0, positions, marks, trade_date=date(2026, 3, 2))
    tracker._components["AG"] = (0.0, 0.0)
    with pytest.raises(RuntimeError, match="incremental portfolio equity drift"):
        tracker.reconcile(
            1_000.0,
            positions,
            marks,
            trade_date=date(2026, 3, 3),
        )


def test_overnight_reduction_bounds_are_cached_by_session_and_date() -> None:
    cache: dict[object, object] = {}
    sessions = _sessions()

    assert engine._is_overnight_reduction_time(
        pd.Timestamp("2026-03-02 14:55", tz="Asia/Shanghai"),
        sessions,
        10,
        bounds_cache=cache,
    )
    assert engine._is_overnight_reduction_time(
        pd.Timestamp("2026-03-02 14:56", tz="Asia/Shanghai"),
        sessions,
        10,
        bounds_cache=cache,
    )
    assert len(cache) == 1

    assert not engine._is_overnight_reduction_time(
        pd.Timestamp("2026-03-03 14:49", tz="Asia/Shanghai"),
        sessions,
        10,
        bounds_cache=cache,
    )
    assert len(cache) == 2
