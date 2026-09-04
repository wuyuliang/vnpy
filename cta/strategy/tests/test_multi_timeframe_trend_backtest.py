from __future__ import annotations

from dataclasses import replace
from datetime import date, time
from pathlib import Path
from types import SimpleNamespace

import pandas as pd
import pytest

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.strategy.brooks.cycle_v1.instruments.metadata import BlockedMetadataError
from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
)
from cta.strategy.multi_timeframe_trend_backtest import runner as trend_runner
from cta.strategy.multi_timeframe_trend_backtest import engine as trend_engine
from cta.strategy.multi_timeframe_trend_backtest import charts as trend_charts
from cta.strategy.multi_timeframe_trend_backtest import report as trend_report
from cta.strategy.multi_timeframe_trend_backtest.charts import render_opportunity_charts
from cta.strategy.multi_timeframe_trend_backtest.engine import replay_trend_strategy
from cta.strategy.multi_timeframe_trend_backtest.report import publish_backtest_report
from cta.strategy.multi_timeframe_trend_backtest.runner import (
    _blocked_replay,
    _execution_candidates,
    _sort_aggregated_bars,
    build_parser,
    build_reproduction_command,
)


TZ = "Asia/Shanghai"


class _MetadataStore:
    def __init__(
        self,
        *,
        entry_slippage_ticks: float = 0.0,
        limit_down_by_date: dict[date, float] | None = None,
        fees_by_date: dict[date, dict[str, float]] | None = None,
        contract_size: float = 15.0,
        margin_rate: float = 0.10,
        stressed_round_trip_fee_cash: float = 10.0,
        price_tick: float = 1.0,
        margin_rate_by_date: dict[date, float] | None = None,
    ) -> None:
        self.entry_slippage_ticks = entry_slippage_ticks
        self.limit_down_by_date = limit_down_by_date or {}
        self.fees_by_date = fees_by_date or {}
        self.contract_size = contract_size
        self.margin_rate = margin_rate
        self.stressed_round_trip_fee_cash = stressed_round_trip_fee_cash
        self.price_tick = price_tick
        self.margin_rate_by_date = margin_rate_by_date or {}

    def execution_snapshot(self, **request: object) -> SimpleNamespace:
        trade_date = request["exchange_trade_date"]
        fee_timestamp = pd.Timestamp(
            f"{trade_date.isoformat()} 09:00", tz=TZ
        ).to_pydatetime()
        fees = {
            "open_fee_rate": 0.0,
            "close_fee_rate": 0.0,
            "close_today_fee_rate": 0.0,
            "fee_per_lot_open": 4.0,
            "fee_per_lot_close": 6.0,
            "fee_per_lot_close_today": 6.0,
            **self.fees_by_date.get(trade_date, {}),
        }
        return SimpleNamespace(
            contract_size=self.contract_size,
            price_tick=self.price_tick,
            limit_up=1_000.0,
            limit_down=self.limit_down_by_date.get(
                request["exchange_trade_date"], 1.0
            ),
            stressed_round_trip_fee_cash=self.stressed_round_trip_fee_cash,
            stressed_entry_slippage_ticks=self.entry_slippage_ticks,
            stressed_round_trip_slippage_ticks=2.0 * self.entry_slippage_ticks,
            metadata_hash=f"metadata-{trade_date}",
            fee_stress_multiplier=1.0,
            fee_source=f"OFFICIAL-{trade_date}",
            fee_effective_from=fee_timestamp,
            fee_known_at=fee_timestamp,
            **fees,
            daily=SimpleNamespace(
                margin_rate_long=self.margin_rate_by_date.get(
                    trade_date, self.margin_rate
                ),
                margin_rate_short=self.margin_rate_by_date.get(
                    trade_date, self.margin_rate
                ),
                exchange_trade_date=trade_date,
                fee_schedule_id=f"fees-{trade_date}",
            ),
        )


def _minutes(
    rows: list[tuple[str, float, float, float, float, str]],
) -> pd.DataFrame:
    frame = pd.DataFrame(
        rows,
        columns=["bar_end", "open", "high", "low", "close", "contract_code"],
    )
    frame["bar_end"] = pd.to_datetime(frame["bar_end"]).dt.tz_localize(TZ)
    frame["feature_sequence"] = range(len(frame))
    frame["volume"] = 100.0
    frame["open_interest"] = 1_000.0
    frame["exchange_trade_date"] = frame["bar_end"].dt.date
    return frame


def test_prepare_strategy_data_returns_signal_daily_context_for_replay(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    timestamp = pd.Timestamp("2026-01-05 15:00", tz=TZ)
    minute = pd.DataFrame(
        {
            "bar_end": [timestamp],
            "open": [100.0],
            "high": [102.0],
            "low": [99.0],
            "close": [101.0],
            "signal_open": [50.0],
            "signal_high": [51.0],
            "signal_low": [49.0],
            "signal_close": [50.5],
            "contract_code": ["BR2604.SHF"],
        }
    )
    daily_signal = pd.DataFrame(
        {
            "bar_end": [timestamp],
            "open": [50.0],
            "high": [51.0],
            "low": [49.0],
            "close": [50.5],
        }
    )
    replay_daily = daily_signal.assign(daily_atr14=2.0)
    monkeypatch.setattr(
        trend_runner,
        "_aggregate_trading_day_daily_bars",
        lambda bars, *, sessions, assignments=None: daily_signal.copy()
        if float(bars.iloc[0]["open"]) == 50.0
        else daily_signal.assign(open=100.0),
    )
    monkeypatch.setattr(
        trend_runner,
        "aggregate_completed_bars",
        lambda bars, *, minutes, sessions, assignments=None: daily_signal.copy(),
    )
    monkeypatch.setattr(trend_runner, "_attach_adjustment", lambda frame, minute: frame)
    monkeypatch.setattr(
        trend_runner,
        "_attach_signal_bar_instruments",
        lambda frame, **kwargs: frame,
    )
    monkeypatch.setattr(
        trend_runner,
        "_representative_instrument",
        lambda *args, **kwargs: object(),
    )
    monkeypatch.setattr(
        trend_runner,
        "generate_multi_timeframe_candidates",
        lambda *args, **kwargs: trend_runner._empty_candidates(),
    )
    monkeypatch.setattr(
        trend_runner,
        "_execution_candidates",
        lambda candidates, **kwargs: candidates,
    )
    monkeypatch.setattr(
        trend_runner,
        "_management_context",
        lambda **kwargs: pd.DataFrame(),
    )
    monkeypatch.setattr(
        trend_runner,
        "build_daily_context",
        lambda frame, config: replay_daily,
    )

    result = trend_runner._prepare_strategy_data(
        SimpleNamespace(minute_bars=minute, sessions=()),
        metadata_store=object(),
        config=MultiTimeframeTrendConfig(),
        start=date(2026, 1, 1),
        end=date(2026, 1, 31),
        initial_equity=1_000_000.0,
    )

    assert result[5] is replay_daily


def _logical_trade(
    *,
    direction: int = 1,
    symbol: str = "AG",
    contract_code: str = "AG2602.SHF",
    entry_time: str = "2026-01-05 09:05",
    exit_time: str = "2026-01-05 09:06",
    entry_price: float = 100.0,
    exit_price: float = 110.0,
) -> dict[str, object]:
    return {
        "symbol": symbol,
        "contract_code": contract_code,
        "direction": direction,
        "entry_time": pd.Timestamp(entry_time, tz=TZ),
        "exit_time": pd.Timestamp(exit_time, tz=TZ),
        "entry_price": entry_price,
        "exit_price": exit_price,
    }


def _return_bars(
    contract_code: str,
    *,
    step: float = 1.0,
    post_entry_count: int = 30,
) -> pd.DataFrame:
    morning = pd.date_range("2026-01-05 09:06", periods=4, freq="min", tz=TZ)
    afternoon = pd.date_range(
        "2026-01-05 13:31",
        periods=max(post_entry_count - len(morning), 0),
        freq="min",
        tz=TZ,
    )
    post_entry_times = morning[:post_entry_count].append(afternoon)
    return pd.DataFrame(
        {
            "bar_end": pd.DatetimeIndex(
                [pd.Timestamp("2026-01-05 09:05", tz=TZ), *post_entry_times]
            ),
            "contract_code": contract_code,
            "close": [1_000.0]
            + [100.0 + step * index for index in range(1, post_entry_count + 1)],
        }
    )


def _replay_return_bars(
    contract_code: str,
    *,
    step: float = 1.0,
) -> pd.DataFrame:
    rows = [
        ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, contract_code),
        ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, contract_code),
    ]
    entry_time = pd.Timestamp("2026-01-05 09:06")
    for index in range(1, 31):
        close = 100.0 + step * index
        rows.append(
            (
                (entry_time + pd.Timedelta(minutes=index)).strftime(
                    "%Y-%m-%d %H:%M"
                ),
                close,
                close + 1.0,
                close - 1.0,
                close,
                contract_code,
            )
        )
    return _minutes(rows)


def _candidate(
    *,
    signal: str = "2026-01-05 09:05",
    active: str = "2026-01-05 09:06",
    expires: str = "2026-01-05 09:20",
    setup: str = "pullback_breakout",
    trigger: float = 100.0,
    stop: float = 98.0,
    contract: str = "AG2602.SHF",
    candidate_id: str = "AG-000001",
    symbol: str = "AG",
    daily_bull_trend_id: int = 1,
    prior_5d_high: float = 90.0,
) -> pd.DataFrame:
    signal_time = pd.Timestamp(signal, tz=TZ)
    return pd.DataFrame(
        [
            {
                "candidate_id": candidate_id,
                "symbol": symbol,
                "exchange": "SHFE",
                "contract_code": contract,
                "signal_type": setup,
                "setup_type": setup,
                "direction": 1,
                "direction_value": 1,
                "signal_time": signal_time,
                "signal_datetime": signal_time,
                "active_time": pd.Timestamp(active, tz=TZ),
                "expires_at": pd.Timestamp(expires, tz=TZ),
                "trigger": trigger,
                "stop_price": stop,
                "target_price": float("nan"),
                "daily_bull_trend_id": daily_bull_trend_id,
                "prior_5d_high": prior_5d_high,
                "candidate_status": "not_triggered",
                "filtered_reason": "",
                "adjustment_scale": 1.0,
                "adjustment_offset": 0.0,
            }
        ]
    )


def _day_sessions(*, close: time = time(15)) -> tuple[SessionSpec, ...]:
    return (
        SessionSpec(
            session_id="day",
            is_night=False,
            segments=(SessionSegment("day", time(9), close, time(9)),),
        ),
    )


def _replay(
    minute_bars: pd.DataFrame,
    *,
    roll_execution_bars: pd.DataFrame | None = None,
    candidates: pd.DataFrame | None = None,
    five_minute_context: pd.DataFrame | None = None,
    metadata_store: _MetadataStore | None = None,
    config: MultiTimeframeTrendConfig | None = None,
    initial_equity: float = 1_000_000.0,
):
    return replay_trend_strategy(
        root_symbol="AG",
        exchange="SHFE",
        minute_bars=minute_bars,
        roll_execution_bars=roll_execution_bars,
        five_minute_context=(
            five_minute_context
            if five_minute_context is not None
            else pd.DataFrame(columns=["bar_end", "daily_direction"])
        ),
        candidates=candidates if candidates is not None else _candidate(),
        metadata_store=metadata_store or _MetadataStore(),
        config=config or MultiTimeframeTrendConfig(),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=initial_equity,
    )


@pytest.mark.parametrize(
    (
        "case",
        "direction",
        "trigger",
        "trend_id",
        "filled_trend_ids",
        "ratio",
        "expected",
    ),
    [
        ("disabled", 1, 100.0, 1, set(), 0.0, ""),
        (
            "strict_boundary",
            1,
            100.0 * (1.0 + 0.001),
            1,
            set(),
            0.001,
            "FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET",
        ),
        ("strict_breakout", 1, 100.100001, 1, set(), 0.001, ""),
        ("filled_trend", 1, 100.0, 1, {1}, 0.001, ""),
        (
            "new_trend",
            1,
            100.0,
            2,
            {1},
            0.001,
            "FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET",
        ),
        ("short_unchanged", -1, 100.0, 1, set(), 0.001, ""),
    ],
)
def test_first_trend_entry_breakout_buffer_reason(
    case: str,
    direction: int,
    trigger: float,
    trend_id: int,
    filled_trend_ids: set[int],
    ratio: float,
    expected: str,
) -> None:
    del case
    candidate = {
        "direction": direction,
        "trigger": trigger,
        "daily_bull_trend_id": trend_id,
        "prior_5d_high": 100.0,
    }
    config = MultiTimeframeTrendConfig(
        first_trend_entry_daily_breakout_buffer_ratio=ratio
    )

    assert trend_engine._first_trend_entry_breakout_buffer_reason(
        candidate,
        filled_trend_ids,
        config,
    ) == expected


def test_replay_zero_breakout_buffer_keeps_prior_high_equality_allowed() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    candidate = _candidate(prior_5d_high=100.0, trigger=100.0)

    artifacts = _replay(
        bars,
        candidates=candidate,
        metadata_store=_MetadataStore(entry_slippage_ticks=1.0),
        config=MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=0.0
        ),
    )

    assert artifacts.fills.loc[
        artifacts.fills["fill_kind"].eq("ENTRY"), "candidate_id"
    ].tolist() == ["AG-000001"]
    assert "FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET" not in set(
        artifacts.rejections["reason_code"]
    )
    trade = artifacts.trades.iloc[0]
    assert trade["is_first_trade_in_trend_segment"] == 1
    assert trade["entry_price"] != pytest.approx(candidate["trigger"].item())
    assert trade["trigger_to_prior_5d_high_ratio"] == pytest.approx(0.0)


def test_replay_breakout_buffer_rejects_strict_boundary_without_entry_fill() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    candidate = _candidate(
        prior_5d_high=100.0,
        trigger=100.0 * (1.0 + 0.001),
    )

    artifacts = _replay(
        bars,
        candidates=candidate,
        config=MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=0.001
        ),
    )

    assert artifacts.rejections["reason_code"].tolist() == [
        "FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET"
    ]
    assert artifacts.fills.loc[
        artifacts.fills["fill_kind"].eq("ENTRY")
    ].empty


def test_replay_zero_breakout_buffer_accepts_legacy_candidate_schema() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    candidate = _candidate().drop(
        columns=["daily_bull_trend_id", "prior_5d_high"]
    )

    artifacts = _replay(
        bars,
        candidates=candidate,
        config=MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=0.0
        ),
    )

    assert artifacts.fills.loc[
        artifacts.fills["fill_kind"].eq("ENTRY"), "candidate_id"
    ].tolist() == ["AG-000001"]
    trade = artifacts.trades.iloc[0]
    assert trade["is_first_trade_in_trend_segment"] == 0
    assert pd.isna(trade["trigger_to_prior_5d_high_ratio"])


@pytest.mark.parametrize("missing_column", ["daily_bull_trend_id", "prior_5d_high"])
def test_replay_positive_breakout_buffer_fails_closed_without_trend_context(
    missing_column: str,
) -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    candidate = _candidate().drop(columns=missing_column)

    artifacts = _replay(
        bars,
        candidates=candidate,
        config=MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=0.001
        ),
    )

    assert artifacts.rejections["reason_code"].tolist() == [
        "FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET"
    ]
    assert artifacts.fills.loc[
        artifacts.fills["fill_kind"].eq("ENTRY")
    ].empty


