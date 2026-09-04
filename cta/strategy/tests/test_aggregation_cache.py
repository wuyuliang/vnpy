from __future__ import annotations

from datetime import date, time
from importlib import import_module, util
from pathlib import Path

import pandas as pd

from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
    aggregate_completed_bars,
    aggregate_completed_daily_bars,
)


TZ = "Asia/Shanghai"


def _cache_api() -> tuple[object, object, object, int]:
    module_name = "cta.strategy.multi_timeframe_trend_backtest.aggregation_cache"
    assert util.find_spec(module_name) is not None, "aggregation cache module is missing"
    module = import_module(module_name)
    return (
        module.AggregationCache,
        module.cached_aggregate_completed_bars,
        module.cached_aggregate_completed_daily_bars,
        module.AGGREGATION_CACHE_VERSION,
    )


def _sessions(*, segment_id: str = "day") -> tuple[SessionSpec, ...]:
    return (
        SessionSpec(
            session_id="DAY",
            is_night=False,
            segments=(
                SessionSegment(segment_id, time(9), time(9, 5), time(9)),
            ),
        ),
    )


def _minute_bars() -> pd.DataFrame:
    ends = pd.date_range("2026-03-02 09:01", periods=5, freq="1min", tz=TZ)
    return pd.DataFrame(
        {
            "bar_end": ends,
            "feature_sequence": range(5),
            "open": [100.0, 101.0, 102.0, 103.0, 104.0],
            "high": [101.0, 102.0, 103.0, 104.0, 105.0],
            "low": [99.0, 100.0, 101.0, 102.0, 103.0],
            "close": [100.5, 101.5, 102.5, 103.5, 104.5],
            "volume": [1.0, 2.0, 3.0, 4.0, 5.0],
            "turnover": [10.0, 20.0, 30.0, 40.0, 50.0],
            "open_interest": [10.0, 11.0, 12.0, 13.0, 14.0],
            "contract_code": ["BR2604.SHF"] * 5,
            "exchange_trade_date": [date(2026, 3, 2)] * 5,
        }
    )


def _cached_intraday(cache: object, frame: pd.DataFrame, **kwargs: object) -> pd.DataFrame:
    _, cached_bars, _, _ = _cache_api()
    return cached_bars(
        cache,
        frame,
        minutes=kwargs.get("minutes", 5),
        sessions=kwargs.get("sessions", _sessions()),
        compute=aggregate_completed_bars,
    )


def test_second_identical_aggregation_hits_disk_cache(tmp_path: Path) -> None:
    cache_type, _, _, _ = _cache_api()
    cache = cache_type(tmp_path)

    first = _cached_intraday(cache, _minute_bars())
    second = _cached_intraday(cache, _minute_bars())

    pd.testing.assert_frame_equal(first, second)
    assert cache.stats == {"hits": 1, "misses": 1, "errors": 0}


def test_cache_key_covers_interval_sessions_and_input_content(tmp_path: Path) -> None:
    cache_type, _, _, _ = _cache_api()
    cache = cache_type(tmp_path)
    bars = _minute_bars()

    _cached_intraday(cache, bars)
    _cached_intraday(cache, bars, minutes=1)
    _cached_intraday(cache, bars, sessions=_sessions(segment_id="renamed"))
    changed = bars.copy()
    changed.loc[0, "open"] = 999.0
    _cached_intraday(cache, changed)

    assert cache.stats == {"hits": 0, "misses": 4, "errors": 0}


def test_cache_version_change_does_not_hit_old_entry(tmp_path: Path) -> None:
    cache_type, _, _, version = _cache_api()
    _cached_intraday(cache_type(tmp_path, version=version), _minute_bars())
    newer = cache_type(tmp_path, version=version + 1)

    _cached_intraday(newer, _minute_bars())

    assert newer.stats == {"hits": 0, "misses": 1, "errors": 0}


def test_unwritable_cache_path_falls_back_to_computation(tmp_path: Path) -> None:
    cache_type, _, _, _ = _cache_api()
    root_file = tmp_path / "not-a-directory"
    root_file.write_text("occupied", encoding="utf-8")
    cache = cache_type(root_file)

    actual = _cached_intraday(cache, _minute_bars())
    expected = aggregate_completed_bars(
        _minute_bars(), minutes=5, sessions=_sessions()
    )

    pd.testing.assert_frame_equal(actual, expected)
    assert cache.stats == {"hits": 0, "misses": 1, "errors": 1}


def test_corrupt_cache_entry_is_recomputed(tmp_path: Path) -> None:
    cache_type, _, _, _ = _cache_api()
    _cached_intraday(cache_type(tmp_path), _minute_bars())
    cache_file = next(tmp_path.rglob("*.parquet"))
    cache_file.write_bytes(b"not parquet")
    cache = cache_type(tmp_path)

    actual = _cached_intraday(cache, _minute_bars())
    expected = aggregate_completed_bars(
        _minute_bars(), minutes=5, sessions=_sessions()
    )

    pd.testing.assert_frame_equal(actual, expected)
    assert cache.stats == {"hits": 0, "misses": 1, "errors": 1}


def test_daily_aggregation_uses_the_same_cache_contract(tmp_path: Path) -> None:
    cache_type, _, cached_daily, _ = _cache_api()
    cache = cache_type(tmp_path)
    bars = _minute_bars()

    first = cached_daily(
        cache,
        bars,
        sessions=_sessions(),
        compute=aggregate_completed_daily_bars,
    )
    second = cached_daily(
        cache,
        bars,
        sessions=_sessions(),
        compute=aggregate_completed_daily_bars,
    )

    pd.testing.assert_frame_equal(first, second)
    assert cache.stats == {"hits": 1, "misses": 1, "errors": 0}


def test_runner_enables_cache_by_default_and_records_explicit_disable() -> None:
    from cta.strategy.multi_timeframe_trend_backtest.runner import (
        build_parser,
        build_reproduction_command,
    )

    parser = build_parser()
    enabled = parser.parse_args(["--start", "2026-03-01", "--end", "2026-04-01"])
    disabled = parser.parse_args(
        [
            "--start",
            "2026-03-01",
            "--end",
            "2026-04-01",
            "--aggregation-cache-root",
            "",
        ]
    )

    assert enabled.aggregation_cache_root.endswith("aggregated_cache")
    assert disabled.aggregation_cache_root == ""
    assert build_reproduction_command(disabled)["argv"][-2:] == [
        "--aggregation-cache-root",
        "",
    ]
