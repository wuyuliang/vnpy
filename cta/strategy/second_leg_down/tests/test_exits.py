from __future__ import annotations

import pandas as pd

from cta.strategy.multi_timeframe_trend_backtest import engine
from cta.strategy.second_leg_down.config import SecondLegDownConfig
from cta.strategy.tests.test_multi_timeframe_trend_backtest import _MetadataStore
from cta.strategy.tests.test_multi_timeframe_trend_profit_floor import _position


TZ = "Asia/Shanghai"


def _short_position():
    position = _position(entry=100.0, stop=101.0, direction=-1)
    position.pending.candidate.update(
        {
            "candidate_id": "RB-000001",
            "contract_code": "RB2605.SHF",
            "setup_type": "second_leg_down",
        }
    )
    return position


def _bar(index: int, open_: float, high: float, low: float, close: float):
    timestamp = pd.Timestamp("2026-01-05 09:06", tz=TZ) + pd.Timedelta(
        minutes=index - 1
    )
    return pd.Series(
        {
            "bar_end": timestamp,
            "open": open_,
            "high": high,
            "low": low,
            "close": close,
            "atr": 1.0,
            "contract_code": "RB2605.SHF",
            "exchange_trade_date": timestamp.date(),
            "_bar_index": index,
        }
    )


def _manage(position, bar, config, pending_reason=""):
    return engine._manage_open_position(
        position,
        bar,
        metadata_store=_MetadataStore(
            contract_size=10.0,
            price_tick=0.1,
            stressed_round_trip_fee_cash=0.0,
        ),
        root_symbol="RB",
        exchange="SHFE",
        pending_reason=pending_reason,
        protective_first=True,
        config=config,
    )


def test_no_progress_triggers_on_fifth_close_and_fills_next_open() -> None:
    config = SecondLegDownConfig(profit_floor_enabled=False)
    position = _short_position()

    for index in range(1, 5):
        decision = _manage(position, _bar(index, 100.0, 100.2, 99.8, 100.0), config)
        assert decision.pending_reason == ""
    decision = _manage(position, _bar(5, 100.0, 100.2, 99.8, 100.0), config)
    assert decision.reason == ""
    assert decision.pending_reason == "NO_PROGRESS_TIME_STOP"

    filled = _manage(
        position,
        _bar(6, 99.7, 99.9, 99.6, 99.8),
        config,
        decision.pending_reason,
    )
    assert filled.reason == "NO_PROGRESS_TIME_STOP"
    assert filled.reference == 99.7


def test_no_progress_does_not_trigger_after_half_r_peak() -> None:
    config = SecondLegDownConfig(profit_floor_enabled=False)
    position = _short_position()
    bars = [
        _bar(1, 100.0, 100.1, 99.4, 99.7),
        *[_bar(index, 99.8, 100.0, 99.6, 99.8) for index in range(2, 6)],
    ]

    decisions = [_manage(position, bar, config) for bar in bars]

    assert all(item.pending_reason != "NO_PROGRESS_TIME_STOP" for item in decisions)


def test_stop_beats_no_progress_on_the_same_bar() -> None:
    config = SecondLegDownConfig(profit_floor_enabled=False)
    position = _short_position()
    for index in range(1, 5):
        _manage(position, _bar(index, 100.0, 100.2, 99.8, 100.0), config)

    decision = _manage(position, _bar(5, 100.0, 101.2, 99.8, 101.0), config)

    assert decision.reason == "STOP"
    assert decision.pending_reason == ""


def test_no_progress_beats_profit_floor_on_the_same_bar() -> None:
    config = SecondLegDownConfig(no_progress_min_r=2.0)
    position = _short_position()
    position.maximum_favorable_price = 99.0
    position.profit_floor_price = 99.5

    decision = _manage(position, _bar(5, 99.4, 99.6, 99.2, 99.4), config)

    assert decision.pending_reason == "NO_PROGRESS_TIME_STOP"