def test_replay_same_timestamp_candidate_observes_prior_entry_fill() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:00", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:01", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    candidates = pd.concat(
        [
            _candidate(
                signal="2026-01-05 09:00",
                active="2026-01-05 09:01",
                expires="2026-01-05 09:01",
                trigger=100.0,
                prior_5d_high=90.0,
                candidate_id="AG-000001",
            ),
            _candidate(
                signal="2026-01-05 09:01",
                active="2026-01-05 09:02",
                expires="2026-01-05 09:02",
                trigger=100.1,
                prior_5d_high=100.0,
                candidate_id="AG-000002",
            ),
        ],
        ignore_index=True,
    )

    artifacts = _replay(
        bars,
        candidates=candidates,
        config=MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=0.001
        ),
    )

    assert artifacts.fills.loc[
        artifacts.fills["fill_kind"].eq("ENTRY"), "candidate_id"
    ].tolist() == ["AG-000001"]
    assert artifacts.rejections.loc[
        artifacts.rejections["candidate_id"].eq("AG-000002"), "reason_code"
    ].tolist() == ["ROOT_EXPOSURE_ACTIVE"]


def test_replay_breakout_buffer_tracks_fills_and_restarts_for_new_trend() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:00", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:01", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:02", 97.0, 98.0, 97.0, 97.0, "AG2602.SHF"),
            ("2026-01-05 09:03", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:04", 97.0, 98.0, 97.0, 97.0, "AG2602.SHF"),
        ]
    )
    candidates = pd.concat(
        [
            _candidate(
                signal="2026-01-05 09:00",
                active="2026-01-05 09:01",
                expires="2026-01-05 09:01",
                trigger=100.100001,
                prior_5d_high=100.0,
                candidate_id="AG-000001",
            ),
            _candidate(
                signal="2026-01-05 09:02",
                active="2026-01-05 09:03",
                expires="2026-01-05 09:03",
                trigger=100.0,
                prior_5d_high=100.0,
                candidate_id="AG-000002",
            ),
            _candidate(
                signal="2026-01-05 09:04",
                active="2026-01-05 09:05",
                expires="2026-01-05 09:05",
                trigger=100.0,
                prior_5d_high=100.0,
                daily_bull_trend_id=2,
                candidate_id="AG-000003",
            ),
        ],
        ignore_index=True,
    )

    artifacts = _replay(
        bars,
        candidates=candidates,
        config=MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=0.001
        ),
    )

    assert artifacts.fills.loc[
        artifacts.fills["fill_kind"].eq("ENTRY"), "candidate_id"
    ].tolist() == ["AG-000001", "AG-000002"]
    trades = artifacts.trades.set_index("candidate_id")
    assert trades["is_first_trade_in_trend_segment"].tolist() == [1, 0]
    assert trades["trigger_to_prior_5d_high_ratio"].tolist() == pytest.approx(
        [0.100001, 0.0]
    )
    buffer_rejections = artifacts.rejections.loc[
        artifacts.rejections["reason_code"].eq(
            "FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET"
        ),
        "candidate_id",
    ]
    assert buffer_rejections.tolist() == ["AG-000003"]


def test_replay_marks_first_fill_again_after_bull_trend_segment_changes() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:00", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:01", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:02", 97.0, 98.0, 97.0, 97.0, "AG2602.SHF"),
            ("2026-01-05 09:03", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:04", 97.0, 98.0, 97.0, 97.0, "AG2602.SHF"),
            ("2026-01-05 09:05", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 97.0, 98.0, 97.0, 97.0, "AG2602.SHF"),
        ]
    )
    candidates = pd.concat(
        [
            _candidate(
                signal="2026-01-05 09:00",
                active="2026-01-05 09:01",
                expires="2026-01-05 09:01",
                candidate_id="AG-000001",
                daily_bull_trend_id=1,
            ),
            _candidate(
                signal="2026-01-05 09:02",
                active="2026-01-05 09:03",
                expires="2026-01-05 09:03",
                candidate_id="AG-000002",
                daily_bull_trend_id=1,
            ),
            _candidate(
                signal="2026-01-05 09:04",
                active="2026-01-05 09:05",
                expires="2026-01-05 09:05",
                candidate_id="AG-000003",
                daily_bull_trend_id=2,
            ),
        ],
        ignore_index=True,
    )

    artifacts = _replay(
        bars,
        candidates=candidates,
        config=MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=0.0
        ),
    )

    assert artifacts.trades["is_first_trade_in_trend_segment"].tolist() == [1, 0, 1]


def test_trade_trigger_ratio_is_nan_for_nonpositive_prior_high() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )

    artifacts = _replay(
        bars,
        candidates=_candidate(prior_5d_high=0.0),
        config=MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=0.0
        ),
    )

    assert pd.isna(artifacts.trades.iloc[0]["trigger_to_prior_5d_high_ratio"])


def test_replay_expired_candidate_does_not_disable_breakout_buffer() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:00", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:01", 100.0, 100.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:02", 100.0, 100.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:03", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    candidates = pd.concat(
        [
            _candidate(
                signal="2026-01-05 09:00",
                active="2026-01-05 09:01",
                expires="2026-01-05 09:01",
                trigger=101.0,
                prior_5d_high=100.0,
                candidate_id="AG-000001",
            ),
            _candidate(
                signal="2026-01-05 09:02",
                active="2026-01-05 09:03",
                expires="2026-01-05 09:03",
                trigger=100.100001,
                prior_5d_high=100.0,
                candidate_id="AG-000002",
            ),
        ],
        ignore_index=True,
    )

    artifacts = _replay(
        bars,
        candidates=candidates,
        config=MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=0.001
        ),
    )

    assert artifacts.orders.loc[
        artifacts.orders["candidate_id"].eq("AG-000001"), "status"
    ].tolist() == ["ACTIVE", "EXPIRED"]
    assert artifacts.fills.loc[
        artifacts.fills["fill_kind"].eq("ENTRY"), "candidate_id"
    ].tolist() == ["AG-000002"]
    assert artifacts.trades["is_first_trade_in_trend_segment"].tolist() == [1]


def test_portfolio_breakout_buffer_fill_state_is_isolated_per_root() -> None:
    ag_bars = _minutes(
        [
            ("2026-01-05 09:00", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:01", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    au_bars = _minutes(
        [
            ("2026-01-05 09:00", 99.0, 99.0, 99.0, 99.0, "AU2602.SHF"),
            ("2026-01-05 09:01", 100.0, 101.0, 100.0, 100.0, "AU2602.SHF"),
        ]
    )
    inputs = (
        trend_engine.PortfolioReplayInput(
            root_symbol="AG",
            exchange="SHFE",
            minute_bars=ag_bars,
            five_minute_context=pd.DataFrame(
                columns=["bar_end", "daily_direction"]
            ),
            candidates=_candidate(
                signal="2026-01-05 09:00",
                active="2026-01-05 09:01",
                expires="2026-01-05 09:01",
                trigger=100.0,
                prior_5d_high=90.0,
                candidate_id="AG-000001",
            ),
            sessions=_day_sessions(),
        ),
        trend_engine.PortfolioReplayInput(
            root_symbol="AU",
            exchange="SHFE",
            minute_bars=au_bars,
            five_minute_context=pd.DataFrame(
                columns=["bar_end", "daily_direction"]
            ),
            candidates=_candidate(
                signal="2026-01-05 09:01",
                active="2026-01-05 09:02",
                expires="2026-01-05 09:02",
                trigger=100.1,
                prior_5d_high=100.0,
                contract="AU2602.SHF",
                candidate_id="AU-000001",
                symbol="AU",
            ),
            sessions=_day_sessions(),
        ),
    )

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=inputs,
        metadata_store=_MetadataStore(),
        config=MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=0.001
        ),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=1_000_000.0,
    )

    assert artifacts.fills.loc[
        artifacts.fills["fill_kind"].eq("ENTRY"), "candidate_id"
    ].tolist() == ["AG-000001"]
    assert artifacts.rejections.loc[
        artifacts.rejections["candidate_id"].eq("AU-000001"), "reason_code"
    ].tolist() == ["FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET"]


def test_trade_return_columns_follow_gross_pnl() -> None:
    gross_index = trend_engine.TRADE_COLUMNS.index("gross_pnl")

    assert trend_engine.FORWARD_RETURN_HORIZONS == (5, 10, 20, 30)
    assert trend_engine.TRADE_RETURN_COLUMNS == (
        "total_return",
        "return_5min",
        "return_10min",
        "return_20min",
        "return_30min",
    )
    assert trend_engine.TRADE_COLUMNS[gross_index + 1:gross_index + 6] == (
        "total_return",
        "return_5min",
        "return_10min",
        "return_20min",
        "return_30min",
    )


def test_market_liquidity_columns_follow_forward_returns() -> None:
    return_index = trend_engine.TRADE_COLUMNS.index("return_30min")

    assert trend_engine.MARKET_LIQUIDITY_WINDOWS == (5, 10, 20)
    assert trend_engine.MARKET_LIQUIDITY_COLUMNS == (
        "prior_5d_avg_market_volume",
        "prior_5d_avg_market_turnover",
        "prior_10d_avg_market_volume",
        "prior_10d_avg_market_turnover",
        "prior_20d_avg_market_volume",
        "prior_20d_avg_market_turnover",
    )
    assert trend_engine.TRADE_COLUMNS[return_index + 1:return_index + 7] == (
        trend_engine.MARKET_LIQUIDITY_COLUMNS
    )


def test_market_liquidity_uses_complete_prior_days_across_contract_roll() -> None:
    trade_dates = pd.date_range("2026-01-01", periods=21, freq="D").date
    daily = pd.DataFrame(
        {
            "symbol": ["AG"] * 21,
            "exchange_trade_date": trade_dates,
            "contract_code": ["AG2602.SHF"] * 10 + ["AG2604.SHF"] * 11,
            "volume": list(range(1, 22)),
            "turnover": [value * 1_000.0 for value in range(1, 22)],
        }
    )
    trades = pd.DataFrame(
        [
            {
                "candidate_id": "AG-000001",
                "symbol": "AG",
            }
        ]
    )
    entry_trade_dates = pd.DataFrame(
        {
            "candidate_id": ["AG-000001"],
            "exchange_trade_date": [date(2026, 1, 21)],
        }
    )

    result = trend_report._with_market_liquidity(
        trades,
        daily_market_bars=daily,
        entry_trade_dates=entry_trade_dates,
    )

    assert result.loc[0, "prior_5d_avg_market_volume"] == pytest.approx(18.0)
    assert result.loc[0, "prior_10d_avg_market_volume"] == pytest.approx(15.5)
    assert result.loc[0, "prior_20d_avg_market_volume"] == pytest.approx(10.5)
    assert result.loc[0, "prior_5d_avg_market_turnover"] == pytest.approx(
        18_000.0
    )
    assert result.loc[0, "prior_10d_avg_market_turnover"] == pytest.approx(
        15_500.0
    )
    assert result.loc[0, "prior_20d_avg_market_turnover"] == pytest.approx(
        10_500.0
    )


def test_market_liquidity_requires_full_window() -> None:
    trade_dates = pd.date_range("2026-01-01", periods=19, freq="D").date
    daily = pd.DataFrame(
        {
            "symbol": ["AG"] * 19,
            "exchange_trade_date": trade_dates,
            "volume": list(range(1, 20)),
            "turnover": [value * 1_000.0 for value in range(1, 20)],
        }
    )
    trades = pd.DataFrame(
        [{"candidate_id": "AG-000001", "symbol": "AG"}]
    )
    entry_trade_dates = pd.DataFrame(
        {
            "candidate_id": ["AG-000001"],
            "exchange_trade_date": [date(2026, 1, 20)],
        }
    )

    result = trend_report._with_market_liquidity(
        trades,
        daily_market_bars=daily,
        entry_trade_dates=entry_trade_dates,
    )

    assert result.loc[0, "prior_5d_avg_market_volume"] == pytest.approx(17.0)
    assert result.loc[0, "prior_10d_avg_market_volume"] == pytest.approx(14.5)
    assert pd.isna(result.loc[0, "prior_20d_avg_market_volume"])
    assert pd.isna(result.loc[0, "prior_20d_avg_market_turnover"])


def test_entry_trade_dates_use_exchange_date_for_night_fill() -> None:
    entry_time = pd.Timestamp("2026-01-04 21:01", tz=TZ)
    trades = pd.DataFrame(
        [
            {
                "candidate_id": "AG-000001",
                "symbol": "AG",
                "entry_time": entry_time,
            }
        ]
    )
    minute_bars = pd.DataFrame(
        {
            "bar_end": [entry_time],
            "exchange_trade_date": [date(2026, 1, 5)],
        }
    )

    result = trend_runner._entry_trade_dates(
        trades,
        minute_bars_by_symbol={"AG": minute_bars},
    )

    assert result.to_dict("records") == [
        {
            "candidate_id": "AG-000001",
            "exchange_trade_date": date(2026, 1, 5),
        }
    ]


def test_symbol_performance_uses_realized_trade_curve_and_keeps_zero_trade_roots() -> None:
    trades = pd.DataFrame(
        {
            "candidate_id": ["AG-000003", "AG-000001", "AG-000002"],
            "symbol": ["AG", "AG", "AG"],
            "exit_time": pd.to_datetime(
                [
                    "2026-01-07 15:00",
                    "2026-01-05 15:00",
                    "2026-01-06 15:00",
                ]
            ).tz_localize(TZ),
            "gross_pnl": [-70.0, 110.0, -30.0],
            "net_pnl": [-80.0, 100.0, -40.0],
            "net_r": [-0.8, 1.0, -0.4],
            "fees": [5.0, 5.0, 5.0],
            "slippage": [1.0, 1.0, 1.0],
            "turnover": [3_000.0, 1_000.0, 2_000.0],
        }
    )

    result = trend_report._build_symbol_performance(
        trades,
        symbols=("AG", "AU"),
        initial_equity=1_000_000.0,
    ).set_index("symbol")

    assert tuple(result.reset_index().columns) == trend_report.SYMBOL_PERFORMANCE_COLUMNS
    ag = result.loc["AG"]
    assert ag["trade_count"] == 3
    assert ag["winning_trade_count"] == 1
    assert ag["losing_trade_count"] == 2
    assert ag["breakeven_trade_count"] == 0
    assert ag["win_rate_pct"] == pytest.approx(100.0 / 3.0)
    assert ag["total_gross_pnl"] == pytest.approx(10.0)
    assert ag["total_net_pnl"] == pytest.approx(-20.0)
    assert ag["total_return_pct"] == pytest.approx(-0.002)
    assert ag["max_drawdown_amount"] == pytest.approx(120.0)
    assert ag["max_drawdown_pct"] == pytest.approx(0.012)
    assert ag["profit_factor"] == pytest.approx(100.0 / 120.0)
    assert ag["average_win_loss_ratio"] == pytest.approx(100.0 / 60.0)
    assert ag["average_win"] == pytest.approx(100.0)
    assert ag["average_loss"] == pytest.approx(-60.0)
    assert ag["expectancy_R"] == pytest.approx(-0.2 / 3.0)
    assert ag["fees"] == pytest.approx(15.0)
    assert ag["slippage"] == pytest.approx(3.0)
    assert ag["turnover"] == pytest.approx(6_000.0)

    au = result.loc["AU"]
    assert au["trade_count"] == 0
    assert au["total_net_pnl"] == 0.0
    assert au["max_drawdown_amount"] == 0.0
    assert pd.isna(au["win_rate_pct"])
    assert pd.isna(au["profit_factor"])
    assert pd.isna(au["average_win_loss_ratio"])


def test_symbol_performance_drawdown_starts_from_zero() -> None:
    trades = pd.DataFrame(
        {
            "candidate_id": ["AG-000001", "AG-000002"],
            "symbol": ["AG", "AG"],
            "exit_time": pd.to_datetime(
                ["2026-01-05 15:00", "2026-01-06 15:00"]
            ).tz_localize(TZ),
            "gross_pnl": [-10.0, -20.0],
            "net_pnl": [-10.0, -20.0],
            "net_r": [-0.1, -0.2],
            "fees": [0.0, 0.0],
            "slippage": [0.0, 0.0],
            "turnover": [1_000.0, 1_000.0],
        }
    )

    result = trend_report._build_symbol_performance(
        trades,
        symbols=("AG",),
        initial_equity=100_000.0,
    ).iloc[0]

    assert result["max_drawdown_amount"] == pytest.approx(30.0)
    assert result["max_drawdown_pct"] == pytest.approx(0.03)


