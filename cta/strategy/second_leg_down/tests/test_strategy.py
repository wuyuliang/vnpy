from __future__ import annotations

from datetime import time

import pandas as pd
import pandas.testing as pdt
import pytest

from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
)
from cta.strategy.multi_timeframe_trend_strategy import CANDIDATE_COLUMNS
from cta.strategy.second_leg_down.config import SecondLegDownConfig
from cta.strategy.second_leg_down.strategy import (
    SECOND_LEG_AUDIT_COLUMNS,
    SecondLegInstrument,
    generate_second_leg_down_candidates,
)


def _sessions() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec(
            "day",
            False,
            (SessionSegment("day", time(9), time(15), time(9)),),
        ),
    )


def _bars() -> pd.DataFrame:
    rows = 35
    end = pd.date_range(
        "2026-01-05 09:01", periods=rows, freq="min", tz="Asia/Shanghai"
    )
    frame = pd.DataFrame(
        {
            "bar_end": end,
            "open": [110.0 - index * 0.1 for index in range(rows)],
            "high": [110.05 - index * 0.1 for index in range(rows)],
            "low": [109.85 - index * 0.1 for index in range(rows)],
            "close": [109.9 - index * 0.1 for index in range(rows)],
            "volume": [10.0] * rows,
            "contract_code": ["RB2605.SHF"] * rows,
            "exchange_trade_date": [pd.Timestamp("2026-01-05").date()] * rows,
        }
    )
    frame.loc[27, ["open", "high", "low", "close", "volume"]] = [
        108.0, 108.05, 106.9, 107.0, 30.0
    ]
    frame.loc[28, ["open", "high", "low", "close", "volume"]] = [
        107.0, 107.02, 105.95, 106.0, 30.0
    ]
    return frame


def _daily_bars(*, include_future: bool = False) -> pd.DataFrame:
    end = pd.date_range(
        end="2026-01-04 15:00",
        periods=30,
        freq="D",
        tz="Asia/Shanghai",
    )
    frame = pd.DataFrame(
        {
            "bar_end": end,
            "close": [130.0 - index for index in range(len(end))],
        }
    )
    if include_future:
        frame.loc[len(frame)] = [
            pd.Timestamp("2026-01-05 15:00", tz="Asia/Shanghai"),
            1_000.0,
        ]
    return frame


def _instrument() -> SecondLegInstrument:
    return SecondLegInstrument(
        symbol="RB",
        exchange="SHFE",
        contract="RB2605.SHF",
        tick_size=1.0,
        multiplier=10.0,
        stressed_round_trip_cost=0.0,
        metadata_asof=pd.Timestamp("2025-01-01", tz="Asia/Shanghai"),
        margin_rate=0.1,
    )


def _config(**overrides: object) -> SecondLegDownConfig:
    return SecondLegDownConfig(
        body_mode="per_bar",
        atr_period=3,
        # fixture 就是 1 分钟 K 线，信号周期也钉成 1，
        # 否则 for_replay() 会把窗口按 5 分钟再放大一遍
        signal_timeframe_minutes=1,
        order_ttl_bars=3,
        big_body_atr_mult=1.0,
        volume_surge_mult=2.0,
        volume_baseline_min_samples=5,
        structure_lookback_bars=5,
        **overrides,
    )


def test_candidate_contract_and_next_bar_activation() -> None:
    candidates = generate_second_leg_down_candidates(
        _bars(),
        daily_bars=_daily_bars(),
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(),
        equity=1_000_000.0,
    )

    candidate = candidates.loc[candidates["signal_time"].eq(_bars().loc[28, "bar_end"])].iloc[0]
    assert list(candidates.columns[: len(CANDIDATE_COLUMNS)]) == list(CANDIDATE_COLUMNS)
    assert set(SECOND_LEG_AUDIT_COLUMNS).issubset(candidates.columns)
    assert candidate["direction"] == -1
    assert candidate["setup_type"] == "second_leg_down"
    assert candidate["trigger"] == pytest.approx(105.0)
    assert candidate["stop_price"] == pytest.approx(108.0)
    assert candidate["active_time"] == _bars().loc[29, "bar_end"]
    ttl = _config().for_replay().order_ttl_bars
    assert candidate["expires_at"] == _bars().loc[29 + ttl - 1, "bar_end"]
    assert candidate["quantity"] > 0
    assert candidate["capital_basis"] == "margin"
    assert candidate["daily_feature_asof"] == pd.Timestamp(
        "2026-01-04 15:00", tz="Asia/Shanghai"
    )
    assert candidate["daily_direction"] == -1
    assert candidate["daily_ema5"] < candidate["daily_ema10"] < candidate["daily_ema20"]


@pytest.mark.parametrize(
    ("stop_mode", "expected"),
    [
        ("pattern_open", 108.0),
        ("pattern_high", 109.05),
        ("atr", None),
    ],
)
def test_candidate_stop_modes(stop_mode: str, expected: float) -> None:
    candidates = generate_second_leg_down_candidates(
        _bars(),
        daily_bars=_daily_bars(),
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(stop_mode=stop_mode, stop_atr_mult=1.5),
        equity=1_000_000.0,
    )

    candidate = candidates.loc[candidates["signal_time"].eq(_bars().loc[28, "bar_end"])].iloc[0]
    if stop_mode == "atr":
        expected = candidate["trigger"] + 1.5 * candidate["feature_atr14"]
    assert candidate["stop_price"] == pytest.approx(expected)