def test_missing_follow_through_lowers_target_to_one_r() -> None:
    config = SecondLegDownConfig(profit_floor_enabled=False, no_progress_bars=0)
    position = _short_position()
    for index in range(1, 3):
        decision = _manage(
            position, _bar(index, 100.0, 100.1, 99.7, 99.9), config
        )
        assert decision.pending_reason == ""

    decision = _manage(position, _bar(3, 99.9, 100.0, 98.9, 99.0), config)
    assert decision.pending_reason == "NO_FOLLOW_THROUGH_TARGET"

    filled = _manage(
        position,
        _bar(4, 98.8, 99.0, 98.5, 98.7),
        config,
        decision.pending_reason,
    )
    assert filled.reason == "NO_FOLLOW_THROUGH_TARGET"
    assert filled.reference == 98.8


def test_big_bear_follow_through_keeps_the_original_management() -> None:
    config = SecondLegDownConfig(profit_floor_enabled=False, no_progress_bars=0)
    position = _short_position()

    decision = _manage(position, _bar(1, 100.0, 100.0, 97.8, 98.0), config)
    for index in range(2, 5):
        decision = _manage(
            position, _bar(index, 98.0, 98.2, 97.8, 98.0), config
        )

    assert position.follow_through_seen
    assert not position.no_follow_through_target_active
    assert decision.pending_reason == ""


def test_profit_floor_beats_no_follow_through_target() -> None:
    config = SecondLegDownConfig(no_progress_bars=0)
    position = _short_position()
    position.maximum_favorable_price = 99.4
    position.profit_floor_price = 99.5
    _manage(position, _bar(1, 99.6, 99.7, 99.4, 99.5), config)
    _manage(position, _bar(2, 99.5, 99.6, 99.3, 99.4), config)

    decision = _manage(position, _bar(3, 99.4, 99.6, 98.9, 99.1), config)

    assert decision.pending_reason == "PROFIT_FLOOR"


def test_stop_beats_no_follow_through_target() -> None:
    config = SecondLegDownConfig(profit_floor_enabled=False, no_progress_bars=0)
    position = _short_position()
    _manage(position, _bar(1, 100.0, 100.1, 99.8, 100.0), config)
    _manage(position, _bar(2, 100.0, 100.1, 99.8, 100.0), config)

    decision = _manage(position, _bar(3, 100.0, 101.2, 98.8, 99.0), config)

    assert decision.reason == "STOP"


def test_shared_engine_accepts_second_leg_risk_band_at_submit_and_fill() -> None:
    config = SecondLegDownConfig()
    candidate = {
        "candidate_id": "RB-000001",
        "contract_code": "RB2605.SHF",
        "setup_type": "second_leg_down",
        "direction": -1,
        "signal_time": pd.Timestamp("2026-01-05 09:05", tz=TZ),
        "active_time": pd.Timestamp("2026-01-05 09:06", tz=TZ),
        "exchange_trade_date": pd.Timestamp("2026-01-05").date(),
        "trigger": 100.0,
        "stop_price": 110.0,
    }
    pending = engine._submit_candidate(
        candidate,
        root_symbol="RB",
        exchange="SHFE",
        metadata_store=_MetadataStore(
            contract_size=10.0,
            margin_rate=0.1,
            stressed_round_trip_fee_cash=0.0,
            price_tick=1.0,
        ),
        config=config,
        equity=1_000_000.0,
    )

    status, reason, fill, reference, quantity = engine._match_entry(
        pending,
        _bar(1, 100.0, 101.0, 99.0, 100.0),
        config=config,
        equity=1_000_000.0,
    )

    assert pending.quantity == 50
    assert (status, reason, fill, reference, quantity) == (
        "FILLED",
        "FILLED",
        100.0,
        100.0,
        50,
    )


def test_shared_engine_accepts_an_empty_candidate_contract() -> None:
    frame = pd.DataFrame(
        columns=[
            "candidate_id",
            "contract_code",
            "setup_type",
            "direction",
            "signal_time",
            "active_time",
            "expires_at",
            "trigger",
            "stop_price",
            "filtered_reason",
        ]
    )

    result = engine._validated_candidates(frame)

    assert result.empty