def test_trade_trend_audit_columns_follow_cycle() -> None:
    cycle_index = trend_engine.TRADE_COLUMNS.index("cycle")

    assert trend_engine.TRADE_COLUMNS[cycle_index + 1:cycle_index + 3] == (
        "is_first_trade_in_trend_segment",
        "trigger_to_prior_5d_high_ratio",
    )


def test_trade_returns_use_direction_and_actual_bars_after_entry() -> None:
    trades = pd.DataFrame(
        [
            _logical_trade(direction=1, contract_code="AG2602.SHF"),
            _logical_trade(
                direction=-1,
                contract_code="AG2604.SHF",
                exit_price=90.0,
            ),
            _logical_trade(
                direction=1,
                contract_code="AG2606.SHF",
                exit_price=90.0,
            ),
        ]
    )
    primary = pd.concat(
        [
            _return_bars("AG2602.SHF", step=1.0),
            _return_bars("AG2604.SHF", step=-1.0),
            _return_bars("AG2606.SHF", step=-1.0),
        ],
        ignore_index=True,
    )

    result = trend_engine._with_trade_returns(
        trades,
        observation_bars_by_symbol={
            "AG": trend_engine._observation_bars(primary, pd.DataFrame())
        },
    )

    assert "total_return" not in trades.columns
    assert result["total_return"].tolist() == pytest.approx([10.0, 10.0, -10.0])
    assert result["return_5min"].tolist() == pytest.approx([5.0, 5.0, -5.0])
    assert result["return_10min"].tolist() == pytest.approx([10.0, 10.0, -10.0])
    assert result["return_20min"].tolist() == pytest.approx([20.0, 20.0, -20.0])
    assert result["return_30min"].tolist() == pytest.approx([30.0, 30.0, -30.0])
    assert primary.loc[5, "bar_end"] == pd.Timestamp("2026-01-05 13:31", tz=TZ)


def test_trade_returns_continue_after_exit_and_leave_unavailable_values_nan() -> None:
    trades = pd.DataFrame(
        [
            _logical_trade(exit_time="2026-01-05 09:06"),
            _logical_trade(contract_code="AG2604.SHF"),
            _logical_trade(contract_code="AG9999.SHF"),
        ]
    )
    valid_bars = _return_bars("AG2602.SHF", post_entry_count=9)
    invalid_bars = _return_bars("AG2604.SHF", post_entry_count=5)
    invalid_bars.loc[invalid_bars.index[-1], "close"] = float("inf")

    result = trend_engine._with_trade_returns(
        trades,
        observation_bars_by_symbol={
            "AG": trend_engine._observation_bars(
                pd.concat([valid_bars, invalid_bars], ignore_index=True),
                pd.DataFrame(),
            )
        },
    )

    assert result.loc[0, "return_5min"] == pytest.approx(5.0)
    assert result.loc[0, ["return_10min", "return_20min", "return_30min"]].isna().all()
    assert pd.isna(result.loc[1, "return_5min"])
    assert result.loc[2, list(trend_engine.TRADE_RETURN_COLUMNS[1:])].isna().all()


@pytest.mark.parametrize(
    "invalid_close",
    [pd.NA, 0.0, -1.0],
    ids=["missing", "zero", "negative"],
)
def test_trade_returns_leave_invalid_observed_close_nan(
    invalid_close: object,
) -> None:
    bars = _return_bars("AG2602.SHF", post_entry_count=5)
    bars["close"] = bars["close"].astype(object)
    bars.loc[bars.index[-1], "close"] = invalid_close

    result = trend_engine._with_trade_returns(
        pd.DataFrame([_logical_trade()]),
        observation_bars_by_symbol={
            "AG": trend_engine._observation_bars(bars, pd.DataFrame())
        },
    )

    assert pd.isna(result.loc[0, "return_5min"])


def test_observation_bars_prefer_primary_and_fill_from_supplemental() -> None:
    first = pd.Timestamp("2026-01-05 09:06", tz=TZ)
    second = pd.Timestamp("2026-01-05 09:07", tz=TZ)
    primary = pd.DataFrame(
        {
            "bar_end": [first],
            "contract_code": ["AG2602.SHF"],
            "close": [101.0],
            "ignored": ["primary"],
        }
    )
    supplemental = pd.DataFrame(
        {
            "bar_end": [second, first],
            "contract_code": ["AG2602.SHF", "AG2602.SHF"],
            "close": [102.0, 999.0],
        }
    )

    result = trend_engine._observation_bars(primary, supplemental)

    assert result.columns.tolist() == ["bar_end", "contract_code", "close"]
    assert result["bar_end"].tolist() == [first, second]
    assert result["close"].tolist() == [101.0, 102.0]


def test_trade_returns_isolate_symbol_and_contract_and_use_default_symbol() -> None:
    trades = pd.DataFrame(
        [
            _logical_trade(symbol="AG", contract_code="SHARED"),
            _logical_trade(symbol="AU", contract_code="SHARED"),
            _logical_trade(symbol="AG", contract_code="AG-ONLY"),
            _logical_trade(symbol="AG", contract_code="MISSING"),
            _logical_trade(symbol="", contract_code="SHARED"),
            _logical_trade(symbol="", contract_code="MISSING"),
        ]
    )
    ag_bars = pd.concat(
        [
            _return_bars("SHARED", step=1.0),
            _return_bars("AG-ONLY", step=2.0),
        ],
        ignore_index=True,
    )
    au_bars = _return_bars("SHARED", step=3.0)

    result = trend_engine._with_trade_returns(
        trades,
        observation_bars_by_symbol={
            "AG": trend_engine._observation_bars(ag_bars, pd.DataFrame()),
            "AU": trend_engine._observation_bars(au_bars, pd.DataFrame()),
        },
        default_symbol="AU",
    )

    assert result["return_5min"].tolist()[:3] == pytest.approx([5.0, 15.0, 10.0])
    assert pd.isna(result.loc[3, "return_5min"])
    assert result.loc[4, "return_5min"] == pytest.approx(15.0)
    assert pd.isna(result.loc[5, "return_5min"])


def test_empty_trade_returns_have_complete_trade_columns() -> None:
    trades = pd.DataFrame(
        columns=[
            "symbol",
            "contract_code",
            "direction",
            "entry_time",
            "exit_time",
            "entry_price",
            "exit_price",
        ]
    )

    result = trend_engine._with_trade_returns(
        trades,
        observation_bars_by_symbol={},
    )

    assert result.empty
    assert tuple(result.columns) == trend_engine.TRADE_COLUMNS


def test_entry_does_not_fill_on_signal_bar() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 105.0, 99.0, 104.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 99.0, 99.0, 98.0, 98.5, "AG2602.SHF"),
        ]
    )

    artifacts = _replay(bars)

    assert artifacts.fills.empty
    assert artifacts.trades.empty
    assert tuple(artifacts.trades.columns) == trend_engine.TRADE_COLUMNS


def test_replay_trend_strategy_enriches_trade_returns_from_observed_bars() -> None:
    artifacts = _replay(
        _replay_return_bars("AG2602.SHF"),
        candidates=_candidate(symbol=""),
    )

    trade = artifacts.trades.iloc[0]
    assert trade["entry_time"] == pd.Timestamp("2026-01-05 09:06", tz=TZ)
    assert trade["entry_price"] == pytest.approx(100.0)
    assert trade["exit_price"] == pytest.approx(130.0)
    assert trade[list(trend_engine.TRADE_RETURN_COLUMNS)].tolist() == pytest.approx(
        [30.0, 5.0, 10.0, 20.0, 30.0]
    )
    gross_index = artifacts.trades.columns.get_loc("gross_pnl")
    assert artifacts.trades.columns[gross_index + 1:gross_index + 6].tolist() == list(
        trend_engine.TRADE_RETURN_COLUMNS
    )


def test_replay_trend_portfolio_isolates_returns_by_symbol_and_contract() -> None:
    empty_context = pd.DataFrame(columns=["bar_end", "daily_direction"])
    artifacts = trend_engine.replay_trend_portfolio(
        inputs=(
            trend_engine.PortfolioReplayInput(
                root_symbol="AG",
                exchange="SHFE",
                minute_bars=_replay_return_bars("AG2602.SHF", step=1.0),
                five_minute_context=empty_context,
                candidates=_candidate(
                    contract="AG2602.SHF",
                    candidate_id="AG-000001",
                    symbol="AG",
                ),
                sessions=_day_sessions(),
            ),
            trend_engine.PortfolioReplayInput(
                root_symbol="AU",
                exchange="SHFE",
                minute_bars=_replay_return_bars("AU2602.SHF", step=2.0),
                five_minute_context=empty_context,
                candidates=_candidate(
                    contract="AU2602.SHF",
                    candidate_id="AU-000001",
                    symbol="AU",
                ),
                sessions=_day_sessions(),
            ),
        ),
        metadata_store=_MetadataStore(),
        config=MultiTimeframeTrendConfig(max_concurrent_positions=2),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=1_000_000.0,
    )

    trades = artifacts.trades.set_index(["symbol", "contract_code"])
    assert trades.loc[("AG", "AG2602.SHF"), "return_5min"] == pytest.approx(5.0)
    assert trades.loc[("AU", "AU2602.SHF"), "return_5min"] == pytest.approx(10.0)
    assert trades["is_first_trade_in_trend_segment"].tolist() == [1, 1]


def test_pullback_two_r_is_reference_only_and_does_not_exit() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 101.0, 102.0, 100.0, 101.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 104.0, 107.0, 103.0, 106.0, "AG2602.SHF"),
        ]
    )

    artifacts = _replay(bars)

    trade = artifacts.trades.iloc[0]
    assert trade["entry_price"] == pytest.approx(101.0)
    assert trade["exit_price"] == pytest.approx(106.0)
    assert trade["final_target"] == pytest.approx(107.0)
    assert trade["target_exit_enabled"] == 0
    assert trade["exit_reason"] == "INTERVAL_END"


def test_pullback_stop_remains_executable_without_target_exit() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 100.0, 104.0, 98.0, 103.0, "AG2602.SHF"),
        ]
    )

    artifacts = _replay(bars)

    trade = artifacts.trades.iloc[0]
    assert trade["exit_price"] == pytest.approx(98.0)
    assert trade["exit_reason"] == "STOP"


def test_pullback_breakout_uses_confirmed_swing_trailing_stop() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 102.0, 103.0, 101.0, 102.0, "AG2602.SHF"),
            ("2026-01-05 09:08", 102.0, 103.0, 101.0, 101.0, "AG2602.SHF"),
        ]
    )
    context = pd.DataFrame(
        [
            {
                "bar_end": pd.Timestamp("2026-01-05 09:07", tz=TZ),
                "daily_direction": 1,
                "latest_swing_low": 102.0,
                "latest_swing_low_known_at": pd.Timestamp(
                    "2026-01-05 09:07", tz=TZ
                ),
                "atr14": 1.0,
            }
        ]
    )

    artifacts = _replay(bars, five_minute_context=context)

    trade = artifacts.trades.iloc[0]
    assert trade["exit_reason"] == "STOP"
    assert trade["exit_price"] == pytest.approx(101.0)
    assert trade["final_target"] == pytest.approx(104.0)
    assert trade["target_exit_enabled"] == 0


def test_symbol_scaling_state_requires_strict_recovery() -> None:
    config = MultiTimeframeTrendConfig()
    state = trend_engine._SymbolScalingState()

    assert not trend_engine._advance_symbol_scaling(state, -60.0, config).active
    assert state.consecutive_losses == 1
    assert not trend_engine._advance_symbol_scaling(state, 0.0, config).active
    assert state.consecutive_losses == 0

    trend_engine._advance_symbol_scaling(state, -60.0, config)
    trend_engine._advance_symbol_scaling(state, -40.0, config)
    assert state.active
    assert state.recovery_deficit == pytest.approx(100.0)

    trend_engine._advance_symbol_scaling(state, -25.0, config)
    assert state.recovery_deficit == pytest.approx(125.0)
    trend_engine._advance_symbol_scaling(state, 25.0, config)
    assert state.recovery_deficit == pytest.approx(100.0)
    trend_engine._advance_symbol_scaling(state, 100.0, config)
    assert state.active
    assert state.recovery_deficit == pytest.approx(0.0)
    trend_engine._advance_symbol_scaling(state, 0.01, config)
    assert not state.active
    assert state.recovery_deficit == pytest.approx(0.0)


def test_symbol_loss_cooldown_state_triggers_at_inclusive_pair_window() -> None:
    config = MultiTimeframeTrendConfig()
    state = trend_engine._SymbolLossCooldownState()
    first_exit = pd.Timestamp("2026-01-05 14:30", tz=TZ)
    second_exit = pd.Timestamp("2026-01-07 14:30", tz=TZ)

    trend_engine._advance_symbol_loss_cooldown(
        state,
        trade={"exit_time": first_exit, "net_pnl": -1.0},
        config=config,
    )
    assert state.last_loss_exit_time == first_exit
    assert state.cooldown_trigger_time is None

    trend_engine._advance_symbol_loss_cooldown(
        state,
        trade={"exit_time": second_exit, "net_pnl": -2.0},
        config=config,
    )
    assert state.cooldown_trigger_time == second_exit
    assert state.cooldown_release_time == pd.Timestamp(
        "2026-01-09 09:00", tz=TZ
    )

    exact_open = pd.Timestamp("2026-01-08 09:00", tz=TZ)
    assert trend_engine._next_day_open_after(exact_open) == exact_open


def test_symbol_loss_cooldown_state_converts_utc_to_market_time() -> None:
    config = MultiTimeframeTrendConfig()
    state = trend_engine._SymbolLossCooldownState()
    first_exit = pd.Timestamp("2026-01-05 06:30", tz="UTC")
    second_exit = pd.Timestamp("2026-01-07 06:30", tz="UTC")
    expected_trigger = pd.Timestamp("2026-01-07 14:30", tz=TZ)
    expected_release = pd.Timestamp("2026-01-09 09:00", tz=TZ)

    minimum = pd.Timestamp("2026-01-08 01:00", tz="UTC")
    next_open = trend_engine._next_day_open_after(minimum)
    assert next_open == pd.Timestamp("2026-01-08 09:00", tz=TZ)
    assert str(next_open.tz) == TZ

    for exit_time in (first_exit, second_exit):
        trend_engine._advance_symbol_loss_cooldown(
            state,
            trade={"exit_time": exit_time, "net_pnl": -1.0},
            config=config,
        )

    assert state.last_loss_exit_time == expected_trigger
    assert state.cooldown_trigger_time == expected_trigger
    assert state.cooldown_release_time == expected_release
    assert str(state.last_loss_exit_time.tz) == TZ
    assert str(state.cooldown_trigger_time.tz) == TZ
    assert str(state.cooldown_release_time.tz) == TZ

    detail = trend_engine._symbol_loss_cooldown_detail(
        state,
        pd.Timestamp("2026-01-09 00:59", tz="UTC"),
        config,
    )
    assert "2026-01-07T14:30:00+08:00" in detail
    assert "2026-01-09T09:00:00+08:00" in detail
    assert not trend_engine._symbol_loss_cooldown_detail(
        state,
        pd.Timestamp("2026-01-09 01:00", tz="UTC"),
        config,
    )


