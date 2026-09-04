from __future__ import annotations

from datetime import date, time
import inspect

import pandas as pd

from cta.strategy.brooks.cycle_v1.instruments import sessions as cycle_sessions
from cta.strategy.brooks.scalp import data as scalp_data
from cta.strategy.multi_timeframe_trend_backtest.aggregation_cache import (
    AGGREGATION_CACHE_VERSION,
)


TZ = "Asia/Shanghai"


def _bars(ends: pd.DatetimeIndex) -> pd.DataFrame:
    count = len(ends)
    return pd.DataFrame(
        {
            "bar_end": ends,
            "feature_sequence": range(count),
            "open": [100.0 + value for value in range(count)],
            "high": [101.0 + value for value in range(count)],
            "low": [99.0 + value for value in range(count)],
            "close": [100.5 + value for value in range(count)],
            "volume": 10.0,
            "turnover": 1_000.0,
            "open_interest": [1_000.0 + value for value in range(count)],
            "contract_code": "BR2604.SHF",
            "exchange_trade_date": date(2026, 3, 2),
        }
    )


def test_cycle_aggregation_assigns_segments_without_per_bar_callback(
    monkeypatch,
) -> None:
    sessions = (
        cycle_sessions.SessionSpec(
            session_id="night",
            is_night=True,
            segments=(
                cycle_sessions.SessionSegment(
                    "night", time(21), time(2, 30), time(21)
                ),
            ),
        ),
    )
    bars = _bars(
        pd.date_range("2026-03-03 00:01", periods=5, freq="1min", tz=TZ)
    )
    monkeypatch.setattr(
        cycle_sessions,
        "_assign_segment",
        lambda *args, **kwargs: (_ for _ in ()).throw(
            AssertionError("per-bar segment callback used")
        ),
    )

    five = cycle_sessions.aggregate_completed_bars(
        bars, minutes=5, sessions=sessions
    )
    one = cycle_sessions.aggregate_completed_bars(
        bars.iloc[:1], minutes=1, sessions=sessions
    )
    incomplete = cycle_sessions.aggregate_completed_bars(
        bars.drop(index=2), minutes=5, sessions=sessions
    )
    daily_sessions = (
        cycle_sessions.SessionSpec(
            session_id="short",
            is_night=True,
            segments=(
                cycle_sessions.SessionSegment(
                    "midnight", time(0), time(0, 5), time(0)
                ),
            ),
        ),
    )
    daily = cycle_sessions.aggregate_completed_daily_bars(
        bars, sessions=daily_sessions
    )

    assert five["bar_end"].tolist() == [
        pd.Timestamp("2026-03-03 00:05", tz=TZ)
    ]
    assert one["bar_end"].tolist() == [
        pd.Timestamp("2026-03-03 00:01", tz=TZ)
    ]
    assert incomplete.empty
    assert len(daily) == 1
    assert daily.loc[0, "open"] == 100.0
    assert daily.loc[0, "close"] == 104.5


def test_scalp_hot_paths_do_not_use_row_series_iteration() -> None:
    normalization_source = inspect.getsource(scalp_data.normalize_source_bars)
    aggregation_source = inspect.getsource(scalp_data.aggregate_completed_bars)

    assert ".iterrows(" not in normalization_source
    assert "group.iloc[" not in aggregation_source


def test_vectorized_aggregation_invalidates_p04_cache_entries() -> None:
    assert AGGREGATION_CACHE_VERSION == 2


def test_one_bucket_assignment_can_aggregate_signal_and_actual(
    monkeypatch,
) -> None:
    assert hasattr(cycle_sessions, "build_aggregation_assignments")
    sessions = (
        cycle_sessions.SessionSpec(
            session_id="day",
            is_night=False,
            segments=(
                cycle_sessions.SessionSegment("day", time(9), time(9, 5), time(9)),
            ),
        ),
    )
    actual = _bars(
        pd.date_range("2026-03-02 09:01", periods=5, freq="1min", tz=TZ)
    )
    signal = actual.copy()
    signal.loc[:, ["open", "high", "low", "close"]] += 100.0
    original = cycle_sessions._assign_segments
    calls = 0

    def counted(*args, **kwargs):
        nonlocal calls
        calls += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(cycle_sessions, "_assign_segments", counted)
    assignments = cycle_sessions.build_aggregation_assignments(
        actual, minutes=5, sessions=sessions
    )
    actual_result = cycle_sessions.aggregate_completed_bars(
        actual,
        minutes=5,
        sessions=sessions,
        assignments=assignments,
    )
    signal_result = cycle_sessions.aggregate_completed_bars(
        signal,
        minutes=5,
        sessions=sessions,
        assignments=assignments,
    )

    assert calls == 1
    assert actual_result.loc[0, "open"] == 100.0
    assert signal_result.loc[0, "open"] == 200.0
