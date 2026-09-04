from __future__ import annotations

import inspect
from types import SimpleNamespace

import pandas as pd

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.strategy.multi_timeframe_trend_backtest import engine


def _pending() -> SimpleNamespace:
    return SimpleNamespace(
        candidate={
            "active_time": pd.Timestamp(
                "2026-03-02 09:01", tz="Asia/Shanghai"
            ),
            "expires_at": pd.Timestamp(
                "2026-03-02 09:05", tz="Asia/Shanghai"
            ),
            "contract_code": "AG2604.SHF",
        }
    )


def test_pending_order_component_handles_wait_expiry_and_match(
    monkeypatch,
) -> None:
    pending = _pending()
    config = MultiTimeframeTrendConfig(entry_blocked_session_windows=())
    bar = {"contract_code": "AG2604.SHF"}

    waiting = engine._process_pending_order(
        pending,
        bar,
        pd.Timestamp("2026-03-02 09:00", tz="Asia/Shanghai"),
        config=config,
        equity=100_000.0,
    )
    expired = engine._process_pending_order(
        pending,
        bar,
        pd.Timestamp("2026-03-02 09:06", tz="Asia/Shanghai"),
        config=config,
        equity=100_000.0,
    )
    monkeypatch.setattr(
        engine,
        "_match_entry",
        lambda *args, **kwargs: ("FILLED", "", 101.0, 100.0, 2),
    )
    filled = engine._process_pending_order(
        pending,
        bar,
        pd.Timestamp("2026-03-02 09:02", tz="Asia/Shanghai"),
        config=config,
        equity=100_000.0,
    )

    assert waiting.status == "WAITING"
    assert expired.status == "EXPIRED"
    assert expired.reason == "ORDER_EXPIRED"
    assert filled.status == "FILLED"
    assert filled.match == ("FILLED", "", 101.0, 100.0, 2)


def test_candidate_filter_component_preserves_requested_order() -> None:
    candidate = {
        "filtered_reason": "SOURCE_FILTER",
        "direction": 1,
        "daily_bull_trend_id": 1,
        "trigger": 100.0,
        "prior_5d_high": 90.0,
    }
    decision = engine._apply_candidate_filters(
        candidate,
        filled_bull_trend_ids=set(),
        timestamp=pd.Timestamp("2026-03-02 09:00", tz="Asia/Shanghai"),
        config=MultiTimeframeTrendConfig(entry_blocked_session_windows=()),
        checks=("stored", "breakout", "session"),
    )

    assert decision.reason == "SOURCE_FILTER"
    assert decision.detail == ""


def test_both_replays_delegate_shared_event_operations() -> None:
    single = inspect.getsource(engine.replay_trend_strategy)
    portfolio = inspect.getsource(engine.replay_trend_portfolio)

    for source in (single, portfolio):
        assert "_process_pending_order(" in source
        assert "_apply_candidate_filters(" in source
        assert "_manage_open_position(" in source
        assert "_match_entry(" not in source