@pytest.mark.parametrize("entry_point", ["next_open", "advance", "detail"])
def test_symbol_loss_cooldown_state_rejects_naive_timestamp(
    entry_point: str,
) -> None:
    config = MultiTimeframeTrendConfig()
    naive = pd.Timestamp("2026-01-08 09:00")
    state = trend_engine._SymbolLossCooldownState(
        last_loss_exit_time=pd.Timestamp("2026-01-07 14:30", tz=TZ),
        cooldown_trigger_time=pd.Timestamp("2026-01-07 14:30", tz=TZ),
        cooldown_release_time=pd.Timestamp("2026-01-09 09:00", tz=TZ),
    )

    with pytest.raises(ValueError, match="timezone-aware"):
        if entry_point == "next_open":
            trend_engine._next_day_open_after(naive)
        elif entry_point == "advance":
            trend_engine._advance_symbol_loss_cooldown(
                state,
                trade={"exit_time": naive, "net_pnl": -1.0},
                config=config,
            )
        else:
            trend_engine._symbol_loss_cooldown_detail(state, naive, config)


def test_symbol_loss_cooldown_state_detail_blocks_until_release() -> None:
    config = MultiTimeframeTrendConfig()
    trigger = pd.Timestamp("2026-01-07 14:30", tz=TZ)
    release = pd.Timestamp("2026-01-09 09:00", tz=TZ)
    state = trend_engine._SymbolLossCooldownState(
        last_loss_exit_time=trigger,
        cooldown_trigger_time=trigger,
        cooldown_release_time=release,
    )

    detail = trend_engine._symbol_loss_cooldown_detail(
        state,
        release - pd.Timedelta(nanoseconds=1),
        config,
    )
    assert detail
    assert "second_loss_exit" in detail
    assert "release_at" in detail
    assert not trend_engine._symbol_loss_cooldown_detail(
        state,
        release,
        config,
    )


def test_symbol_loss_cooldown_state_retains_loss_after_pair_window() -> None:
    config = MultiTimeframeTrendConfig()
    state = trend_engine._SymbolLossCooldownState()
    first_exit = pd.Timestamp("2026-01-05 14:30", tz=TZ)
    second_exit = pd.Timestamp("2026-01-07 14:31", tz=TZ)
    third_exit = pd.Timestamp("2026-01-09 14:31", tz=TZ)

    for exit_time in (first_exit, second_exit):
        trend_engine._advance_symbol_loss_cooldown(
            state,
            trade={"exit_time": exit_time, "net_pnl": -1.0},
            config=config,
        )

    assert state.last_loss_exit_time == second_exit
    assert state.cooldown_trigger_time is None
    assert state.cooldown_release_time is None

    trend_engine._advance_symbol_loss_cooldown(
        state,
        trade={"exit_time": third_exit, "net_pnl": -1.0},
        config=config,
    )
    assert state.cooldown_trigger_time == third_exit


@pytest.mark.parametrize("net_pnl", [0.0, 1.0])
def test_symbol_loss_cooldown_state_non_loss_clears_state(net_pnl: float) -> None:
    config = MultiTimeframeTrendConfig()
    previous_exit = pd.Timestamp("2026-01-07 14:30", tz=TZ)
    state = trend_engine._SymbolLossCooldownState(
        last_loss_exit_time=previous_exit,
        cooldown_trigger_time=previous_exit,
        cooldown_release_time=pd.Timestamp("2026-01-09 09:00", tz=TZ),
    )

    trend_engine._advance_symbol_loss_cooldown(
        state,
        trade={
            "exit_time": pd.Timestamp("2026-01-08 10:00", tz=TZ),
            "net_pnl": net_pnl,
        },
        config=config,
    )

    assert state.last_loss_exit_time is None
    assert state.cooldown_trigger_time is None
    assert state.cooldown_release_time is None


@pytest.mark.parametrize("net_pnl", [-1.0, 0.0, 1.0])
def test_symbol_loss_cooldown_state_disabled_is_noop(net_pnl: float) -> None:
    config = MultiTimeframeTrendConfig(symbol_loss_cooldown_enabled=False)
    previous_exit = pd.Timestamp("2026-01-07 14:30", tz=TZ)
    release = pd.Timestamp("2026-01-09 09:00", tz=TZ)
    state = trend_engine._SymbolLossCooldownState(
        last_loss_exit_time=previous_exit,
        cooldown_trigger_time=previous_exit,
        cooldown_release_time=release,
    )

    trend_engine._advance_symbol_loss_cooldown(
        state,
        trade={
            "exit_time": pd.Timestamp("2026-01-08 10:00", tz=TZ),
            "net_pnl": net_pnl,
        },
        config=config,
    )

    assert state.last_loss_exit_time == previous_exit
    assert state.cooldown_trigger_time == previous_exit
    assert state.cooldown_release_time == release
    assert not trend_engine._symbol_loss_cooldown_detail(
        state,
        pd.Timestamp("2026-01-08 10:00", tz=TZ),
        config,
    )


def test_portfolio_scaling_state_uses_realized_cash_high_water() -> None:
    config = MultiTimeframeTrendConfig(portfolio_drawdown_threshold=0.01)
    exact = trend_engine._PortfolioScalingState(high_water=100_000.0)

    trend_engine._advance_portfolio_scaling(
        exact,
        cash=99_000.0,
        net_pnl=-1_000.0,
        config=config,
    )
    assert not exact.active
    assert exact.high_water == pytest.approx(100_000.0)

    state = trend_engine._PortfolioScalingState(high_water=100_000.0)
    trend_engine._advance_portfolio_scaling(
        state,
        cash=98_999.0,
        net_pnl=-1_001.0,
        config=config,
    )
    assert state.active
    assert state.recovery_deficit == pytest.approx(1_001.0)

    trend_engine._advance_portfolio_scaling(
        state,
        cash=100_000.0,
        net_pnl=1_001.0,
        config=config,
    )
    assert state.active
    assert state.recovery_deficit == pytest.approx(0.0)
    trend_engine._advance_portfolio_scaling(
        state,
        cash=100_000.01,
        net_pnl=0.01,
        config=config,
    )
    assert not state.active
    assert state.high_water == pytest.approx(100_000.01)


def test_replay_scales_entry_when_symbol_and_portfolio_states_are_active() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 98.0, 99.0, 98.0, 98.0, "AG2602.SHF"),
            ("2026-01-05 09:08", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:09", 98.0, 99.0, 98.0, 98.0, "AG2602.SHF"),
            ("2026-01-05 09:10", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    candidates = pd.concat(
        [
            _candidate(candidate_id="AG-000001"),
            _candidate(
                signal="2026-01-05 09:07",
                active="2026-01-05 09:08",
                candidate_id="AG-000002",
            ),
            _candidate(
                signal="2026-01-05 09:09",
                active="2026-01-05 09:10",
                candidate_id="AG-000003",
            ),
        ],
        ignore_index=True,
    )

    artifacts = _replay(
        bars,
        candidates=candidates,
        config=MultiTimeframeTrendConfig(
            symbol_position_scale=0.5,
            portfolio_position_scale=0.5,
        ),
    )

    scaled = artifacts.trades.set_index("candidate_id").loc["AG-000003"]
    assert scaled["base_quantity"] == 245
    assert scaled["symbol_quantity_scale"] == pytest.approx(0.5)
    assert scaled["portfolio_quantity_scale"] == pytest.approx(0.5)
    assert scaled["quantity_scale"] == pytest.approx(0.25)
    assert scaled["quantity"] == 61
    triggers = artifacts.position_scaling_events.query(
        "event_type == 'TRIGGER'"
    )
    # 新增了 DRAWDOWN 这个缩放来源，这里只断言原有两个来源仍在
    triggers = artifacts.position_scaling_events.query(
        "event_type == 'TRIGGER' and scope in ['SYMBOL', 'PORTFOLIO']"
    )
    assert set(triggers["scope"]) == {"SYMBOL", "PORTFOLIO"}
    assert set(triggers["candidate_id"]) == {"AG-000002"}


def test_portfolio_exit_updates_scaling_before_same_timestamp_entry() -> None:
    ag_bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 98.0, 99.0, 98.0, 98.0, "AG2602.SHF"),
            ("2026-01-05 09:08", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:09", 98.0, 99.0, 98.0, 98.0, "AG2602.SHF"),
        ]
    )
    au_bars = _minutes(
        [
            ("2026-01-05 09:08", 99.0, 99.0, 99.0, 99.0, "AU2602.SHF"),
            ("2026-01-05 09:09", 100.0, 101.0, 100.0, 100.0, "AU2602.SHF"),
            ("2026-01-05 09:10", 100.0, 101.0, 100.0, 100.0, "AU2602.SHF"),
        ]
    )
    ag_candidates = pd.concat(
        [
            _candidate(candidate_id="AG-000001"),
            _candidate(
                signal="2026-01-05 09:07",
                active="2026-01-05 09:08",
                candidate_id="AG-000002",
            ),
        ],
        ignore_index=True,
    )
    au_candidate = _candidate(
        signal="2026-01-05 09:08",
        active="2026-01-05 09:09",
        contract="AU2602.SHF",
        candidate_id="AU-000001",
        symbol="AU",
    )
    empty_context = pd.DataFrame(columns=["bar_end", "daily_direction"])

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=(
            trend_engine.PortfolioReplayInput(
                root_symbol="AG",
                exchange="SHFE",
                minute_bars=ag_bars,
                five_minute_context=empty_context,
                candidates=ag_candidates,
                sessions=_day_sessions(),
            ),
            trend_engine.PortfolioReplayInput(
                root_symbol="AU",
                exchange="SHFE",
                minute_bars=au_bars,
                five_minute_context=empty_context,
                candidates=au_candidate,
                sessions=_day_sessions(),
            ),
        ),
        metadata_store=_MetadataStore(),
        config=MultiTimeframeTrendConfig(
            symbol_position_scale=0.5,
            portfolio_position_scale=0.5,
        ),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=1_000_000.0,
    )

    au_trade = artifacts.trades.set_index("candidate_id").loc["AU-000001"]
    assert au_trade["base_quantity"] == 245
    assert au_trade["symbol_quantity_scale"] == pytest.approx(1.0)
    assert au_trade["portfolio_quantity_scale"] == pytest.approx(0.5)
    assert au_trade["quantity"] == 122


def test_replay_rejects_dynamic_scale_below_one_lot() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 98.0, 99.0, 98.0, 98.0, "AG2602.SHF"),
            ("2026-01-05 09:08", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:09", 98.0, 99.0, 98.0, 98.0, "AG2602.SHF"),
            ("2026-01-05 09:10", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    candidates = pd.concat(
        [
            _candidate(candidate_id="AG-000001"),
            _candidate(
                signal="2026-01-05 09:07",
                active="2026-01-05 09:08",
                candidate_id="AG-000002",
            ),
            _candidate(
                signal="2026-01-05 09:09",
                active="2026-01-05 09:10",
                candidate_id="AG-000003",
            ),
        ],
        ignore_index=True,
    )
    config = MultiTimeframeTrendConfig(
        symbol_position_scale=0.1,
        portfolio_position_scale=0.1,
        # 本用例测的正是"缩放到不足一手就拒单"的旧行为
        position_scale_min_one_lot=False,
    )

    artifacts = _replay(
        bars,
        candidates=candidates,
        config=config,
        initial_equity=100_000.0,
    )

    assert "DYNAMIC_RISK_SCALE_BELOW_ONE_LOT" in set(
        artifacts.rejections["reason_code"]
    )
    cancelled = artifacts.orders.loc[
        artifacts.orders["candidate_id"] == "AG-000003"
    ].iloc[-1]
    assert cancelled["reason"] == "DYNAMIC_RISK_SCALE_BELOW_ONE_LOT"


def test_open_position_closes_on_old_contract_bar_after_contract_switch() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 110.0, 111.0, 109.0, 110.0, "AG2604.SHF"),
            ("2026-01-05 09:08", 111.0, 112.0, 110.0, 111.0, "AG2604.SHF"),
        ]
    )
    roll_bars = _minutes(
        [
            ("2026-01-05 09:08", 103.0, 104.0, 97.0, 103.0, "AG2602.SHF"),
        ]
    )

    artifacts = _replay(bars, roll_execution_bars=roll_bars)

    trade = artifacts.trades.iloc[0]
    assert trade["contract_code"] == "AG2602.SHF"
    assert trade["exit_time"] == pd.Timestamp("2026-01-05 09:08", tz=TZ)
    assert trade["exit_price"] == pytest.approx(103.0)
    assert trade["exit_reason"] == "ROLL_MAPPING_CHANGED"


def test_open_position_blocks_official_replay_when_roll_bar_is_missing() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 101.0, 102.0, 100.0, 101.0, "AG2604.SHF"),
        ]
    )

    with pytest.raises(BlockedMetadataError, match="BLOCKED_ROLL_EXECUTION_BAR"):
        _replay(bars)


def test_portfolio_closes_old_contract_position_with_roll_execution_bar() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 110.0, 111.0, 109.0, 110.0, "AG2604.SHF"),
            ("2026-01-05 09:08", 111.0, 112.0, 110.0, 111.0, "AG2604.SHF"),
        ]
    )
    roll_bars = _minutes(
        [
            ("2026-01-05 09:08", 103.0, 104.0, 97.0, 103.0, "AG2602.SHF"),
        ]
    )

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=(
            trend_engine.PortfolioReplayInput(
                root_symbol="AG",
                exchange="SHFE",
                minute_bars=bars,
                roll_execution_bars=roll_bars,
                five_minute_context=pd.DataFrame(
                    columns=["bar_end", "daily_direction"]
                ),
                candidates=_candidate(),
                sessions=_day_sessions(),
            ),
        ),
        metadata_store=_MetadataStore(),
        config=MultiTimeframeTrendConfig(),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=1_000_000.0,
    )

    trade = artifacts.trades.iloc[0]
    assert trade["contract_code"] == "AG2602.SHF"
    assert trade["exit_price"] == pytest.approx(103.0)
    assert trade["exit_reason"] == "ROLL_MAPPING_CHANGED"


def test_load_roll_execution_bars_selects_first_old_bar_after_switch(
    tmp_path: Path,
) -> None:
    active_bars = _minutes(
        [
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 110.0, 111.0, 109.0, 110.0, "AG2604.SHF"),
            ("2026-01-05 09:08", 111.0, 112.0, 110.0, 111.0, "AG2604.SHF"),
        ]
    )
    explicit = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                [
                    "2026-01-05 09:06",
                    "2026-01-05 09:07",
                    "2026-01-05 09:08",
                ]
            ),
            "open": [101.0, 999.0, 103.0],
            "high": [102.0, 1_000.0, 104.0],
            "low": [100.0, 998.0, 102.0],
            "close": [101.0, 999.0, 103.0],
            "volume": [100.0, 90.0, 80.0],
            "open_interest": [1_000.0, 950.0, 900.0],
            "turnover": [1_000.0, 950.0, 900.0],
            "ts_code": ["AG2602.SHF", "AG2602.SHF", "AG2602.SHF"],
        }
    )
    path = (
        tmp_path / "contract" / "AG" / "minute" / "AG2602_SHF.parquet"
    )
    path.parent.mkdir(parents=True)
    explicit.to_parquet(path, index=False)
    loaded = SimpleNamespace(root_symbol="AG", minute_bars=active_bars)

    result = trend_runner._load_roll_execution_bars(
        loaded,
        data_root=tmp_path / "minute",
    )

    assert len(result) == 1
    row = result.iloc[0]
    assert row["contract_code"] == "AG2602.SHF"
    assert row["bar_end"] == pd.Timestamp("2026-01-05 09:08", tz=TZ)
    assert row["open"] == pytest.approx(103.0)
    assert row["exchange_trade_date"] == date(2026, 1, 5)


