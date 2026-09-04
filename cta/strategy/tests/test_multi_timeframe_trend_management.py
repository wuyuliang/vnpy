from __future__ import annotations

import math

import pandas as pd
import pytest

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.strategy.multi_timeframe_trend_management import (
    ExitReason,
    manage_position_on_bar,
    open_position_from_fill,
)
from cta.strategy.multi_timeframe_trend_rules import SignalCandidate


def _candidate(setup_type: str, direction: int, stop_price: float) -> SignalCandidate:
    signal_time = pd.Timestamp("2024-01-01 09:00", tz="Asia/Shanghai")
    return SignalCandidate(
        setup_type=setup_type,
        direction=direction,
        signal_index=10,
        signal_time=signal_time,
        known_at=signal_time,
        trigger=100.0,
        stop_price=stop_price,
    )


def test_pullback_position_has_no_executable_target() -> None:
    position = open_position_from_fill(
        _candidate("pullback_breakout", 1, 98.0),
        actual_entry_price=101.0,
        config=MultiTimeframeTrendConfig(),
    )

    assert math.isnan(position.target_price)


def test_pullback_stop_exits_position() -> None:
    position = open_position_from_fill(
        _candidate("pullback_breakout", 1, 98.0),
        actual_entry_price=100.0,
        config=MultiTimeframeTrendConfig(),
    )
    bar = pd.Series({"open": 100.0, "high": 105.0, "low": 97.0, "close": 103.0})

    _, decision = manage_position_on_bar(
        position,
        bar,
        daily_direction=1,
        config=MultiTimeframeTrendConfig(),
        tick_size=1.0,
    )

    assert decision.should_exit
    assert decision.reason is ExitReason.STOP
    assert decision.price == pytest.approx(98.0)


def test_pullback_reaching_two_r_updates_trailing_stop_without_exit() -> None:
    position = open_position_from_fill(
        _candidate("pullback_breakout", 1, 98.0),
        actual_entry_price=101.0,
        config=MultiTimeframeTrendConfig(),
    )
    known_at = pd.Timestamp("2024-01-01 09:05", tz="Asia/Shanghai")
    bar = pd.Series(
        {
            "open": 106.0,
            "high": 108.0,
            "low": 103.0,
            "close": 107.0,
            "atr14": 1.0,
            "latest_swing_low": 104.0,
            "latest_swing_low_known_at": known_at,
        }
    )

    updated, decision = manage_position_on_bar(
        position,
        bar,
        daily_direction=1,
        config=MultiTimeframeTrendConfig(),
        tick_size=1.0,
    )

    assert not decision.should_exit
    assert updated.stop_price == pytest.approx(103.0)
    assert updated.last_structure_known_at == known_at


def test_always_in_trailing_stop_updates_only_after_completed_bar() -> None:
    position = open_position_from_fill(
        _candidate("always_in", 1, 95.0),
        actual_entry_price=100.0,
        config=MultiTimeframeTrendConfig(),
    )
    known_at = pd.Timestamp("2024-01-01 09:05", tz="Asia/Shanghai")
    bar = pd.Series(
        {
            "open": 100.0,
            "high": 103.0,
            "low": 96.0,
            "close": 102.0,
            "atr14": 2.0,
            "latest_swing_low": 99.0,
            "latest_swing_low_known_at": known_at,
        }
    )

    updated, decision = manage_position_on_bar(
        position,
        bar,
        daily_direction=1,
        config=MultiTimeframeTrendConfig(),
        tick_size=1.0,
    )

    assert not decision.should_exit
    assert updated.stop_price == pytest.approx(98.0)
    assert updated.last_structure_known_at == known_at


def test_daily_direction_invalidation_emits_market_exit_intent() -> None:
    position = open_position_from_fill(
        _candidate("always_in", -1, 105.0),
        actual_entry_price=100.0,
        config=MultiTimeframeTrendConfig(),
    )
    bar = pd.Series({"open": 100.0, "high": 102.0, "low": 98.0, "close": 99.0})

    _, decision = manage_position_on_bar(
        position,
        bar,
        daily_direction=0,
        config=MultiTimeframeTrendConfig(),
        tick_size=1.0,
    )

    assert decision.should_exit
    assert decision.reason is ExitReason.DAILY_DIRECTION_INVALID
    assert pd.isna(decision.price)