def test_future_mutation_cannot_change_existing_candidate_bytes() -> None:
    original = _bars()
    signal_time = original.loc[28, "bar_end"]
    changed = original.copy()
    changed.loc[29:, ["open", "high", "low", "close", "volume"]] = [
        1_000.0,
        2_000.0,
        1.0,
        1_500.0,
        999_999.0,
    ]

    before = generate_second_leg_down_candidates(
        original,
        daily_bars=_daily_bars(),
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(),
        equity=1_000_000.0,
    )
    after = generate_second_leg_down_candidates(
        changed,
        daily_bars=_daily_bars(),
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(),
        equity=1_000_000.0,
    )
    keys = [
        "candidate_id",
        "signal_time",
        "trigger",
        "stop_price",
        "quantity",
    ]
    before = before.loc[before["signal_time"].le(signal_time), keys].reset_index(drop=True)
    after = after.loc[after["signal_time"].le(signal_time), keys].reset_index(drop=True)

    pdt.assert_frame_equal(before, after, check_exact=True)


def test_adjusted_signal_prices_are_mapped_back_to_actual_contract_prices() -> None:
    bars = _bars()
    bars["adjustment_scale"] = 2.0
    bars["adjustment_offset"] = 10.0

    candidates = generate_second_leg_down_candidates(
        bars,
        daily_bars=_daily_bars(),
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(),
        equity=1_000_000.0,
    )

    candidate = candidates.loc[candidates["signal_time"].eq(bars.loc[28, "bar_end"])].iloc[0]
    assert candidate["trigger"] == pytest.approx(47.0)
    assert candidate["stop_price"] == pytest.approx(49.0)
    assert candidate["adjustment_scale"] == pytest.approx(2.0)
    assert candidate["adjustment_offset"] == pytest.approx(10.0)


def test_future_daily_mutation_cannot_change_existing_candidate_bytes() -> None:
    bars = _bars()

    before = generate_second_leg_down_candidates(
        bars,
        daily_bars=_daily_bars(),
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(),
        equity=1_000_000.0,
    )
    after = generate_second_leg_down_candidates(
        bars,
        daily_bars=_daily_bars(include_future=True),
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(),
        equity=1_000_000.0,
    )

    pdt.assert_frame_equal(before, after, check_exact=True)


def test_disabled_volume_filter_records_failed_volume_audit() -> None:
    bars = _bars()
    bars.loc[[27, 28], "volume"] = 10.0

    candidates = generate_second_leg_down_candidates(
        bars,
        daily_bars=_daily_bars(),
        sessions=_sessions(),
        instrument=_instrument(),
        config=_config(volume_filter_enabled=False),
        equity=1_000_000.0,
    )

    candidate = candidates.loc[candidates["signal_time"].eq(bars.loc[28, "bar_end"])].iloc[0]
    assert candidate["last_volume_ratio"] == pytest.approx(1.0)
    assert candidate["feature_volume_expanded"] == 0


# ---------------------------------------------------------------------------
# 信号周期与执行时间轴分离
# ---------------------------------------------------------------------------
def test_for_replay_scales_bar_windows_to_execution_bars() -> None:
    config = SecondLegDownConfig(signal_timeframe_minutes=5)
    replay = config.for_replay()
    # 以"根"计的出场窗口说的是信号周期的根数，落到 1 分钟引擎上要乘回去
    assert replay.no_progress_bars == config.no_progress_bars * 5
    assert replay.follow_through_bars == config.follow_through_bars * 5
    default = SecondLegDownConfig()
    assert default.for_replay().no_progress_bars == (
        default.no_progress_bars * default.signal_timeframe_minutes
    )


def test_orders_live_on_the_execution_timeline_not_the_signal_one() -> None:
    """5 分钟信号在 10:05 收盘，订单属于 10:06 那根 1 分钟 K 线，而不是 10:10。"""
    from cta.strategy.second_leg_down.strategy import _order_window

    execution = pd.DatetimeIndex(
        pd.date_range("2026-01-05 10:00", periods=30, freq="min", tz="Asia/Shanghai")
    )
    config = SecondLegDownConfig(signal_timeframe_minutes=5, order_ttl_bars=3)
    active, expires = _order_window(
        pd.Timestamp("2026-01-05 10:05", tz="Asia/Shanghai"),
        execution_index=execution,
        signal_frame=pd.DataFrame(),
        signal_index=0,
        config=config,
    )
    assert active == pd.Timestamp("2026-01-05 10:06", tz="Asia/Shanghai")
    # 3 根信号 K 线 = 15 根执行 K 线，从 10:06 起算
    assert expires == pd.Timestamp("2026-01-05 10:20", tz="Asia/Shanghai")


def test_order_window_returns_none_past_the_end_of_the_timeline() -> None:
    from cta.strategy.second_leg_down.strategy import _order_window

    execution = pd.DatetimeIndex(
        pd.date_range("2026-01-05 10:00", periods=3, freq="min", tz="Asia/Shanghai")
    )
    active, expires = _order_window(
        pd.Timestamp("2026-01-05 10:02", tz="Asia/Shanghai"),
        execution_index=execution,
        signal_frame=pd.DataFrame(),
        signal_index=0,
        config=SecondLegDownConfig(),
    )
    assert active is None and expires is None