def test_representative_instrument_allows_historical_tick_changes() -> None:
    bars = _minutes(
        [
            ("2026-04-09 09:05", 100.0, 100.0, 100.0, 100.0, "P2605.DCE"),
            ("2026-04-09 09:06", 101.0, 101.0, 101.0, 101.0, "P2605.DCE"),
        ]
    )
    loaded = SimpleNamespace(
        root_symbol="P",
        exchange="DCE",
        minute_bars=bars,
    )

    class _HistoricalMechanicsStore:
        instruments = [
            SimpleNamespace(
                root_symbol="P",
                exchange="DCE",
                price_tick=2.0,
                contract_size=10.0,
            ),
            SimpleNamespace(
                root_symbol="P",
                exchange="DCE",
                price_tick=1.0,
                contract_size=10.0,
            ),
        ]

        def execution_snapshot(self, **request: object) -> SimpleNamespace:
            del request
            return SimpleNamespace(
                price_tick=2.0,
                contract_size=10.0,
                stressed_round_trip_fee_cash=5.0,
                stressed_round_trip_slippage_ticks=2.0,
                instrument=SimpleNamespace(
                    known_at=pd.Timestamp("2025-09-15 15:30", tz=TZ)
                ),
            )

    instrument = trend_runner._representative_instrument(
        loaded,
        metadata_store=_HistoricalMechanicsStore(),
        initial_equity=1_000_000.0,
    )

    assert instrument.contract == "P2605.DCE"
    assert instrument.tick_size == pytest.approx(2.0)
    assert instrument.multiplier == pytest.approx(10.0)
    assert instrument.stressed_round_trip_cost == pytest.approx(45.0)


def test_net_r_includes_stressed_round_trip_slippage() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 102.0, 100.0, 101.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 107.0, 107.0, 106.0, 107.0, "AG2602.SHF"),
        ]
    )

    artifacts = _replay(
        bars,
        metadata_store=_MetadataStore(entry_slippage_ticks=1.0),
    )

    assert artifacts.trades.iloc[0]["net_r"] == pytest.approx(65.0 / 85.0)


def test_same_trade_date_exit_uses_close_today_fee() -> None:
    trade_date = date(2026, 1, 5)
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 104.0, 104.0, 103.0, 104.0, "AG2602.SHF"),
        ]
    )
    store = _MetadataStore(
        fees_by_date={
            trade_date: {
                "open_fee_rate": 0.001,
                "close_fee_rate": 0.002,
                "close_today_fee_rate": 0.003,
                "fee_per_lot_open": 0.0,
                "fee_per_lot_close": 0.0,
                "fee_per_lot_close_today": 0.0,
            }
        }
    )

    trade = _replay(bars, metadata_store=store).trades.iloc[0]

    assert trade["open_fee"] == pytest.approx(
        trade["quantity"] * 100.0 * 15.0 * 0.001
    )
    assert trade["close_fee"] == pytest.approx(
        trade["quantity"] * 104.0 * 15.0 * 0.003
    )
    assert trade["fees"] == pytest.approx(trade["open_fee"] + trade["close_fee"])
    assert trade["close_type"] == "CLOSE_TODAY"


def test_cross_trade_date_exit_uses_exit_day_close_fee() -> None:
    entry_date = date(2026, 1, 5)
    exit_date = date(2026, 1, 6)
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-06 09:01", 98.0, 99.0, 98.0, 98.0, "AG2602.SHF"),
        ]
    )
    store = _MetadataStore(
        fees_by_date={
            entry_date: {
                "open_fee_rate": 0.001,
                "close_fee_rate": 0.002,
                "close_today_fee_rate": 0.003,
                "fee_per_lot_open": 0.0,
                "fee_per_lot_close": 0.0,
                "fee_per_lot_close_today": 0.0,
            },
            exit_date: {
                "open_fee_rate": 0.007,
                "close_fee_rate": 0.004,
                "close_today_fee_rate": 0.009,
                "fee_per_lot_open": 0.0,
                "fee_per_lot_close": 0.0,
                "fee_per_lot_close_today": 0.0,
            },
        }
    )

    artifacts = replay_trend_strategy(
        root_symbol="AG",
        exchange="SHFE",
        minute_bars=bars,
        five_minute_context=pd.DataFrame(columns=["bar_end", "daily_direction"]),
        candidates=_candidate(setup="always_in"),
        metadata_store=store,
        config=MultiTimeframeTrendConfig(),
        start=entry_date,
        end=exit_date,
        initial_equity=1_000_000.0,
    )
    trade = artifacts.trades.iloc[0]

    assert trade["open_fee"] == pytest.approx(
        trade["quantity"] * 100.0 * 15.0 * 0.001
    )
    assert trade["close_fee"] == pytest.approx(
        trade["quantity"] * 98.0 * 15.0 * 0.004
    )
    assert trade["fees"] == pytest.approx(trade["open_fee"] + trade["close_fee"])
    assert trade["close_type"] == "CLOSE"
    assert trade["exit_fee_schedule_id"] == f"fees-{exit_date}"
    assert trade["final_target"] == pytest.approx(104.0)
    assert trade["target_exit_enabled"] == 0


def test_portfolio_replay_shares_symbol_and_total_margin_limits() -> None:
    ag_bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 100.1, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    au_bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AU2602.SHF"),
            ("2026-01-05 09:06", 100.0, 100.1, 100.0, 100.0, "AU2602.SHF"),
        ]
    )
    store = _MetadataStore(
        contract_size=10.0,
        margin_rate=1.0,
        stressed_round_trip_fee_cash=0.0,
        price_tick=0.1,
    )
    inputs = (
        trend_engine.PortfolioReplayInput(
            root_symbol="AG",
            exchange="SHFE",
            minute_bars=ag_bars,
            five_minute_context=pd.DataFrame(
                columns=["bar_end", "daily_direction"]
            ),
            candidates=_candidate(
                setup="always_in",
                stop=99.9,
                contract="AG2602.SHF",
                candidate_id="AG-000001",
                symbol="AG",
            ),
            sessions=_day_sessions(),
        ),
        trend_engine.PortfolioReplayInput(
            root_symbol="AU",
            exchange="SHFE",
            minute_bars=au_bars,
            five_minute_context=pd.DataFrame(
                columns=["bar_end", "daily_direction"]
            ),
            candidates=_candidate(
                setup="always_in",
                stop=99.9,
                contract="AU2602.SHF",
                candidate_id="AU-000001",
                symbol="AU",
            ),
            sessions=_day_sessions(),
        ),
    )

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=inputs,
        metadata_store=store,
        config=MultiTimeframeTrendConfig(),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=100_000.0,
    )

    quantities = artifacts.trades.set_index("symbol")["quantity"].to_dict()
    assert quantities == {"AG": 40, "AU": 20}
    assert artifacts.daily_equity["peak_margin_utilization"].max() == pytest.approx(
        0.60
    )
    assert artifacts.daily_equity["peak_symbol_margin_utilization"].max() == (
        pytest.approx(0.40)
    )
    assert artifacts.daily_equity["max_concurrent_positions"].max() == 2


def test_portfolio_replay_enforces_max_concurrent_positions() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    second = bars.assign(contract_code="AU2602.SHF")
    inputs = (
        trend_engine.PortfolioReplayInput(
            "AG",
            "SHFE",
            bars,
            pd.DataFrame(columns=["bar_end", "daily_direction"]),
            _candidate(setup="always_in"),
            _day_sessions(),
        ),
        trend_engine.PortfolioReplayInput(
            "AU",
            "SHFE",
            second,
            pd.DataFrame(columns=["bar_end", "daily_direction"]),
            _candidate(
                setup="always_in",
                contract="AU2602.SHF",
                candidate_id="AU-000001",
                symbol="AU",
            ),
            _day_sessions(),
        ),
    )

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=inputs,
        metadata_store=_MetadataStore(),
        config=MultiTimeframeTrendConfig(max_concurrent_positions=1),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=1_000_000.0,
    )

    assert artifacts.trades["symbol"].tolist() == ["AG"]
    assert "MAX_CONCURRENT_POSITIONS" in set(artifacts.rejections["reason_code"])


def test_portfolio_reduces_newest_positions_ten_minutes_before_close() -> None:
    definitions = (
        ("AG", "AG2602.SHF", "2026-01-05 14:26", "2026-01-05 14:27"),
        ("AU", "AU2602.SHF", "2026-01-05 14:27", "2026-01-05 14:28"),
        ("CU", "CU2602.SHF", "2026-01-05 14:28", "2026-01-05 14:29"),
    )
    inputs = []
    for symbol, contract, signal, active in definitions:
        bars = _minutes(
            [
                ("2026-01-05 14:26", 99.0, 99.0, 99.0, 99.0, contract),
                ("2026-01-05 14:27", 100.0, 101.0, 100.0, 100.0, contract),
                ("2026-01-05 14:28", 100.0, 101.0, 100.0, 100.0, contract),
                ("2026-01-05 14:29", 100.0, 101.0, 100.0, 100.0, contract),
                ("2026-01-05 14:50", 100.0, 101.0, 100.0, 100.0, contract),
                ("2026-01-05 14:51", 100.0, 101.0, 100.0, 100.0, contract),
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
                    signal=signal,
                    active=active,
                    expires="2026-01-05 14:40",
                    setup="always_in",
                    stop=99.9,
                    contract=contract,
                    candidate_id=f"{symbol}-000001",
                    symbol=symbol,
                ),
                sessions=_day_sessions(),
            )
        )

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=tuple(inputs),
        metadata_store=_MetadataStore(
            contract_size=10.0,
            margin_rate=1.0,
            stressed_round_trip_fee_cash=0.0,
            price_tick=0.1,
            fees_by_date={
                date(2026, 1, 5): {
                    "open_fee_rate": 0.0,
                    "close_fee_rate": 0.0,
                    "close_today_fee_rate": 0.0,
                    "fee_per_lot_open": 0.0,
                    "fee_per_lot_close": 0.0,
                    "fee_per_lot_close_today": 0.0,
                }
            },
        ),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=False,
            profit_floor_enabled=False,
            entry_blocked_session_windows=(),
            daily_circuit_breaker_enabled=False,
            max_positions_per_sector=99,
            max_symbol_margin_utilization=0.20,
        ),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=100_000.0,
    )

    overnight = artifacts.trades.loc[
        artifacts.trades["exit_reason"].eq("OVERNIGHT_MARGIN_REDUCTION")
    ]
    assert overnight["symbol"].tolist() == ["CU", "AU"]
    assert overnight["exit_time"].eq(
        pd.Timestamp("2026-01-05 14:51", tz=TZ)
    ).all()
    assert artifacts.trades.loc[
        artifacts.trades["symbol"].eq("AG"), "exit_reason"
    ].item() == "INTERVAL_END"
    assert artifacts.daily_equity[
        "peak_overnight_margin_utilization"
    ].max() == pytest.approx(0.60)
    assert "OVERNIGHT_MARGIN_LIMIT_BREACH" in set(
        artifacts.rejections["reason_code"]
    )


def test_portfolio_reduces_only_required_lots_ten_minutes_before_close(
    monkeypatch,
) -> None:
    contract = "AG2602.SHF"
    bars = _minutes(
        [
            ("2026-01-05 14:47", 99.0, 99.0, 99.0, 99.0, contract),
            ("2026-01-05 14:48", 100.0, 100.1, 100.0, 100.0, contract),
            ("2026-01-05 14:50", 100.0, 100.0, 100.0, 100.0, contract),
            ("2026-01-05 14:51", 101.0, 101.0, 101.0, 101.0, contract),
            ("2026-01-06 09:01", 102.0, 102.0, 102.0, 102.0, contract),
        ]
    )
    store = _MetadataStore(
        contract_size=100.0,
        margin_rate=0.60,
        stressed_round_trip_fee_cash=0.0,
        price_tick=0.1,
        fees_by_date={
            date(2026, 1, 5): {
                "fee_per_lot_open": 1.0,
                "fee_per_lot_close_today": 2.0,
            },
            date(2026, 1, 6): {
                "fee_per_lot_open": 1.0,
                "fee_per_lot_close": 3.0,
            },
        },
    )

    symbol_updates: list[float] = []
    portfolio_updates: list[float] = []
    loss_cooldown_updates: list[float] = []
    original_symbol_update = trend_engine._advance_symbol_scaling
    original_portfolio_update = trend_engine._advance_portfolio_scaling
    original_loss_cooldown_update = trend_engine._advance_symbol_loss_cooldown

    def capture_symbol_update(state, net_pnl, config):
        symbol_updates.append(float(net_pnl))
        return original_symbol_update(state, net_pnl, config)

    def capture_portfolio_update(state, *, cash, net_pnl, config):
        portfolio_updates.append(float(net_pnl))
        return original_portfolio_update(
            state,
            cash=cash,
            net_pnl=net_pnl,
            config=config,
        )

    def capture_loss_cooldown_update(state, *, trade, config):
        loss_cooldown_updates.append(float(trade["net_pnl"]))
        return original_loss_cooldown_update(state, trade=trade, config=config)

    monkeypatch.setattr(
        trend_engine, "_advance_symbol_scaling", capture_symbol_update
    )
    monkeypatch.setattr(
        trend_engine, "_advance_portfolio_scaling", capture_portfolio_update
    )
    monkeypatch.setattr(
        trend_engine,
        "_advance_symbol_loss_cooldown",
        capture_loss_cooldown_update,
    )

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=(
            trend_engine.PortfolioReplayInput(
                root_symbol="AG",
                exchange="SHFE",
                minute_bars=bars,
                five_minute_context=pd.DataFrame(
                    columns=["bar_end", "daily_direction"]
                ),
                candidates=_candidate(
                    signal="2026-01-05 14:47",
                    active="2026-01-05 14:48",
                    expires="2026-01-05 14:49",
                    setup="always_in",
                    trigger=100.0,
                    stop=97.5,
                ),
                sessions=_day_sessions(),
            ),
        ),
        metadata_store=store,
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=False,
            profit_floor_enabled=False,
            entry_blocked_session_windows=(),
            daily_circuit_breaker_enabled=False,
            max_positions_per_sector=99,
            overnight_reduction_minutes=10),
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        initial_equity=100_000.0,
    )

    assert artifacts.exit_legs["quantity"].tolist() == [1, 3]
    assert artifacts.exit_legs["reason"].tolist() == [
        "OVERNIGHT_MARGIN_REDUCTION",
        "INTERVAL_END",
    ]
    assert artifacts.exit_legs["close_type"].tolist() == [
        "CLOSE_TODAY",
        "CLOSE",
    ]
    assert artifacts.exit_legs["is_final_exit"].tolist() == [0, 1]
    trade = artifacts.trades.iloc[0]
    assert len(artifacts.trades) == 1
    assert trade["quantity"] == 4
    assert trade["overnight_reduced_quantity"] == 1
    assert trade["had_overnight_reduction"] == 1
    assert trade["exit_price"] == pytest.approx(101.75)
    assert trade["net_pnl"] == pytest.approx(artifacts.exit_legs["net_pnl"].sum())
    assert artifacts.fills.loc[
        artifacts.fills["fill_kind"].eq("EXIT"), "quantity"
    ].tolist() == [1, 3]
    assert portfolio_updates == pytest.approx(
        artifacts.exit_legs["net_pnl"].tolist()
    )
    assert symbol_updates == pytest.approx([trade["net_pnl"]])
    assert loss_cooldown_updates == pytest.approx([trade["net_pnl"]])
    first_day = artifacts.daily_equity.loc[
        artifacts.daily_equity["date"].eq(date(2026, 1, 5))
    ].iloc[0]
    assert first_day["open_risk"] == pytest.approx(750.0)


