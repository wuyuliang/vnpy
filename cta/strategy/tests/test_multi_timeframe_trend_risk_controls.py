"""Tests for the chop/gap execution controls added on top of the trend strategy."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, time
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.config.futures_sector_map import sector_for_root
from cta.config.multi_timeframe_trend_config import (
    MultiTimeframeTrendConfig,
    is_entry_window_blocked,
    minute_of_day_from_text,
    minute_of_day_from_time,
)
from cta.strategy.multi_timeframe_trend_backtest.engine import (
    _DailyCircuitState,
    _DrawdownScalingState,
    _PortfolioScalingState,
    _SymbolScalingState,
    _advance_daily_circuit,
    _daily_circuit_blocked,
    _high_gap_flags_by_trade_date,
    _is_pre_break_time,
    _position_scaling_snapshot,
    _pre_break_decision,
    _sector_exposure_blocked,
)


@dataclass(frozen=True)
class _Segment:
    segment_id: str
    start: time
    end: time


@dataclass(frozen=True)
class _Session:
    session_id: str
    is_night: bool
    segments: tuple[_Segment, ...]


DAY_SESSION = _Session(
    "day",
    False,
    (
        _Segment("morning", time(9, 0), time(11, 30)),
        _Segment("afternoon", time(13, 30), time(15, 0)),
    ),
)
NIGHT_SESSION = _Session(
    "night",
    True,
    (_Segment("night", time(21, 0), time(23, 0)),),
)
LATE_NIGHT_SESSION = _Session(
    "night",
    True,
    (_Segment("night", time(21, 0), time(2, 30)),),
)


def _at(text: str) -> pd.Timestamp:
    return pd.Timestamp(text, tz="Asia/Shanghai")


# --------------------------------------------------------------------------
# 入场时段窗口
# --------------------------------------------------------------------------
def test_default_entry_windows_cover_the_two_loss_making_blocks() -> None:
    windows = MultiTimeframeTrendConfig().entry_blocked_session_windows
    assert windows == (("13:00", "15:00"), ("22:00", "02:30"))


@pytest.mark.parametrize(
    "clock,blocked",
    [
        ("09:30", False),
        ("11:29", False),
        ("12:59", False),
        ("13:00", True),
        ("14:59", True),
        ("15:00", False),
        ("21:00", False),
        ("21:59", False),
        ("22:00", True),
        ("23:30", True),
        ("00:10", True),
        ("02:29", True),
        ("02:30", False),
    ],
)
def test_entry_window_boundaries_are_half_open(clock: str, blocked: bool) -> None:
    windows = MultiTimeframeTrendConfig().entry_blocked_session_windows
    assert (
        is_entry_window_blocked(minute_of_day_from_text(clock), windows)
        is blocked
    )


def test_minute_of_day_converters_have_distinct_input_contracts() -> None:
    assert minute_of_day_from_text("13:45") == 13 * 60 + 45
    assert minute_of_day_from_time(time(13, 45)) == 13 * 60 + 45
    with pytest.raises(TypeError):
        minute_of_day_from_text(time(13, 45))  # type: ignore[arg-type]
    with pytest.raises(TypeError):
        minute_of_day_from_time("13:45")  # type: ignore[arg-type]


def test_empty_entry_windows_block_nothing() -> None:
    config = MultiTimeframeTrendConfig(entry_blocked_session_windows=())
    assert config.entry_blocked_session_windows == ()
    for minute in range(0, 1440, 17):
        assert not is_entry_window_blocked(
            minute, config.entry_blocked_session_windows
        )


@pytest.mark.parametrize(
    "windows",
    [
        (("13:00", "13:00"),),
        (("25:00", "15:00"),),
        (("13:0", "15:00"),),
        (("13:00", "15:00"), ("13:00", "15:00")),
        "13:00-15:00",
    ],
)
def test_invalid_entry_windows_are_rejected(windows: object) -> None:
    with pytest.raises(ValueError):
        MultiTimeframeTrendConfig(entry_blocked_session_windows=windows)


# --------------------------------------------------------------------------
# 组合级日内熔断
# --------------------------------------------------------------------------
def _circuit_config(**kwargs: object) -> MultiTimeframeTrendConfig:
    return MultiTimeframeTrendConfig(**kwargs)


def test_circuit_trips_on_consecutive_losses() -> None:
    config = _circuit_config()
    state = _DailyCircuitState()
    day = date(2026, 3, 24)
    _advance_daily_circuit(
        state, trade_date=day, net_pnl=-100.0, equity=1e6, config=config
    )
    assert not _daily_circuit_blocked(state, day, config)
    _advance_daily_circuit(
        state, trade_date=day, net_pnl=-100.0, equity=1e6, config=config
    )
    assert "LOSS_STREAK" in _daily_circuit_blocked(state, day, config)


def test_circuit_trips_on_cumulative_loss_before_streak() -> None:
    config = _circuit_config()
    state = _DailyCircuitState()
    day = date(2026, 3, 24)
    _advance_daily_circuit(
        state, trade_date=day, net_pnl=-25_000.0, equity=1e6, config=config
    )
    detail = _daily_circuit_blocked(state, day, config)
    assert "LOSS_R" in detail
    assert state.loss_streak == 1


def test_circuit_streak_resets_on_a_winner_but_cumulative_loss_does_not() -> None:
    config = _circuit_config()
    state = _DailyCircuitState()
    day = date(2026, 3, 24)
    _advance_daily_circuit(
        state, trade_date=day, net_pnl=-8_000.0, equity=1e6, config=config
    )
    _advance_daily_circuit(
        state, trade_date=day, net_pnl=1_000.0, equity=1e6, config=config
    )
    assert state.loss_streak == 0
    assert state.realized_pnl == pytest.approx(-7_000.0)
    assert not _daily_circuit_blocked(state, day, config)
    _advance_daily_circuit(
        state, trade_date=day, net_pnl=-14_000.0, equity=1e6, config=config
    )
    assert "LOSS_R" in _daily_circuit_blocked(state, day, config)


def test_circuit_resets_on_the_next_exchange_trade_date() -> None:
    config = _circuit_config()
    state = _DailyCircuitState()
    day = date(2026, 3, 24)
    for _ in range(2):
        _advance_daily_circuit(
            state, trade_date=day, net_pnl=-100.0, equity=1e6, config=config
        )
    assert _daily_circuit_blocked(state, day, config)
    assert not _daily_circuit_blocked(state, date(2026, 3, 25), config)
    _advance_daily_circuit(
        state, trade_date=date(2026, 3, 25), net_pnl=-100.0, equity=1e6, config=config
    )
    assert not _daily_circuit_blocked(state, date(2026, 3, 25), config)


def test_circuit_threshold_follows_current_equity() -> None:
    config = _circuit_config(daily_circuit_breaker_loss_streak=99)
    state = _DailyCircuitState()
    day = date(2026, 3, 24)
    _advance_daily_circuit(
        state, trade_date=day, net_pnl=-15_000.0, equity=1e6, config=config
    )
    assert not _daily_circuit_blocked(state, day, config)
    state_small = _DailyCircuitState()
    _advance_daily_circuit(
        state_small, trade_date=day, net_pnl=-15_000.0, equity=5e5, config=config
    )
    assert _daily_circuit_blocked(state_small, day, config)


def test_disabled_circuit_never_blocks() -> None:
    config = _circuit_config(daily_circuit_breaker_enabled=False)
    state = _DailyCircuitState()
    day = date(2026, 3, 24)
    for _ in range(5):
        _advance_daily_circuit(
            state, trade_date=day, net_pnl=-50_000.0, equity=1e6, config=config
        )
    assert not _daily_circuit_blocked(state, day, config)


# --------------------------------------------------------------------------
# 板块集中度
# --------------------------------------------------------------------------
def test_chemical_complex_is_one_sector() -> None:
    assert {sector_for_root(root) for root in ("MA", "EG", "L", "PP", "V")} == {
        "CHEMICAL"
    }
    assert sector_for_root("PG") == "ENERGY"
    assert sector_for_root("ZZZZ") == "UNCLASSIFIED"


def test_sector_limit_blocks_the_third_chemical_position() -> None:
    config = MultiTimeframeTrendConfig()
    sector_by_root = {r: sector_for_root(r) for r in ("MA", "EG", "L", "PP", "AG")}
    assert not _sector_exposure_blocked(
        "L", sector_by_root, {"MA": object()}, {}, config
    )
    detail = _sector_exposure_blocked(
        "L", sector_by_root, {"MA": object(), "EG": object()}, {}, config
    )
    assert "sector=CHEMICAL" in detail
    assert not _sector_exposure_blocked(
        "AG", sector_by_root, {"MA": object(), "EG": object()}, {}, config
    )


def test_sector_limit_counts_pending_orders_and_ignores_self() -> None:
    config = MultiTimeframeTrendConfig()
    sector_by_root = {r: sector_for_root(r) for r in ("MA", "EG", "L")}
    assert _sector_exposure_blocked(
        "L", sector_by_root, {"MA": object()}, {"EG": object()}, config
    )
    assert not _sector_exposure_blocked(
        "MA", sector_by_root, {"MA": object()}, {}, config
    )


# --------------------------------------------------------------------------
# 跨休市时点
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "clock,expected",
    [
        ("14:49", False),
        ("14:50", True),
        ("15:00", True),
        ("15:01", False),
        ("11:20", False),  # 午间休市不算长休市
        ("11:30", False),
        ("22:49", False),
        ("22:50", True),
        ("23:00", True),
    ],
)
def test_pre_break_window_covers_day_and_night_closes(
    clock: str, expected: bool
) -> None:
    assert (
        _is_pre_break_time(
            _at(f"2026-03-24 {clock}"), (DAY_SESSION, NIGHT_SESSION), 10
        )
        is expected
    )


@pytest.mark.parametrize("clock,expected", [("02:19", False), ("02:20", True), ("02:30", True)])
def test_pre_break_window_handles_a_night_close_after_midnight(
    clock: str, expected: bool
) -> None:
    assert (
        _is_pre_break_time(
            _at(f"2026-03-25 {clock}"), (DAY_SESSION, LATE_NIGHT_SESSION), 10
        )
        is expected
    )


# --------------------------------------------------------------------------
# 高跳空分级
# --------------------------------------------------------------------------
def _daily_frame(gaps: list[float], atr: float = 10.0) -> pd.DataFrame:
    closes = [100.0]
    opens = [100.0]
    for gap in gaps:
        opens.append(closes[-1] + gap)
        closes.append(opens[-1])
    return pd.DataFrame(
        {
            "exchange_trade_date": pd.date_range("2026-01-01", periods=len(closes)),
            "open": opens,
            "close": closes,
            "daily_atr14": [atr] * len(closes),
        }
    )


def test_high_gap_flag_is_causal_and_uses_prior_days_only() -> None:
    config = MultiTimeframeTrendConfig(
        high_gap_lookback_days=20, high_gap_quantile=0.9, high_gap_ratio_threshold=1.0
    )
    calm = _daily_frame([0.5] * 40)
    flags = _high_gap_flags_by_trade_date(calm, config)
    assert flags and not any(flags.values())

    wild = _daily_frame([15.0] * 40)
    wild_flags = _high_gap_flags_by_trade_date(wild, config)
    assert any(wild_flags.values())
    ordered = sorted(wild_flags)
    # 前若干天样本不足，不得被判为高跳空
    assert not wild_flags[ordered[0]]


def test_high_gap_flag_is_empty_without_daily_context() -> None:
    config = MultiTimeframeTrendConfig()
    assert _high_gap_flags_by_trade_date(None, config) == {}
    assert _high_gap_flags_by_trade_date(pd.DataFrame(), config) == {}


def test_high_gap_flag_rejects_nonempty_context_without_required_columns() -> None:
    config = MultiTimeframeTrendConfig()
    incomplete = pd.DataFrame(
        {
            "exchange_trade_date": [date(2026, 1, 5)],
            "open": [100.0],
            "close": [101.0],
        }
    )

    with pytest.raises(ValueError, match="daily_atr14"):
        _high_gap_flags_by_trade_date(incomplete, config)


# --------------------------------------------------------------------------
# 配置校验
# --------------------------------------------------------------------------
@pytest.mark.parametrize(
    "kwargs",
    [
        {"daily_circuit_breaker_loss_r": 0.0},
        {"daily_circuit_breaker_loss_streak": 0},
        {"daily_circuit_breaker_enabled": 1},
        {"pre_break_gap_risk_scale": 0.0},
        {"pre_break_gap_risk_scale": 1.5},
        {"pre_break_lead_minutes": 0},
        {"max_positions_per_sector": 0},
        {"high_gap_lookback_days": 0},
        {"high_gap_quantile": 1.0},
        {"high_gap_ratio_threshold": 0.0},
        {
            "pre_break_min_unrealized_r": 0.5,
            "pre_break_high_gap_min_unrealized_r": 0.1,
        },
    ],
)
def test_invalid_risk_control_config_is_rejected(kwargs: dict[str, object]) -> None:
    with pytest.raises(ValueError):
        MultiTimeframeTrendConfig(**kwargs)


# --------------------------------------------------------------------------
# 组合回放集成测试
# --------------------------------------------------------------------------
from datetime import date as _date  # noqa: E402

from cta.strategy.multi_timeframe_trend_backtest import engine as trend_engine  # noqa: E402
from cta.strategy.tests.test_multi_timeframe_trend_backtest import (  # noqa: E402
    _MetadataStore,
    _candidate,
    _day_sessions,
    _minutes,
)


_FREE_FEES = {
    "open_fee_rate": 0.0,
    "close_fee_rate": 0.0,
    "close_today_fee_rate": 0.0,
    "fee_per_lot_open": 0.0,
    "fee_per_lot_close": 0.0,
    "fee_per_lot_close_today": 0.0,
}


def _store() -> _MetadataStore:
    return _MetadataStore(
        contract_size=10.0,
        margin_rate=0.1,
        stressed_round_trip_fee_cash=0.0,
        price_tick=0.1,
        fees_by_date={_date(2026, 1, 5): dict(_FREE_FEES)},
    )


def _one_symbol_replay(
    *,
    bars: pd.DataFrame,
    config: MultiTimeframeTrendConfig,
    signal: str = "2026-01-05 14:26",
    active: str = "2026-01-05 14:27",
):
    inputs = (
        trend_engine.PortfolioReplayInput(
            root_symbol="AG",
            exchange="SHFE",
            minute_bars=bars,
            five_minute_context=pd.DataFrame(columns=["bar_end", "daily_direction"]),
            candidates=_candidate(
                signal=signal,
                active=active,
                expires="2026-01-05 14:45",
                setup="always_in",
                stop=99.0,
                candidate_id="AG-000001",
                symbol="AG",
            ),
            sessions=_day_sessions(),
        ),
    )
    return trend_engine.replay_trend_portfolio(
        inputs=inputs,
        metadata_store=_store(),
        config=config,
        start=_date(2026, 1, 5),
        end=_date(2026, 1, 5),
        initial_equity=1_000_000.0,
    )


def _single_symbol_replay(
    *,
    bars: pd.DataFrame,
    candidates: pd.DataFrame,
    config: MultiTimeframeTrendConfig,
):
    return trend_engine.replay_trend_strategy(
        root_symbol="AG",
        exchange="SHFE",
        minute_bars=bars,
        five_minute_context=pd.DataFrame(
            columns=["bar_end", "daily_direction"]
        ),
        candidates=candidates,
        metadata_store=_store(),
        config=config,
        start=_date(2026, 1, 5),
        end=_date(2026, 1, 5),
        initial_equity=1_000_000.0,
        sessions=_day_sessions(),
    )


def _bars_ending_at(last_close: float) -> pd.DataFrame:
    return _minutes(
        [
            ("2026-01-05 14:26", 99.5, 99.5, 99.5, 99.5, "AG2602.SHF"),
            ("2026-01-05 14:27", 100.0, 100.5, 99.9, 100.0, "AG2602.SHF"),
            ("2026-01-05 14:40", 100.0, 100.5, 99.9, 100.0, "AG2602.SHF"),
            (
                "2026-01-05 14:50",
                last_close,
                last_close + 0.1,
                last_close - 0.1,
                last_close,
                "AG2602.SHF",
            ),
            (
                "2026-01-05 14:51",
                last_close,
                last_close + 0.1,
                last_close - 0.1,
                last_close,
                "AG2602.SHF",
            ),
        ]
    )


_OPEN_WINDOWS = dict(
    entry_blocked_session_windows=(),
    daily_circuit_breaker_enabled=False,
    max_positions_per_sector=99,
    # 这些用例测的是跨休市保护与板块限额，止盈地板会抢先平仓，先关掉
    profit_floor_enabled=False,
)


def test_replay_flattens_a_position_that_has_no_buffer_before_the_close() -> None:
    artifacts = _one_symbol_replay(
        bars=_bars_ending_at(99.8),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=True,
            pre_break_min_unrealized_r=0.0,
            **_OPEN_WINDOWS,
        ),
    )
    assert artifacts.trades["exit_reason"].tolist() == ["PRE_BREAK_NO_BUFFER"]
    # 无缓冲仓位整笔平掉，不是部分减仓
    assert artifacts.exit_legs["quantity"].sum() == int(
        artifacts.trades["quantity"].iloc[0]
    )
    assert "PRE_BREAK_NO_BUFFER" in set(artifacts.rejections["reason_code"])


def test_single_replay_flattens_a_position_without_pre_break_buffer() -> None:
    artifacts = _single_symbol_replay(
        bars=_bars_ending_at(99.8),
        candidates=_candidate(
            signal="2026-01-05 14:26",
            active="2026-01-05 14:27",
            expires="2026-01-05 14:45",
            setup="always_in",
            stop=99.0,
            candidate_id="AG-000001",
            symbol="AG",
        ),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=True,
            pre_break_min_unrealized_r=0.0,
            **_OPEN_WINDOWS,
        ),
    )

    assert artifacts.trades["exit_reason"].tolist() == [
        "PRE_BREAK_NO_BUFFER"
    ]
    assert "PRE_BREAK_NO_BUFFER" in set(artifacts.rejections["reason_code"])


def test_single_replay_tracks_and_closes_chase_high_virtual_trade() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:00", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:01", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:02", 98.5, 99.0, 98.0, 98.5, "AG2602.SHF"),
        ]
    )
    candidate = _candidate(
        signal="2026-01-05 09:00",
        active="2026-01-05 09:01",
        expires="2026-01-05 09:05",
        setup="always_in",
        stop=99.0,
        candidate_id="AG-000001",
        symbol="AG",
    ).assign(
        filtered_reason="ENTRY_RANGE_POSITION_TOO_HIGH",
        chase_high_candidate=1,
    )

    artifacts = _single_symbol_replay(
        bars=bars,
        candidates=candidate,
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=False,
            **_OPEN_WINDOWS,
        ),
    )

    assert artifacts.trades.empty
    assert "CHASE_HIGH_VIRTUAL_CLOSED" in set(
        artifacts.rejections["reason_code"]
    )


def test_replay_keeps_a_position_that_already_has_buffer() -> None:
    artifacts = _one_symbol_replay(
        bars=_bars_ending_at(101.5),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=True,
            pre_break_min_unrealized_r=0.0,
            **_OPEN_WINDOWS,
        ),
    )
    assert "PRE_BREAK_NO_BUFFER" not in set(artifacts.trades["exit_reason"])


def test_one_lot_can_enter_under_scaling_then_is_fully_reduced() -> None:
    config = MultiTimeframeTrendConfig(
        drawdown_scale_factor=0.5,
        pre_break_gap_risk_scale=0.5,
    )
    quantity, *_ = _position_scaling_snapshot(
        base_quantity=1,
        symbol_state=_SymbolScalingState(),
        portfolio_state=_PortfolioScalingState(high_water=1_000_000.0),
        drawdown_state=_DrawdownScalingState(
            high_water=1_000_000.0,
            active=True,
        ),
        config=config,
    )
    assert quantity == 1

    position = SimpleNamespace(
        pending=SimpleNamespace(candidate={"direction": 1}),
        current_metadata=SimpleNamespace(contract_size=10.0),
        initial_risk_cash=10.0,
        entry_price=100.0,
        quantity=quantity,
    )
    decision = _pre_break_decision(
        position,
        mark=101.0,
        trade_date=date(2026, 1, 5),
        high_gap_by_date={date(2026, 1, 5): True},
        config=config,
    )

    assert decision.reason == "PRE_BREAK_GAP_RISK_REDUCTION"
    assert decision.close_quantity == 1


def test_replay_pre_break_protection_can_be_disabled() -> None:
    artifacts = _one_symbol_replay(
        bars=_bars_ending_at(99.8),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=False,
            **_OPEN_WINDOWS,
        ),
    )
    assert "PRE_BREAK_NO_BUFFER" not in set(artifacts.trades["exit_reason"])


def test_replay_blocks_entries_inside_the_default_afternoon_window() -> None:
    artifacts = _one_symbol_replay(
        bars=_bars_ending_at(101.5),
        config=MultiTimeframeTrendConfig(),
    )
    assert artifacts.trades.empty
    assert "ENTRY_SESSION_WINDOW_BLOCKED" in set(artifacts.rejections["reason_code"])


def test_replay_allows_the_same_entry_outside_the_window() -> None:
    bars = _minutes(
        [
            ("2026-01-05 10:26", 99.5, 99.5, 99.5, 99.5, "AG2602.SHF"),
            ("2026-01-05 10:27", 100.0, 100.5, 99.9, 100.0, "AG2602.SHF"),
            ("2026-01-05 10:40", 101.0, 101.5, 100.9, 101.0, "AG2602.SHF"),
        ]
    )
    artifacts = _one_symbol_replay(
        bars=bars,
        config=MultiTimeframeTrendConfig(),
        signal="2026-01-05 10:26",
        active="2026-01-05 10:27",
    )
    assert "ENTRY_SESSION_WINDOW_BLOCKED" not in set(
        artifacts.rejections["reason_code"]
    )
    assert not artifacts.fills.empty


def test_replay_sector_limit_blocks_the_third_chemical_symbol() -> None:
    roots = (("MA", "MA2602.ZCE"), ("EG", "EG2602.DCE"), ("L", "L2602.DCE"))
    inputs = []
    for symbol, contract in roots:
        bars = _minutes(
            [
                ("2026-01-05 10:26", 99.5, 99.5, 99.5, 99.5, contract),
                ("2026-01-05 10:27", 100.0, 100.5, 99.9, 100.0, contract),
                ("2026-01-05 10:40", 101.0, 101.5, 100.9, 101.0, contract),
            ]
        )
        inputs.append(
            trend_engine.PortfolioReplayInput(
                root_symbol=symbol,
                exchange="SHFE",
                minute_bars=bars,
                five_minute_context=pd.DataFrame(
                    columns=["bar_end", "daily_direction"]
                ),
                candidates=_candidate(
                    signal="2026-01-05 10:26",
                    active="2026-01-05 10:27",
                    expires="2026-01-05 10:45",
                    setup="always_in",
                    stop=99.0,
                    contract=contract,
                    candidate_id=f"{symbol}-000001",
                    symbol=symbol,
                ),
                sessions=_day_sessions(),
            )
        )
    artifacts = trend_engine.replay_trend_portfolio(
        inputs=tuple(inputs),
        metadata_store=_store(),
        config=MultiTimeframeTrendConfig(max_positions_per_sector=2),
        start=_date(2026, 1, 5),
        end=_date(2026, 1, 5),
        initial_equity=1_000_000.0,
    )
    assert "SECTOR_CONCENTRATION_LIMIT" in set(artifacts.rejections["reason_code"])
    assert len(artifacts.trades) <= 2


def test_replay_trade_rows_carry_the_real_sector() -> None:
    artifacts = _one_symbol_replay(
        bars=_bars_ending_at(101.5),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=False, **_OPEN_WINDOWS
        ),
    )
    assert set(artifacts.trades["sector"]) == {"PRECIOUS"}
