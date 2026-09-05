from __future__ import annotations

from datetime import time

import pandas as pd
import pandas.testing as pdt
import pytest

from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
)
from cta.strategy.common.setup_pipeline import SetupInstrument
from cta.strategy.second_leg_brooks.config import SecondLegBrooksConfig
from cta.strategy.second_leg_brooks.strategy import generate_brooks_candidates


def _sessions() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec(
            "day",
            False,
            (SessionSegment("day", time(9), time(15), time(9)),),
        ),
    )


def _bars() -> pd.DataFrame:
    rows = 40
    end = pd.date_range(
        "2026-01-05 09:01", periods=rows, freq="min", tz="Asia/Shanghai"
    )
    close = [110.0 - index * 0.1 for index in range(rows)]
    frame = pd.DataFrame(
        {
            "bar_end": end,
            "open": [value + 0.05 for value in close],
            "high": [value + 0.15 for value in close],
            "low": [value - 0.15 for value in close],
            "close": close,
            "volume": [10.0] * rows,
            "contract_code": ["RB2605.SHF"] * rows,
            "exchange_trade_date": [pd.Timestamp("2026-01-05").date()] * rows,
        }
    )
    frame.loc[29, ["open", "high", "low", "close"]] = [108.0, 108.2, 107.8, 108.0]
    frame.loc[30, ["open", "high", "low", "close"]] = [106.2, 106.4, 105.8, 106.0]
    frame.loc[31, ["open", "high", "low", "close"]] = [106.1, 106.8, 106.0, 106.6]
    frame.loc[32, ["open", "high", "low", "close"]] = [106.6, 107.2, 106.5, 107.0]
    frame.loc[33, ["open", "high", "low", "close"]] = [107.0, 106.9, 106.3, 106.5]
    frame.loc[34, ["open", "high", "low", "close", "volume"]] = [
        106.4, 106.5, 105.3, 105.4, 30.0
    ]
    return frame


def _instrument() -> SetupInstrument:
    return SetupInstrument(
        symbol="RB",
        exchange="SHFE",
        contract="RB2605.SHF",
        tick_size=0.1,
        multiplier=10.0,
        stressed_round_trip_cost=0.0,
        metadata_asof=pd.Timestamp("2025-01-01", tz="Asia/Shanghai"),
        margin_rate=0.1,
    )


def _config(**overrides: object) -> SecondLegBrooksConfig:
    return SecondLegBrooksConfig(
        signal_timeframe_minutes=1,
        atr_period=10,
        first_leg_lookback=5,
        first_leg_atr_mult=1.5,
        pullback_min_bars=2,
        pullback_max_bars=6,
        entry_body_atr_mult=1.0,
        entry_volume_mult=1.2,
        volume_baseline_min_samples=5,
        **overrides,
    )


def test_candidate_uses_structural_stop_and_relative_swing_index() -> None:
    candidates = generate_brooks_candidates(
        _bars(),
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(),
    )

    candidate = candidates.loc[candidates["signal_i"].eq(34)].iloc[0]
    assert candidate["trigger"] == pytest.approx(105.3)
    assert candidate["stop_price"] == pytest.approx(107.3)
    assert candidate["first_leg_index"] == -4
    assert candidate["pullback_bars"] == 4
    assert candidate["active_time"] == _bars().loc[35, "bar_end"]


def test_future_mutation_cannot_change_existing_candidate_bytes() -> None:
    original = _bars()
    changed = original.copy()
    changed.loc[35:, ["open", "high", "low", "close", "volume"]] = [
        1_000.0,
        2_000.0,
        1.0,
        1_500.0,
        999_999.0,
    ]
    before = generate_brooks_candidates(
        original,
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(),
    )
    after = generate_brooks_candidates(
        changed,
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(),
    )
    columns = ["candidate_id", "signal_time", "trigger", "stop_price", "quantity"]
    cutoff = original.loc[34, "bar_end"]

    pdt.assert_frame_equal(
        before.loc[before["signal_time"].le(cutoff), columns].reset_index(drop=True),
        after.loc[after["signal_time"].le(cutoff), columns].reset_index(drop=True),
        check_exact=True,
    )


def test_for_replay_scales_exit_windows_without_changing_signal_timeframe() -> None:
    config = SecondLegBrooksConfig(
        signal_timeframe_minutes=5,
        no_progress_bars=8,
        follow_through_bars=3,
        order_ttl_bars=5,
    )

    replay = config.for_replay()

    assert replay.signal_timeframe_minutes == 5
    assert replay.no_progress_bars == 40
    assert replay.follow_through_bars == 15
    assert replay.order_ttl_bars == 5