def _portfolio_symbol_loss_cooldown_artifacts(
    *,
    enabled: bool,
    include_au: bool = False,
    pre_filtered_candidate: bool = False,
) -> trend_engine.ReplayArtifacts:
    contract = "AG2602.SHF"
    bars = _minutes(
        [
            ("2026-01-05 09:00", 99.0, 99.0, 99.0, 99.0, contract),
            ("2026-01-05 09:01", 100.0, 101.0, 100.0, 100.0, contract),
            ("2026-01-05 09:02", 100.0, 100.0, 97.0, 97.0, contract),
            ("2026-01-06 09:00", 99.0, 99.0, 99.0, 99.0, contract),
            ("2026-01-06 09:01", 100.0, 101.0, 100.0, 100.0, contract),
            ("2026-01-06 09:02", 100.0, 100.0, 97.0, 97.0, contract),
            ("2026-01-06 09:03", 100.0, 101.0, 100.0, 100.0, contract),
            ("2026-01-07 09:03", 99.0, 99.0, 99.0, 99.0, contract),
            ("2026-01-08 09:00", 99.0, 99.0, 99.0, 99.0, contract),
            ("2026-01-08 09:01", 100.0, 101.0, 100.0, 100.0, contract),
        ]
    )
    candidates = pd.concat(
        [
            _candidate(
                signal="2026-01-05 09:00",
                active="2026-01-05 09:01",
                expires="2026-01-05 09:02",
                setup="always_in",
                candidate_id="AG-000001",
            ),
            _candidate(
                signal="2026-01-06 09:00",
                active="2026-01-06 09:01",
                expires="2026-01-06 09:02",
                setup="always_in",
                candidate_id="AG-000002",
            ),
            _candidate(
                signal="2026-01-06 09:02",
                active="2026-01-06 09:03",
                expires="2026-01-06 09:03",
                setup="always_in",
                candidate_id="AG-000003",
            ),
            _candidate(
                signal="2026-01-07 09:03",
                active="2026-01-07 09:04",
                expires="2026-01-07 09:05",
                setup="always_in",
                candidate_id="AG-000004",
            ),
            _candidate(
                signal="2026-01-08 09:00",
                active="2026-01-08 09:01",
                expires="2026-01-08 09:02",
                setup="always_in",
                candidate_id="AG-000005",
            ),
        ],
        ignore_index=True,
    )
    if pre_filtered_candidate:
        candidates.loc[
            candidates["candidate_id"].eq("AG-000004"), "filtered_reason"
        ] = "PRE_FILTERED"

    inputs = [
        trend_engine.PortfolioReplayInput(
            root_symbol="AG",
            exchange="SHFE",
            minute_bars=bars,
            five_minute_context=pd.DataFrame(
                columns=["bar_end", "daily_direction"]
            ),
            candidates=candidates,
            sessions=_day_sessions(),
        )
    ]
    if include_au:
        inputs.append(
            trend_engine.PortfolioReplayInput(
                root_symbol="AU",
                exchange="SHFE",
                minute_bars=_minutes(
                    [
                        (
                            "2026-01-06 09:02",
                            99.0,
                            99.0,
                            99.0,
                            99.0,
                            "AU2602.SHF",
                        ),
                        (
                            "2026-01-06 09:03",
                            100.0,
                            101.0,
                            100.0,
                            100.0,
                            "AU2602.SHF",
                        ),
                    ]
                ),
                five_minute_context=pd.DataFrame(
                    columns=["bar_end", "daily_direction"]
                ),
                candidates=_candidate(
                    signal="2026-01-06 09:02",
                    active="2026-01-06 09:03",
                    expires="2026-01-06 09:03",
                    setup="always_in",
                    contract="AU2602.SHF",
                    candidate_id="AU-000001",
                    symbol="AU",
                ),
                sessions=_day_sessions(),
            )
        )

    return trend_engine.replay_trend_portfolio(
        inputs=tuple(inputs),
        metadata_store=_MetadataStore(),
        config=MultiTimeframeTrendConfig(symbol_loss_cooldown_enabled=enabled),
        start=date(2026, 1, 5),
        end=date(2026, 1, 8),
        initial_equity=1_000_000.0,
    )


def test_portfolio_symbol_loss_cooldown_rejects_same_timestamp_and_releases_at_day_open() -> None:
    artifacts = _portfolio_symbol_loss_cooldown_artifacts(enabled=True)

    cooldown_rejections = artifacts.rejections.loc[
        artifacts.rejections["reason_code"].eq("SYMBOL_LOSS_COOLDOWN")
    ].set_index("candidate_id")
    assert set(cooldown_rejections.index) == {"AG-000003", "AG-000004"}
    assert cooldown_rejections.loc["AG-000003", "detail"] == (
        "second_loss_exit=2026-01-06T09:02:00+08:00; "
        "release_at=2026-01-08T09:00:00+08:00"
    )
    assert "AG-000003" not in set(artifacts.plans["candidate_id"])
    assert "AG-000003" not in set(artifacts.orders["candidate_id"])
    assert "AG-000003" not in set(artifacts.fills["candidate_id"])
    assert "AG-000004" not in set(artifacts.plans["candidate_id"])
    assert "AG-000005" in set(artifacts.plans["candidate_id"])


def test_portfolio_symbol_loss_cooldown_can_be_disabled() -> None:
    artifacts = _portfolio_symbol_loss_cooldown_artifacts(enabled=False)

    assert "SYMBOL_LOSS_COOLDOWN" not in set(artifacts.rejections["reason_code"])
    assert "AG-000003" in set(artifacts.plans["candidate_id"])


def test_portfolio_symbol_loss_cooldown_is_isolated_per_symbol() -> None:
    artifacts = _portfolio_symbol_loss_cooldown_artifacts(
        enabled=True,
        include_au=True,
    )

    assert "AU-000001" in set(artifacts.plans["candidate_id"])
    au_reasons = artifacts.rejections.loc[
        artifacts.rejections["candidate_id"].eq("AU-000001"), "reason_code"
    ]
    assert "SYMBOL_LOSS_COOLDOWN" not in set(au_reasons)


def test_portfolio_symbol_loss_cooldown_keeps_filtered_reason_priority() -> None:
    artifacts = _portfolio_symbol_loss_cooldown_artifacts(
        enabled=True,
        pre_filtered_candidate=True,
    )

    candidate_reasons = artifacts.rejections.loc[
        artifacts.rejections["candidate_id"].eq("AG-000004"), "reason_code"
    ]
    assert candidate_reasons.tolist() == ["PRE_FILTERED"]


def test_overnight_reduction_keeps_newest_first_when_cutoff_bar_is_missing() -> None:
    inputs = []
    definitions = (
        (
            "AG",
            "AG2602.SHF",
            "2026-01-05 14:47",
            "2026-01-05 14:48",
            [
                ("2026-01-05 14:47", 99.0, 99.0, 99.0, 99.0),
                ("2026-01-05 14:48", 100.0, 101.0, 100.0, 100.0),
                ("2026-01-05 14:50", 100.0, 101.0, 100.0, 100.0),
                ("2026-01-05 14:51", 100.0, 101.0, 100.0, 100.0),
            ],
        ),
        (
            "CU",
            "CU2602.SHF",
            "2026-01-05 14:48",
            "2026-01-05 14:49",
            [
                ("2026-01-05 14:48", 99.0, 99.0, 99.0, 99.0),
                ("2026-01-05 14:49", 100.0, 101.0, 100.0, 100.0),
                ("2026-01-05 14:51", 100.0, 101.0, 100.0, 100.0),
            ],
        ),
    )
    for symbol, contract, signal, active, rows in definitions:
        inputs.append(
            trend_engine.PortfolioReplayInput(
                root_symbol=symbol,
                exchange="SHFE",
                minute_bars=_minutes(
                    [(*row, contract) for row in rows]
                ),
                five_minute_context=pd.DataFrame(
                    columns=["bar_end", "daily_direction"]
                ),
                candidates=_candidate(
                    signal=signal,
                    active=active,
                    expires="2026-01-05 14:55",
                    setup="always_in",
                    stop=99.9,
                    contract=contract,
                    candidate_id=f"{symbol}-000001",
                    symbol=symbol,
                ),
                sessions=_day_sessions(),
            )
        )

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=tuple(inputs),
        metadata_store=_MetadataStore(
            contract_size=10.0,
            margin_rate=1.0,
            stressed_round_trip_fee_cash=0.0,
            price_tick=0.1,
        ),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=False,
            profit_floor_enabled=False,
            entry_blocked_session_windows=(),
            daily_circuit_breaker_enabled=False,
            max_positions_per_sector=99,
            max_symbol_margin_utilization=0.20),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=100_000.0,
    )

    assert artifacts.trades.set_index("symbol").loc[
        "CU", "exit_reason"
    ] == "OVERNIGHT_MARGIN_REDUCTION"
    assert artifacts.trades.set_index("symbol").loc[
        "AG", "exit_reason"
    ] == "INTERVAL_END"


def test_portfolio_overnight_reduction_considers_all_tradeable_positions() -> None:
    inputs = []
    for symbol, contract, signal, active, sessions in (
        (
            "AG",
            "AG2602.SHF",
            "2026-01-05 14:26",
            "2026-01-05 14:27",
            _day_sessions(),
        ),
        (
            "CU",
            "CU2602.SHF",
            "2026-01-05 14:28",
            "2026-01-05 14:29",
            _day_sessions(close=time(15, 30)),
        ),
    ):
        inputs.append(
            trend_engine.PortfolioReplayInput(
                root_symbol=symbol,
                exchange="SHFE",
                minute_bars=_minutes(
                    [
                        ("2026-01-05 14:26", 99.0, 99.0, 99.0, 99.0, contract),
                        ("2026-01-05 14:27", 100.0, 101.0, 100.0, 100.0, contract),
                        ("2026-01-05 14:28", 100.0, 101.0, 100.0, 100.0, contract),
                        ("2026-01-05 14:29", 100.0, 101.0, 100.0, 100.0, contract),
                            ("2026-01-05 14:50", 100.0, 101.0, 100.0, 100.0, contract),
                            ("2026-01-05 14:51", 100.0, 101.0, 100.0, 100.0, contract),
                    ]
                ),
                five_minute_context=pd.DataFrame(
                    columns=["bar_end", "daily_direction"]
                ),
                candidates=_candidate(
                    signal=signal,
                    active=active,
                    expires="2026-01-05 14:40",
                    setup="always_in",
                    stop=99.9,
                    contract=contract,
                    candidate_id=f"{symbol}-000001",
                    symbol=symbol,
                ),
                sessions=sessions,
            )
        )

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=tuple(inputs),
        metadata_store=_MetadataStore(
            contract_size=10.0,
            margin_rate=1.0,
            stressed_round_trip_fee_cash=0.0,
            price_tick=0.1,
        ),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=False,
            profit_floor_enabled=False,
            entry_blocked_session_windows=(),
            daily_circuit_breaker_enabled=False,
            max_positions_per_sector=99,
            max_symbol_margin_utilization=0.20),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=100_000.0,
    )

    trades = artifacts.trades.set_index("symbol")
    assert trades.loc["CU", "exit_reason"] == "OVERNIGHT_MARGIN_REDUCTION"
    assert trades.loc["CU", "exit_time"] == pd.Timestamp(
        "2026-01-05 14:51", tz=TZ
    )
    assert trades.loc["AG", "exit_reason"] == "INTERVAL_END"


def test_portfolio_overnight_reduction_retries_after_limit_lock() -> None:
    contract = "AG2602.SHF"
    bars = _minutes(
        [
            ("2026-01-05 14:26", 99.0, 99.0, 99.0, 99.0, contract),
            ("2026-01-05 14:27", 100.0, 101.0, 100.0, 100.0, contract),
            ("2026-01-05 14:50", 100.0, 101.0, 100.0, 100.0, contract),
            ("2026-01-05 14:51", 100.0, 100.0, 100.0, 100.0, contract),
            ("2026-01-05 14:52", 100.0, 101.0, 100.0, 100.0, contract),
        ]
    )
    artifacts = trend_engine.replay_trend_portfolio(
        inputs=(
            trend_engine.PortfolioReplayInput(
                root_symbol="AG",
                exchange="SHFE",
                minute_bars=bars,
                five_minute_context=pd.DataFrame(
                    columns=["bar_end", "daily_direction"]
                ),
                candidates=_candidate(
                    signal="2026-01-05 14:26",
                    active="2026-01-05 14:27",
                    expires="2026-01-05 14:40",
                    setup="always_in",
                    stop=99.9,
                ),
                sessions=_day_sessions(),
            ),
        ),
        metadata_store=_MetadataStore(
            contract_size=10.0,
            margin_rate=1.0,
            stressed_round_trip_fee_cash=0.0,
            price_tick=0.1,
            limit_down_by_date={date(2026, 1, 5): 100.0},
        ),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=False,
            profit_floor_enabled=False,
            entry_blocked_session_windows=(),
            daily_circuit_breaker_enabled=False,
            max_positions_per_sector=99,
            max_symbol_margin_utilization=0.40),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=100_000.0,
    )

    trade = artifacts.trades.iloc[0]
    assert len(artifacts.trades) == 1
    assert trade["exit_reason"] == "INTERVAL_END"
    assert artifacts.exit_legs["reason"].tolist() == [
        "OVERNIGHT_MARGIN_REDUCTION",
        "INTERVAL_END",
    ]
    assert artifacts.exit_legs["quantity"].tolist() == [20, 20]
    assert artifacts.exit_legs.iloc[0]["exit_time"] == pd.Timestamp(
        "2026-01-05 14:52", tz=TZ
    )
    assert "OVERNIGHT_REDUCTION_LIMIT_LOCKED" in set(
        artifacts.rejections["reason_code"]
    )


def test_protective_stop_overrides_pending_overnight_partial_reduction() -> None:
    contract = "AG2602.SHF"
    bars = _minutes(
        [
            ("2026-01-05 14:47", 99.0, 99.0, 99.0, 99.0, contract),
            ("2026-01-05 14:48", 100.0, 100.1, 100.0, 100.0, contract),
            ("2026-01-05 14:50", 100.0, 100.0, 100.0, 100.0, contract),
            ("2026-01-05 14:51", 99.0, 99.0, 97.0, 97.0, contract),
        ]
    )

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=(
            trend_engine.PortfolioReplayInput(
                root_symbol="AG",
                exchange="SHFE",
                minute_bars=bars,
                five_minute_context=pd.DataFrame(
                    columns=["bar_end", "daily_direction"]
                ),
                candidates=_candidate(
                    signal="2026-01-05 14:47",
                    active="2026-01-05 14:48",
                    expires="2026-01-05 14:49",
                    setup="always_in",
                    trigger=100.0,
                    stop=97.5,
                ),
                sessions=_day_sessions(),
            ),
        ),
        metadata_store=_MetadataStore(
            contract_size=100.0,
            margin_rate=0.60,
            stressed_round_trip_fee_cash=0.0,
            price_tick=0.1,
        ),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=False,
            profit_floor_enabled=False,
            entry_blocked_session_windows=(),
            daily_circuit_breaker_enabled=False,
            max_positions_per_sector=99,
            overnight_reduction_minutes=10),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=100_000.0,
    )

    assert artifacts.exit_legs["reason"].tolist() == ["STOP"]
    assert artifacts.exit_legs["quantity"].tolist() == [4]
    assert artifacts.trades.iloc[0]["exit_reason"] == "STOP"


def test_limit_locked_stop_replaces_pending_overnight_partial_reduction() -> None:
    contract = "AG2602.SHF"
    bars = _minutes(
        [
            ("2026-01-05 14:47", 99.0, 99.0, 99.0, 99.0, contract),
            ("2026-01-05 14:48", 100.0, 100.1, 100.0, 100.0, contract),
            ("2026-01-05 14:50", 100.0, 100.0, 100.0, 100.0, contract),
            ("2026-01-05 14:51", 97.0, 97.0, 97.0, 97.0, contract),
            ("2026-01-05 14:52", 99.0, 99.0, 98.0, 99.0, contract),
        ]
    )

    artifacts = trend_engine.replay_trend_portfolio(
        inputs=(
            trend_engine.PortfolioReplayInput(
                root_symbol="AG",
                exchange="SHFE",
                minute_bars=bars,
                five_minute_context=pd.DataFrame(
                    columns=["bar_end", "daily_direction"]
                ),
                candidates=_candidate(
                    signal="2026-01-05 14:47",
                    active="2026-01-05 14:48",
                    expires="2026-01-05 14:49",
                    setup="always_in",
                    trigger=100.0,
                    stop=97.5,
                ),
                sessions=_day_sessions(),
            ),
        ),
        metadata_store=_MetadataStore(
            contract_size=100.0,
            margin_rate=0.60,
            stressed_round_trip_fee_cash=0.0,
            price_tick=0.1,
            limit_down_by_date={date(2026, 1, 5): 97.0},
        ),
        config=MultiTimeframeTrendConfig(
            pre_break_protection_enabled=False,
            profit_floor_enabled=False,
            entry_blocked_session_windows=(),
            daily_circuit_breaker_enabled=False,
            max_positions_per_sector=99,
            overnight_reduction_minutes=10),
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
        initial_equity=100_000.0,
    )

    assert artifacts.exit_legs["reason"].tolist() == ["STOP"]
    assert artifacts.exit_legs["quantity"].tolist() == [4]
    assert artifacts.exit_legs.iloc[0]["exit_time"] == pd.Timestamp(
        "2026-01-05 14:52", tz=TZ
    )


