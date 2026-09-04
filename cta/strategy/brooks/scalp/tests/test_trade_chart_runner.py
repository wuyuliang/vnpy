from __future__ import annotations

from pathlib import Path

import pandas as pd
from PIL import Image

from cta.strategy.brooks.scalp.report import (
    _candle_position,
    _resample_ohlcv,
    _trade_source_window,
    plot_trade_timeframes,
)
from cta.strategy.brooks.scalp.trade_chart_runner import normalize_rb_chart_bars


def _exchange_calendar() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "exchange": ["SHFE", "SHFE", "SHFE"],
            "exchange_trade_date": ["2026-01-02", "2026-01-05", "2026-01-06"],
            "is_open": [1, 1, 1],
            "next_open_date": ["2026-01-05", "2026-01-06", "2026-01-07"],
        }
    )


def test_normalize_rb_chart_bars_keeps_only_completed_minutes_in_each_segment() -> None:
    raw = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                [
                    "2026-01-05 09:00",
                    "2026-01-05 09:01",
                    "2026-01-05 10:15",
                    "2026-01-05 10:30",
                    "2026-01-05 10:31",
                    "2026-01-05 11:30",
                    "2026-01-05 13:30",
                    "2026-01-05 13:31",
                    "2026-01-05 15:00",
                    "2026-01-05 21:00",
                    "2026-01-05 21:01",
                    "2026-01-05 23:00",
                ]
            ),
            "open": 100.0,
            "high": 101.0,
            "low": 99.0,
            "close": 100.5,
            "volume": 10.0,
            "ts_code": "RB2605.SHF",
        }
    )

    bars = normalize_rb_chart_bars(raw, _exchange_calendar())

    assert bars["bar_end"].dt.strftime("%H:%M").tolist() == [
        "09:01",
        "10:15",
        "10:31",
        "11:30",
        "13:31",
        "15:00",
        "21:01",
        "23:00",
    ]
    assert bars["segment_id"].tolist() == [
        "day_1",
        "day_1",
        "day_2",
        "day_2",
        "day_3",
        "day_3",
        "night_continuous",
        "night_continuous",
    ]
    assert bars["segment_start"].dt.strftime("%H:%M").tolist() == [
        "09:00",
        "09:00",
        "10:30",
        "10:30",
        "13:30",
        "13:30",
        "21:00",
        "21:00",
    ]
    assert bars["exchange_trade_date"].astype(str).tolist() == [
        "2026-01-05",
        "2026-01-05",
        "2026-01-05",
        "2026-01-05",
        "2026-01-05",
        "2026-01-05",
        "2026-01-06",
        "2026-01-06",
    ]


def test_daily_chart_combines_prior_evening_with_next_open_trade_date() -> None:
    raw = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                [
                    "2026-01-02 21:01",
                    "2026-01-02 23:00",
                    "2026-01-05 09:01",
                    "2026-01-05 15:00",
                    "2026-01-05 21:01",
                    "2026-01-05 23:00",
                    "2026-01-06 09:01",
                    "2026-01-06 15:00",
                ]
            ),
            "open": [100.0, 101.0, 102.0, 103.0, 110.0, 111.0, 112.0, 113.0],
            "high": [102.0, 104.0, 105.0, 106.0, 112.0, 114.0, 115.0, 116.0],
            "low": [99.0, 100.0, 101.0, 102.0, 109.0, 110.0, 111.0, 112.0],
            "close": [101.0, 103.0, 104.0, 105.0, 111.0, 113.0, 114.0, 115.0],
            "volume": [1.0, 2.0, 3.0, 4.0, 5.0, 6.0, 7.0, 8.0],
            "ts_code": "RB2605.SHF",
        }
    )
    bars = normalize_rb_chart_bars(raw, _exchange_calendar()).set_index("bar_end")

    daily = _resample_ohlcv(bars, "day")

    assert daily.index.strftime("%Y-%m-%d %H:%M").tolist() == [
        "2026-01-05 15:00",
        "2026-01-06 15:00",
    ]
    first = daily.iloc[0]
    assert first["open"] == 100.0
    assert first["high"] == 106.0
    assert first["low"] == 99.0
    assert first["close"] == 105.0
    assert first["volume"] == 10.0


def test_plot_trade_timeframes_outputs_four_panel_png_around_entry_and_exit(
    tmp_path: Path,
) -> None:
    bar_end = pd.date_range(
        "2026-01-05 09:01",
        periods=180,
        freq="min",
        tz="Asia/Shanghai",
    )
    bars = pd.DataFrame(
        {
            "bar_end": bar_end,
            "open": 100.0,
            "high": 102.0,
            "low": 99.0,
            "close": 101.0,
            "volume": range(1, 181),
            "exchange_trade_date": pd.Timestamp("2026-01-05"),
            "session_id": "20260105:day",
            "segment_id": "day_1",
            "segment_start": pd.Timestamp("2026-01-05 09:00", tz="Asia/Shanghai"),
        }
    )
    trade = pd.Series(
        {
            "trade_id": "trade-test-1",
            "contract_code": "RB2605.SHF",
            "direction": "LONG",
            "rule_id": "strong_breakout_follow_through",
            "entry_time": "2026-01-05 09:51:00+08:00",
            "entry_price": 101.0,
            "exit_time": "2026-01-05 10:06:00+08:00",
            "exit_price": 100.0,
            "exit_reason": "no_follow_through_scratch",
            "net_pnl": -25.0,
            "net_r": -0.1,
            "exchange_trade_date": "2026-01-05",
        }
    )
    output = tmp_path / "trade.png"

    plot_trade_timeframes(bars, trade, output, sequence=1)

    assert output.read_bytes().startswith(b"\x89PNG")
    with Image.open(output) as image:
        assert image.size == (1680, 1120)


def test_trade_source_window_bounds_work_without_losing_entry_or_exit() -> None:
    index = pd.date_range(
        "2026-01-01 09:01",
        periods=2_000,
        freq="min",
        tz="Asia/Shanghai",
    )
    bars = pd.DataFrame({"close": 100.0}, index=index)
    entry_time = index[1_000]
    exit_time = index[1_025]

    window = _trade_source_window(
        bars,
        entry_time=entry_time,
        exit_time=exit_time,
        before=600,
        after=400,
    )

    assert len(window) == 1_026
    assert window.index[0] == index[400]
    assert window.index[-1] == index[1_425]
    assert entry_time in window.index
    assert exit_time in window.index


def test_trade_marker_uses_the_candle_that_ends_after_the_fill() -> None:
    index = pd.date_range(
        "2026-01-05 09:35",
        periods=6,
        freq="5min",
        tz="Asia/Shanghai",
    )

    assert _candle_position(index, pd.Timestamp("2026-01-05 09:51", tz="Asia/Shanghai")) == 4
    assert _candle_position(index, pd.Timestamp("2026-01-05 09:50", tz="Asia/Shanghai")) == 3