def test_portfolio_marks_held_margin_with_current_trade_date_rate() -> None:
    entry_date = date(2026, 1, 5)
    exit_date = date(2026, 1, 6)
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-06 09:01", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )
    artifacts = trend_engine.replay_trend_portfolio(
        inputs=(
            trend_engine.PortfolioReplayInput(
                root_symbol="AG",
                exchange="SHFE",
                minute_bars=bars,
                five_minute_context=pd.DataFrame(
                    columns=["bar_end", "daily_direction"]
                ),
                candidates=_candidate(setup="always_in"),
                sessions=_day_sessions(),
            ),
        ),
        metadata_store=_MetadataStore(
            contract_size=10.0,
            margin_rate_by_date={entry_date: 0.10, exit_date: 0.50},
        ),
        config=MultiTimeframeTrendConfig(),
        start=entry_date,
        end=exit_date,
        initial_equity=100_000.0,
    )

    utilization = artifacts.daily_equity.set_index("date")[
        "peak_symbol_margin_utilization"
    ]
    assert utilization.loc[exit_date] == pytest.approx(
        utilization.loc[entry_date] * 5.0
    )


def test_limit_locked_protective_stop_waits_for_tradeable_minute() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-06 09:01", 98.0, 98.0, 98.0, 98.0, "AG2602.SHF"),
            ("2026-01-06 09:02", 97.0, 99.0, 97.0, 98.0, "AG2602.SHF"),
        ]
    )
    store = _MetadataStore(limit_down_by_date={date(2026, 1, 6): 98.0})

    artifacts = replay_trend_strategy(
        root_symbol="AG",
        exchange="SHFE",
        minute_bars=bars,
        five_minute_context=pd.DataFrame(columns=["bar_end", "daily_direction"]),
        candidates=_candidate(),
        metadata_store=store,
        config=MultiTimeframeTrendConfig(),
        start=date(2026, 1, 5),
        end=date(2026, 1, 6),
        initial_equity=1_000_000.0,
    )

    trade = artifacts.trades.iloc[0]
    assert trade["exit_time"] == pd.Timestamp("2026-01-06 09:02", tz=TZ)
    assert trade["exit_price"] == pytest.approx(97.0)


def test_limit_locked_interval_end_blocks_official_liquidation() -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-06 09:01", 98.0, 98.0, 98.0, 98.0, "AG2602.SHF"),
        ]
    )
    store = _MetadataStore(limit_down_by_date={date(2026, 1, 6): 98.0})

    with pytest.raises(
        BlockedMetadataError,
        match="BLOCKED_INTERVAL_END_LIQUIDATION",
    ):
        replay_trend_strategy(
            root_symbol="AG",
            exchange="SHFE",
            minute_bars=bars,
            five_minute_context=pd.DataFrame(
                columns=["bar_end", "daily_direction"]
            ),
            candidates=_candidate(),
            metadata_store=store,
            config=MultiTimeframeTrendConfig(),
            start=date(2026, 1, 5),
            end=date(2026, 1, 6),
            initial_equity=1_000_000.0,
        )


def test_report_contains_metrics_command_and_audit_tables(tmp_path) -> None:
    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:07", 104.0, 104.0, 103.0, 104.0, "AG2602.SHF"),
        ]
    )
    artifacts = _replay(bars)
    artifacts = replace(
        artifacts,
        rejections=pd.DataFrame(
            [
                {
                    "root_symbol": "ALL",
                    "feature_asof": pd.Timestamp("2026-01-05 14:30", tz=TZ),
                    "reason_code": "OVERNIGHT_MARGIN_LIMIT_BREACH",
                    "candidate_id": "",
                    "risk_budget": float("nan"),
                    "loss_per_lot": float("nan"),
                    "detail": "margin above limit",
                },
                {
                    "root_symbol": "AG",
                    "feature_asof": pd.Timestamp("2026-01-05 14:31", tz=TZ),
                    "reason_code": "OVERNIGHT_REDUCTION_LIMIT_LOCKED",
                    "candidate_id": "AG-000001",
                    "risk_budget": float("nan"),
                    "loss_per_lot": float("nan"),
                    "detail": "retry pending",
                },
            ],
            columns=trend_engine.REJECTION_COLUMNS,
        ),
    )
    command = "cd /repo && python3 -m example.runner --symbols AG"
    daily_market_bars = pd.DataFrame(
        {
            "symbol": ["AG"] * 20,
            "exchange_trade_date": pd.date_range(
                "2025-12-16", periods=20, freq="D"
            ).date,
            "volume": list(range(1, 21)),
            "turnover": [value * 1_000.0 for value in range(1, 21)],
        }
    )
    entry_trade_dates = pd.DataFrame(
        {
            "candidate_id": ["AG-000001"],
            "exchange_trade_date": [date(2026, 1, 5)],
        }
    )

    summary, output = publish_backtest_report(
        tmp_path / "ag-run",
        artifacts=artifacts,
        initial_equity=1_000_000.0,
        metadata_gaps=[],
        context={
            "requested_start": "2026-01-05",
            "requested_end": "2026-01-05",
            "requested_symbols": ["AG0.SHFE"],
            "loaded_symbols": ["AG0.SHFE"],
            "position_scaling_config": {
                "symbol_loss_streak": 2,
                "drawdown_scale_threshold": 0.02,
                "drawdown_scale_release": 0.01,
                "drawdown_scale_factor": 0.5,
                "position_scale_min_one_lot": True,
            },
        },
        reproduction_command={
            "working_directory": "/repo",
            "shell_command": command,
            "argv": ["python3", "-m", "example.runner", "--symbols", "AG"],
        },
        daily_market_bars=daily_market_bars,
        entry_trade_dates=entry_trade_dates,
        symbols=("AG", "AU"),
    )

    required = {
        "summary.json",
        "report.md",
        "RUN_COMMAND.sh",
        "candidates.csv",
        "plans.csv",
        "orders.csv",
        "fills.csv",
        "exit_legs.csv",
        "trades.csv",
        "daily_equity.csv",
        "performance_by_group.csv",
        "performance_by_symbol.csv",
        "daily_equity_curve.png",
        "rejections.csv",
        "fee_audit.csv",
        "position_scaling_events.csv",
    }
    assert required.issubset({path.name for path in output.iterdir()})
    assert (output / "RUN_COMMAND.sh").read_text(encoding="utf-8") == command + "\n"
    performance = summary["official_performance"]
    assert {
        "total_return",
        "max_drawdown",
        "win_rate",
        "profit_factor",
        "average_win_loss_ratio",
    }.issubset(performance)
    assert performance["total_return"] == pytest.approx(
        artifacts.daily_equity.iloc[-1]["equity"] / 1_000_000.0 - 1.0
    )
    assert {
        "peak_intraday_margin_utilization",
        "peak_overnight_margin_utilization",
        "peak_symbol_margin_utilization",
        "max_concurrent_positions",
        "rejection_counts",
        "overnight_margin_breach_events",
        "overnight_reduction_limit_locked_events",
    }.issubset(summary["portfolio_risk"])
    assert summary["portfolio_risk"]["rejection_counts"] == {
        "OVERNIGHT_MARGIN_LIMIT_BREACH": 1,
        "OVERNIGHT_REDUCTION_LIMIT_LOCKED": 1,
    }
    assert summary["position_scaling"] == {
        # 真正决定手数的回撤减仓机制
        "drawdown_scale_threshold": 0.02,
        "drawdown_scale_release": 0.01,
        "drawdown_scale_factor": 0.5,
        "position_scale_min_one_lot": True,
        "drawdown_trigger_count": 0,
        "drawdown_recovery_count": 0,
        # 冷却类计数：只影响是否开仓，不改手数
        "symbol_loss_streak": 2,
        "symbol_trigger_count": 0,
        "portfolio_trigger_count": 0,
        "symbol_recovery_count": 0,
        "portfolio_recovery_count": 0,
        "scaled_entry_count": 0,
        "scaled_entry_by_reason": {},
    }
    fee_audit = pd.read_csv(output / "fee_audit.csv")
    exit_legs = pd.read_csv(output / "exit_legs.csv")
    trades = pd.read_csv(output / "trades.csv")
    symbol_performance = pd.read_csv(output / "performance_by_symbol.csv")
    assert trades.columns.tolist() == artifacts.trades.columns.tolist()
    assert trades.columns.tolist() == list(trend_engine.TRADE_COLUMNS)
    assert trades.loc[0, "prior_5d_avg_market_volume"] == pytest.approx(18.0)
    assert trades.loc[0, "prior_10d_avg_market_volume"] == pytest.approx(15.5)
    assert trades.loc[0, "prior_20d_avg_market_volume"] == pytest.approx(10.5)
    assert symbol_performance.columns.tolist() == list(
        trend_report.SYMBOL_PERFORMANCE_COLUMNS
    )
    assert symbol_performance["symbol"].tolist() == ["AG", "AU"]
    assert symbol_performance.loc[0, "trade_count"] == 1
    assert symbol_performance.loc[0, "total_net_pnl"] == pytest.approx(
        artifacts.trades.loc[0, "net_pnl"]
    )
    assert symbol_performance.loc[1, "trade_count"] == 0
    chart = output / "daily_equity_curve.png"
    assert chart.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")
    assert {
        "candidate_id",
        "open_fee",
        "close_fee",
        "close_type",
        "entry_fee_rate",
        "exit_fee_rate",
        "entry_fee_schedule_id",
        "exit_fee_schedule_id",
        "contract_multiplier",
        "entry_fee_source",
        "exit_fee_source",
        "entry_fee_effective_from",
        "exit_fee_effective_from",
        "entry_fee_known_at",
        "exit_fee_known_at",
    }.issubset(fee_audit.columns)
    assert len(exit_legs) == len(fee_audit)
    assert fee_audit["fees"].sum() == pytest.approx(exit_legs["fees"].sum())
    gross_index = trades.columns.get_loc("gross_pnl")
    assert trades.columns[gross_index + 1:gross_index + 6].tolist() == list(
        trend_engine.TRADE_RETURN_COLUMNS
    )
    cycle_index = trades.columns.get_loc("cycle")
    assert trades.columns[cycle_index + 1:cycle_index + 3].tolist() == [
        "is_first_trade_in_trend_segment",
        "trigger_to_prior_5d_high_ratio",
    ]
    assert trades.loc[0, "is_first_trade_in_trend_segment"] == 1
    assert trades.loc[0, "trigger_to_prior_5d_high_ratio"] == pytest.approx(
        artifacts.trades.iloc[0]["trigger_to_prior_5d_high_ratio"]
    )
    report = (output / "report.md").read_text(encoding="utf-8")
    assert "最大回撤" in report
    assert "胜率" in report
    assert "平均盈亏比" in report
    assert "动态仓位" in report


def test_daily_equity_chart_handles_empty_equity(tmp_path: Path) -> None:
    output = tmp_path / "daily_equity_curve.png"

    trend_report._render_daily_equity_chart(
        output,
        daily_equity=pd.DataFrame(columns=["date", "equity"]),
        initial_equity=1_000_000.0,
    )

    assert output.read_bytes().startswith(b"\x89PNG\r\n\x1a\n")


def test_runner_builds_fixed_timeframe_ag_reproduction_command() -> None:
    args = build_parser().parse_args(
        [
            "--symbols",
            "AG",
            "--download-minute-data",
            "--start",
            "2026-01-01",
            "--end",
            "2026-02-05",
            "--initial-equity",
            "1000000",
        ]
    )

    reproduction = build_reproduction_command(args)

    assert args.symbols == ["AG"]
    assert args.risk_per_trade == pytest.approx(0.01)
    assert "cta.strategy.multi_timeframe_trend_backtest.runner" in reproduction[
        "shell_command"
    ]
    assert "--download-minute-data" in reproduction["argv"]
    assert "--long-tf" not in reproduction["argv"]


def test_portfolio_risk_config_defaults() -> None:
    config = MultiTimeframeTrendConfig()

    assert config.max_concurrent_positions == 5
    assert config.intraday_margin_utilization == pytest.approx(0.60)
    assert config.overnight_margin_utilization == pytest.approx(0.20)
    assert config.max_symbol_margin_utilization == pytest.approx(0.40)
    assert config.overnight_reduction_minutes == 10


@pytest.mark.parametrize(
    "overrides",
    [
        {"max_concurrent_positions": 0},
        {"intraday_margin_utilization": 0.0},
        {"intraday_margin_utilization": 1.01},
        {"overnight_margin_utilization": 0.61},
        {"max_symbol_margin_utilization": 0.61},
        {"overnight_reduction_minutes": 0},
    ],
)
def test_portfolio_risk_config_rejects_invalid_limits(
    overrides: dict[str, object],
) -> None:
    with pytest.raises(ValueError):
        MultiTimeframeTrendConfig(**overrides)


def test_portfolio_cli_options_are_reproducible() -> None:
    args = build_parser().parse_args(
        [
            "--symbols",
            "AG",
            "--download-minute-data",
            "--top-n",
            "8",
            "--start",
            "2026-01-01",
            "--end",
            "2026-02-05",
            "--max-concurrent-positions",
            "4",
            "--intraday-margin-utilization",
            "0.55",
            "--overnight-margin-utilization",
            "0.18",
            "--max-symbol-margin-utilization",
            "0.35",
            "--overnight-reduction-minutes",
            "25",
        ]
    )

    reproduction = build_reproduction_command(args)

    assert args.top_n == 8
    assert args.max_concurrent_positions == 4
    for option, value in (
        ("--top-n", "8"),
        ("--max-concurrent-positions", "4"),
        ("--intraday-margin-utilization", "0.55"),
        ("--overnight-margin-utilization", "0.18"),
        ("--max-symbol-margin-utilization", "0.35"),
        ("--overnight-reduction-minutes", "25"),
    ):
        index = reproduction["argv"].index(option)
        assert reproduction["argv"][index + 1] == value


def test_top_n_without_explicit_symbols_does_not_add_ag() -> None:
    args = build_parser().parse_args(
        [
            "--download-minute-data",
            "--top-n",
            "20",
            "--start",
            "2025-01-01",
            "--end",
            "2026-06-30",
        ]
    )

    reproduction = build_reproduction_command(args)

    assert args.symbols == []
    assert "--symbols" not in reproduction["argv"]
    assert reproduction["argv"][reproduction["argv"].index("--top-n") + 1] == "20"


def test_trend_runner_downloads_exact_requested_interval(monkeypatch) -> None:
    captured: dict[str, object] = {}

    def capture_symbols(*_args, **kwargs):
        captured.update(kwargs)
        raise RuntimeError("stop after download boundaries")

    monkeypatch.setattr(trend_runner, "prepare_backtest_symbols", capture_symbols)
    args = build_parser().parse_args(
        [
            "--download-minute-data",
            "--top-n",
            "20",
            "--start",
            "2025-01-01",
            "--end",
            "2026-06-30",
        ]
    )

    with pytest.raises(RuntimeError, match="stop after download boundaries"):
        trend_runner.run_from_args(args)

    assert captured == {
        "start": date(2025, 1, 1),
        "end": date(2026, 6, 30),
    }


def test_trend_runner_reports_candidate_blacklist_context(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured_context: dict[str, object] = {}

    monkeypatch.setattr(
        trend_runner,
        "prepare_backtest_symbols",
        lambda *_args, **_kwargs: ((), (), {"enabled": False}),
    )
    monkeypatch.setattr(
        trend_runner,
        "prepare_backtest_metadata",
        lambda *_args, **_kwargs: (
            object(),
            {"enabled": False},
            {
                "root_symbol": "ALL",
                "field": "test",
                "reason_code": "BLOCKED_METADATA",
                "reason": "empty test universe",
            },
        ),
    )

    def capture_report(
        path: Path,
        *,
        context: dict[str, object],
        **_kwargs: object,
    ) -> tuple[dict[str, object], Path]:
        path.mkdir(parents=True)
        captured_context.update(context)
        return dict(context), path

    monkeypatch.setattr(trend_runner, "publish_backtest_report", capture_report)
    monkeypatch.setattr(
        trend_runner,
        "render_opportunity_charts",
        lambda *_args, **_kwargs: pd.DataFrame(),
    )
    args = build_parser().parse_args(
        [
            "--start",
            "2026-01-01",
            "--end",
            "2026-01-02",
            "--output-root",
            str(tmp_path),
            "--run-id",
            "blacklist_context",
        ]
    )

    trend_runner.run_from_args(args)

    assert captured_context["candidate_blacklist"] == {
        "setup_types": ["pullback_breakout"],
        "directions": [-1],
    }
    assert captured_context["daily_filters"] == {
        "always_in_long_daily_ema_gap_min_ratio": 0.02,
        "first_trend_entry_daily_breakout_buffer_ratio": 0.002,
    }
    assert captured_context["symbol_loss_cooldown_config"] == {
        "enabled": True,
        "pair_window_hours": 48,
        "cooldown_hours": 24,
        "release_time": "09:00",
    }


def test_execution_candidate_recomputes_risk_at_portfolio_event() -> None:
    raw = _candidate().assign(
        filtered_reason="RISK_BELOW_ONE_LOT",
        candidate_status="filtered",
        sample_status="blocked_by_risk",
        is_filtered=1,
        rejection_code="RISK_BELOW_ONE_LOT",
        order_expire_bar_i=1,
        quantity=0,
    )
    five_signal = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                ["2026-01-05 09:05", "2026-01-05 09:10"]
            ).tz_localize(TZ),
            "adjustment_scale": [1.0, 1.0],
            "adjustment_offset": [0.0, 0.0],
            "adjustment_version": ["v1", "v1"],
        }
    )
    minute = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    ).assign(
        adjustment_scale=1.0,
        adjustment_offset=0.0,
        adjustment_version="v1",
    )

    candidates = _execution_candidates(
        raw,
        five_signal=five_signal,
        minute=minute,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
    )

    assert candidates["filtered_reason"].item() == ""
    assert candidates["candidate_status"].item() == "not_triggered"
    assert candidates["sample_status"].item() == "not_triggered_market"
    assert candidates["is_filtered"].item() == 0
    assert candidates["rejection_code"].item() == ""
    assert pd.isna(candidates["quantity"].item())


def test_execution_candidate_maps_virtual_target_to_actual_contract_price() -> None:
    raw = _candidate().assign(
        target_price_virtual=110.0,
        order_expire_bar_i=1,
    )
    five_signal = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                ["2026-01-05 09:05", "2026-01-05 09:10"]
            ).tz_localize(TZ),
            "adjustment_scale": [2.0, 2.0],
            "adjustment_offset": [10.0, 10.0],
            "adjustment_version": ["v1", "v1"],
        }
    )
    minute = _minutes(
        [
            ("2026-01-05 09:05", 44.0, 44.5, 44.0, 44.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 45.0, 46.0, 45.0, 45.0, "AG2602.SHF"),
        ]
    ).assign(
        adjustment_scale=2.0,
        adjustment_offset=10.0,
        adjustment_version="v1",
    )

    candidates = _execution_candidates(
        raw,
        five_signal=five_signal,
        minute=minute,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
    )
    artifacts = _replay(minute, candidates=candidates)

    assert candidates["signal_target_price"].item() == pytest.approx(110.0)
    assert candidates["target_price_virtual"].item() == pytest.approx(50.0)
    assert pd.isna(candidates["target_price"].item())
    assert candidates["target"].item() == pytest.approx(50.0)
    assert artifacts.plans["target"].item() == pytest.approx(50.0)


def test_execution_candidate_maps_prior_high_to_actual_contract_price() -> None:
    raw = _candidate(prior_5d_high=110.0).assign(order_expire_bar_i=1)
    five_signal = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                ["2026-01-05 09:05", "2026-01-05 09:10"]
            ).tz_localize(TZ),
            "adjustment_scale": [2.0, 2.0],
            "adjustment_offset": [10.0, 10.0],
            "adjustment_version": ["v1", "v1"],
        }
    )
    minute = _minutes(
        [
            ("2026-01-05 09:05", 44.0, 44.5, 44.0, 44.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 45.0, 46.0, 45.0, 45.0, "AG2602.SHF"),
        ]
    ).assign(
        adjustment_scale=2.0,
        adjustment_offset=10.0,
        adjustment_version="v1",
    )

    candidates = _execution_candidates(
        raw,
        five_signal=five_signal,
        minute=minute,
        start=date(2026, 1, 5),
        end=date(2026, 1, 5),
    )

    assert candidates["signal_prior_5d_high"].item() == pytest.approx(110.0)
    assert candidates["prior_5d_high"].item() == pytest.approx(50.0)


def test_portfolio_candidate_metadata_gap_blocks_official_replay() -> None:
    class _BlockedStore:
        def execution_snapshot(self, **request: object) -> SimpleNamespace:
            del request
            raise BlockedMetadataError(
                "historical fee schedule is missing",
                "BLOCKED_EXECUTION_COST",
            )

    bars = _minutes(
        [
            ("2026-01-05 09:05", 99.0, 99.0, 99.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 101.0, 100.0, 100.0, "AG2602.SHF"),
        ]
    )

    with pytest.raises(BlockedMetadataError, match="historical fee schedule"):
        trend_engine.replay_trend_portfolio(
            inputs=(
                trend_engine.PortfolioReplayInput(
                    root_symbol="AG",
                    exchange="SHFE",
                    minute_bars=bars,
                    five_minute_context=pd.DataFrame(
                        columns=["bar_end", "daily_direction"]
                    ),
                    candidates=_candidate(setup="always_in"),
                    sessions=_day_sessions(),
                ),
            ),
            metadata_store=_BlockedStore(),
            config=MultiTimeframeTrendConfig(),
            start=date(2026, 1, 5),
            end=date(2026, 1, 5),
            initial_equity=1_000_000.0,
        )


def test_blocked_replay_classifies_every_candidate() -> None:
    candidates = pd.concat(
        [
            _candidate(),
            _candidate(
                signal="2026-01-05 09:10",
                active="2026-01-05 09:11",
                candidate_id="AU-000001",
                symbol="AU",
                contract="AU2602.SHF",
            ),
        ],
        ignore_index=True,
    )

    artifacts = _blocked_replay(
        candidates,
        detail="BLOCKED_ROLL_EXECUTION_BAR: no executable old-contract bar",
    )

    assert set(artifacts.rejections["candidate_id"]) == {
        "AG-000001",
        "AU-000001",
    }
    assert set(artifacts.rejections["reason_code"]) == {"BLOCKED_METADATA"}


def test_render_opportunity_charts_groups_trade_and_rejection_outcomes(
    tmp_path,
    monkeypatch,
) -> None:
    output = tmp_path / "run"
    output.mkdir()
    candidates = pd.concat(
        [
            _candidate(setup="always_in").assign(
                entry=100.0,
                stop=98.0,
                target=float("nan"),
                target_price_virtual=104.0,
                setup="always_in",
            ),
            _candidate(
                signal="2026-01-05 09:06",
                active="2026-01-05 09:07",
                candidate_id="AU-000001",
                symbol="AU",
                contract="AU2602.SHF",
                setup="always_in",
            ).assign(
                entry=100.0,
                stop=98.0,
                target=999.0,
                target_price_virtual=104.0,
                setup="always_in",
            ),
        ],
        ignore_index=True,
    )
    ag = _minutes(
        [
            ("2026-01-05 09:04", 99.0, 100.0, 98.0, 99.0, "AG2602.SHF"),
            ("2026-01-05 09:05", 99.0, 101.0, 99.0, 100.0, "AG2602.SHF"),
            ("2026-01-05 09:06", 100.0, 102.0, 100.0, 101.0, "AG2602.SHF"),
        ]
    ).assign(symbol="AG")
    au = ag.assign(symbol="AU", contract_code="AU2602.SHF")
    five = pd.concat([ag, au], ignore_index=True)
    hourly = five.copy()
    daily = five.groupby("symbol", as_index=False).first()
    daily["bar_end"] = pd.to_datetime(
        ["2026-01-05 15:00", "2026-01-05 15:00"]
    ).tz_localize(TZ)
    trades = pd.DataFrame(
        [
            {
                "candidate_id": "AG-000001",
                "exit_price": 106.0,
                "final_target": 108.0,
            }
        ]
    )
    rejections = pd.DataFrame(
        [
            {
                "candidate_id": "AU-000001",
                "reason_code": "DAILY_OBSTACLE",
                "feature_asof": pd.Timestamp("2026-01-05 09:06", tz=TZ),
            }
        ]
    )
    rendered_outcomes: list[str] = []
    rendered_targets: list[float] = []
    rendered_panel_targets: list[float] = []
    original_render = trend_charts._render_card
    original_overlay = trend_charts._draw_overlay

    def capture_outcome(*args, **kwargs):
        rendered_outcomes.append(str(args[0]["outcome_code"]))
        rendered_targets.append(float(args[0]["target"]))
        return original_render(*args, **kwargs)

    def capture_overlay(*args, **kwargs):
        rendered_panel_targets.append(float(kwargs["candidate"]["target"]))
        return original_overlay(*args, **kwargs)

    monkeypatch.setattr(trend_charts, "_render_card", capture_outcome)
    monkeypatch.setattr(trend_charts, "_draw_overlay", capture_overlay)

    index = render_opportunity_charts(
        output,
        candidates=candidates,
        daily_bars=daily,
        hourly_bars=hourly,
        five_minute_bars=five,
        trades=trades,
        orders=pd.DataFrame(),
        rejections=rejections,
        render_outcomes="all",
    )

    assert len(index) == len(candidates) == 2
    assert (output / "opportunity_charts" / "index.csv").is_file()
    assert index["outcome_code"].tolist() == ["TRADED", "DAILY_OBSTACLE"]
    assert rendered_outcomes == ["TRADED", "DAILY_OBSTACLE"]
    assert rendered_targets == [106.0, 104.0]
    assert rendered_panel_targets == [106.0] * 3 + [104.0] * 3
    assert index["chart_path"].tolist() == [
        "opportunity_charts/TRADED/0001_AG_20260105_090500_always_in_LONG.png",
        "opportunity_charts/DAILY_OBSTACLE/0002_AU_20260105_090600_always_in_LONG.png",
    ]
    assert len(list((output / "opportunity_charts").rglob("*.png"))) == 2
    guide = (output / "OPPORTUNITY_CHARTS.md").read_text(encoding="utf-8")
    assert "1 小时" in guide
    assert "5 分钟" in guide
    assert "成交机会的 Target 使用 `trades.csv` 中的最终加权 `exit_price`" in guide
    assert "未成交机会继续使用虚拟 2R `target_price_virtual`" in guide


@pytest.mark.parametrize(
    ("rows", "error"),
    [
        ([{"candidate_id": "AG-000001"}], "exit_price"),
        ([{"exit_price": 106.0}], "candidate_id"),
        ([{"candidate_id": "AG-000001", "exit_price": float("nan")}], "finite"),
        (
            [
                {"candidate_id": "AG-000001", "exit_price": 106.0},
                {"candidate_id": "AG-000001", "exit_price": 107.0},
            ],
            "duplicate",
        ),
    ],
)
def test_traded_exit_prices_rejects_invalid_trade_audit_rows(
    rows: list[dict[str, object]],
    error: str,
) -> None:
    with pytest.raises(ValueError, match=error):
        trend_charts._traded_exit_prices(pd.DataFrame(rows))


def test_chart_window_keeps_signal_in_center_slot_at_data_boundary() -> None:
    signal = pd.Timestamp("2026-01-05 09:05", tz=TZ)
    frame = trend_charts._chart_frame(
        _minutes(
            [
                ("2026-01-05 09:05", 100.0, 101.0, 99.0, 100.0, "AG2602.SHF"),
                ("2026-01-05 09:10", 101.0, 102.0, 100.0, 101.0, "AG2602.SHF"),
                ("2026-01-05 09:15", 102.0, 103.0, 101.0, 102.0, "AG2602.SHF"),
            ]
        )
    )

    window = trend_charts._event_window(frame, signal, radius=2)

    assert window.attrs["signal_slot"] == 2
    assert window.attrs["plot_slot_count"] == 5
    assert window.loc[signal, "_plot_slot"] == 2
    assert window["_plot_slot"].tolist() == [2, 3, 4]


def test_chart_price_bounds_include_unreached_target() -> None:
    frame = pd.DataFrame(
        {
            "low": [98.0, 99.0],
            "high": [101.0, 102.0],
        }
    )

    low, high = trend_charts._panel_price_bounds(frame, (100.0, 97.0, 110.0))

    assert low <= 97.0
    assert high >= 110.0


def test_aggregated_session_segments_are_sorted_before_feature_building() -> None:
    frame = pd.DataFrame(
        {
            "bar_end": pd.to_datetime(
                ["2026-01-05 09:05", "2026-01-04 21:05", "2026-01-05 09:10"]
            ).tz_localize(TZ),
            "close": [101.0, 99.0, 102.0],
        }
    )

    ordered = _sort_aggregated_bars(frame)

    assert ordered["bar_end"].is_monotonic_increasing
    assert ordered["close"].tolist() == [99.0, 101.0, 102.0]


def test_daily_bar_combines_prior_night_with_exchange_trade_date_day() -> None:
    sessions = (
        SessionSpec(
            session_id="night",
            is_night=True,
            segments=(
                SessionSegment("night", time(21), time(21, 2), time(21)),
            ),
        ),
        SessionSpec(
            session_id="day",
            is_night=False,
            segments=(
                SessionSegment("day", time(9), time(9, 2), time(9)),
            ),
        ),
    )
    bar_ends = pd.to_datetime(
        [
            "2026-01-09 21:01",
            "2026-01-09 21:02",
            "2026-01-12 09:01",
            "2026-01-12 09:02",
        ]
    ).tz_localize(TZ)
    minute = pd.DataFrame(
        {
            "bar_end": bar_ends,
            "feature_sequence": [1, 2, 3, 4],
            "open": [100.0, 101.0, 102.0, 103.0],
            "high": [101.0, 102.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0],
            "close": [100.5, 101.5, 103.5, 104.5],
            "volume": [10.0, 20.0, 30.0, 40.0],
            "open_interest": [1_000.0, 1_001.0, 1_002.0, 1_003.0],
            "contract_code": "RB2605.SHF",
            "exchange_trade_date": date(2026, 1, 12),
        }
    )

    daily = trend_runner._aggregate_trading_day_daily_bars(
        minute,
        sessions=sessions,
    )

    assert len(daily) == 1
    assert daily.iloc[0]["exchange_trade_date"] == date(2026, 1, 12)
    assert daily.iloc[0]["bar_end"] == bar_ends[-1]
    assert daily.iloc[0]["feature_asof"] == bar_ends[-1]
    assert daily.iloc[0]["open"] == 100.0
    assert daily.iloc[0]["high"] == 105.0
    assert daily.iloc[0]["low"] == 99.0
    assert daily.iloc[0]["close"] == 104.5
    assert daily.iloc[0]["volume"] == 100.0
