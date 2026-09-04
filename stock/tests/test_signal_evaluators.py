from dataclasses import dataclass

import pandas as pd

from stock.strategy.signal_evaluators import (
    BullPullbackConfig,
    evaluate_breakout_pullback_continuation,
    evaluate_bull_pullback_continuation,
    evaluate_volume_spike_up,
    scan_breakout_pullback_continuation,
    scan_bull_pullback_continuation,
    scan_volume_spike_up,
    scan_stock_signal_opportunities,
)


@dataclass
class _Bar:
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume: float


def _bars(close_values: list[float], volumes: list[float]) -> list[_Bar]:
    return [
        _Bar(
            open_price=close - 0.2,
            high_price=close + 0.8,
            low_price=close - 0.8,
            close_price=close,
            volume=volume,
        )
        for close, volume in zip(close_values, volumes, strict=True)
    ]


def _signal_frame(volumes: list[float]) -> pd.DataFrame:
    close_values = [
        100.0,
        101.0,
        102.0,
        103.0,
        104.0,
        105.0,
        106.0,
        107.0,
        108.0,
        109.0,
        110.0,
        111.0,
        112.0,
        113.0,
        114.0,
        115.0,
        116.0,
        117.0,
        118.0,
        119.0,
        118.0,
        118.5,
    ]
    return pd.DataFrame(
        {
            "symbol": ["600519.SH"] * len(close_values),
            "exchange": ["SSE"] * len(close_values),
            "interval": ["d"] * len(close_values),
            "datetime": pd.date_range("2024-01-02", periods=len(close_values), freq="B").strftime("%Y-%m-%d 00:00:00"),
            "open": [value - 0.2 for value in close_values],
            "high": [value + 0.8 for value in close_values],
            "low": [value - 0.8 for value in close_values],
            "close": close_values,
            "volume": volumes,
            "open_interest": [0.0] * len(close_values),
            "turnover": [1.0] * len(close_values),
        }
    )


def _breakout_pullback_frame() -> pd.DataFrame:
    history_closes = [110.0] * 6
    close_values = history_closes + [100.0, 101.0, 102.0, 103.0, 104.0, 107.8, 106.0, 105.8, 107.5, 108.0]
    high_values = [240.0] * 6 + [101.0, 102.0, 103.0, 104.0, 105.0, 108.2, 106.7, 106.4, 107.8, 108.4]
    low_values = [109.0] * 6 + [99.0, 100.0, 101.0, 102.0, 103.0, 105.4, 104.8, 105.0, 106.6, 107.2]
    return pd.DataFrame(
        {
            "symbol": ["600519.SH"] * len(close_values),
            "exchange": ["SSE"] * len(close_values),
            "interval": ["d"] * len(close_values),
            "datetime": pd.date_range("2024-01-02", periods=len(close_values), freq="B").strftime("%Y-%m-%d 00:00:00"),
            "open": [value - 0.2 for value in close_values],
            "high": high_values,
            "low": low_values,
            "close": close_values,
            "volume": [1000.0] * len(close_values),
            "open_interest": [0.0] * len(close_values),
            "turnover": [1.0] * len(close_values),
        }
    )


def _volume_spike_frame() -> pd.DataFrame:
    close_values = [240.0, 100.5, 101.0, 100.8, 101.2, 102.0, 102.6]
    return pd.DataFrame(
        {
            "symbol": ["300750.SZ"] * len(close_values),
            "exchange": ["SZSE"] * len(close_values),
            "interval": ["d"] * len(close_values),
            "datetime": pd.date_range("2024-02-01", periods=len(close_values), freq="B").strftime("%Y-%m-%d 00:00:00"),
            "open": [value - 0.2 for value in close_values],
            "high": [value + 0.8 for value in close_values],
            "low": [value - 0.8 for value in close_values],
            "close": close_values,
            "volume": [1000.0, 1100.0, 900.0, 1000.0, 1000.0, 2200.0, 1200.0],
            "pct_chg": [0.0, 0.5, 0.5, -0.2, 0.4, 0.8, 0.5],
            "open_interest": [0.0] * len(close_values),
            "turnover": [1.0] * len(close_values),
        }
    )


def test_evaluate_bull_pullback_continuation_requires_ema_stack_and_volume_confirmation() -> None:
    cfg = BullPullbackConfig(pullback_pct=0.08)
    bars = [
        *_bars([float(100 + index) for index in range(20)], [1000.0] * 20),
        _Bar(117.8, 118.8, 117.0, 118.0, 2500.0),
    ]

    decision = evaluate_bull_pullback_continuation(bars, cfg)

    assert decision is not None
    assert decision["side"] == "long_open"
    assert decision["trigger"] == "bull_pullback_long"
    assert decision["ema5"] >= decision["ema10"] >= decision["ema20"]
    assert decision["volume_condition"] == "latest_volume_doubled"


def test_evaluate_bull_pullback_continuation_rejects_weak_volume() -> None:
    cfg = BullPullbackConfig(pullback_pct=0.08)
    bars = [
        *_bars([float(100 + index) for index in range(20)], [1000.0] * 20),
        _Bar(117.8, 118.8, 117.0, 118.0, 1200.0),
    ]

    assert evaluate_bull_pullback_continuation(bars, cfg) is None


def test_evaluate_bull_pullback_continuation_rejects_broken_ema_stack() -> None:
    cfg = BullPullbackConfig(pullback_pct=0.08)
    bars = [
        *_bars([float(120 - index) for index in range(20)], [1000.0] * 20),
        _Bar(102.8, 103.8, 102.0, 103.0, 2500.0),
    ]

    assert evaluate_bull_pullback_continuation(bars, cfg) is None


def test_scan_bull_pullback_continuation_emits_buy_only_next_day_opportunities() -> None:
    volumes = [1000.0] * 20 + [2500.0, 1100.0]
    frame = _signal_frame(volumes)

    result = scan_bull_pullback_continuation(
        frame,
        BullPullbackConfig(pullback_pct=0.08),
    )

    assert not result.empty
    assert not any(column.startswith("exit_") for column in result.columns)
    assert {"symbol", "exchange", "signal_type", "signal_datetime", "opportunity_date", "entry_action", "close_price"}.issubset(result.columns)
    assert set(result["signal_type"]) == {"bull_pullback_continuation"}
    assert set(result["entry_action"]) == {"buy"}
    assert result.iloc[0]["opportunity_date"] > result.iloc[0]["signal_datetime"]


def test_evaluate_breakout_pullback_continuation_returns_long_signal_after_reclaim() -> None:
    cfg = BullPullbackConfig(
        breakout_window=5,
        breakout_pullback_lookback=3,
        breakout_pullback_pct=0.02,
        breakout_reclaim_pct=0.01,
    )
    frame = _breakout_pullback_frame()
    bars = [
        _Bar(
            open_price=float(row.open),
            high_price=float(row.high),
            low_price=float(row.low),
            close_price=float(row.close),
            volume=float(row.volume),
        )
        for row in frame.iloc[:-1].itertuples(index=False)
    ]

    decision = evaluate_breakout_pullback_continuation(bars, cfg)

    assert decision is not None
    assert decision["side"] == "long_open"
    assert decision["trigger"] == "breakout_pullback_long"
    assert decision["breakout_level"] == 105.0
    assert decision["pullback_low"] == 104.8
    assert decision["lookback_high_18m"] == 240.0
    assert decision["close_to_lookback_high"] < 0.5


def test_evaluate_breakout_pullback_continuation_rejects_price_above_half_lookback_high() -> None:
    cfg = BullPullbackConfig(
        breakout_window=5,
        breakout_pullback_lookback=3,
        breakout_pullback_pct=0.02,
        breakout_reclaim_pct=0.01,
    )
    frame = _breakout_pullback_frame()
    frame.loc[:5, "high"] = 200.0
    bars = [
        _Bar(
            open_price=float(row.open),
            high_price=float(row.high),
            low_price=float(row.low),
            close_price=float(row.close),
            volume=float(row.volume),
        )
        for row in frame.iloc[:-1].itertuples(index=False)
    ]

    assert evaluate_breakout_pullback_continuation(bars, cfg) is None


def test_scan_breakout_pullback_continuation_emits_next_day_opportunity() -> None:
    cfg = BullPullbackConfig(
        breakout_window=5,
        breakout_pullback_lookback=3,
        breakout_pullback_pct=0.02,
        breakout_reclaim_pct=0.01,
    )

    result = scan_breakout_pullback_continuation(_breakout_pullback_frame(), cfg)

    assert not result.empty
    assert set(result["signal_type"]) == {"breakout_pullback_continuation"}
    assert set(result["entry_action"]) == {"buy"}
    assert result.iloc[0]["opportunity_date"] > result.iloc[0]["signal_datetime"]
    assert result.iloc[0]["breakout_level"] == 105.0
    assert result.iloc[0]["pullback_low"] == 104.8
    assert result.iloc[0]["lookback_high_18m"] == 240.0
    assert result.iloc[0]["close_to_lookback_high"] < 0.5


def test_evaluate_volume_spike_up_returns_long_signal_for_up_day_double_five_day_volume() -> None:
    cfg = BullPullbackConfig(volume_spike_window=5, volume_spike_ratio=2.0)
    frame = _volume_spike_frame()
    bars = [
        _Bar(
            open_price=float(row.open),
            high_price=float(row.high),
            low_price=float(row.low),
            close_price=float(row.close),
            volume=float(row.volume),
        )
        for row in frame.iloc[:6].itertuples(index=False)
    ]

    decision = evaluate_volume_spike_up(bars, cfg, pct_chg=float(frame.iloc[5]["pct_chg"]))

    assert decision is not None
    assert decision["side"] == "long_open"
    assert decision["trigger"] == "volume_spike_up_long"
    assert decision["volume_condition"] == "latest_volume_vs_five_day_avg"
    assert decision["volume_5_avg"] == 1000.0


def test_evaluate_volume_spike_up_rejects_down_day_even_with_double_volume() -> None:
    cfg = BullPullbackConfig(volume_spike_window=5, volume_spike_ratio=2.0)
    bars = _bars([240.0, 100.2, 100.4, 100.6, 100.8, 100.5], [1000.0, 1100.0, 900.0, 1000.0, 1000.0, 2200.0])

    assert evaluate_volume_spike_up(bars, cfg, pct_chg=-0.3) is None


def test_evaluate_volume_spike_up_rejects_price_above_half_lookback_high() -> None:
    cfg = BullPullbackConfig(volume_spike_window=5, volume_spike_ratio=2.0)
    bars = _bars([100.0, 100.2, 100.4, 100.6, 100.8, 102.0], [1000.0, 1100.0, 900.0, 1000.0, 1000.0, 2200.0])

    assert evaluate_volume_spike_up(bars, cfg, pct_chg=0.8) is None


def test_scan_volume_spike_up_emits_next_day_opportunity() -> None:
    result = scan_volume_spike_up(_volume_spike_frame(), BullPullbackConfig(volume_spike_window=5, volume_spike_ratio=2.0))

    assert not result.empty
    assert set(result["signal_type"]) == {"volume_spike_up"}
    assert set(result["entry_action"]) == {"buy"}
    assert result.iloc[0]["opportunity_date"] > result.iloc[0]["signal_datetime"]
    assert result.iloc[0]["volume_5_avg"] == 1000.0
    assert result.iloc[0]["lookback_high_18m"] > result.iloc[0]["close_price"] * 2


def test_scan_volume_spike_up_falls_back_to_close_change_when_pct_chg_is_missing() -> None:
    frame = _volume_spike_frame()
    frame.loc[5, "pct_chg"] = pd.NA

    result = scan_volume_spike_up(frame, BullPullbackConfig(volume_spike_window=5, volume_spike_ratio=2.0))

    assert not result.empty
    assert set(result["signal_type"]) == {"volume_spike_up"}


def test_scan_stock_signal_opportunities_combines_signal_types() -> None:
    bull_frame = _signal_frame([1000.0] * 20 + [2500.0, 1100.0])
    breakout_frame = _breakout_pullback_frame()
    breakout_frame["datetime"] = pd.date_range("2024-03-01", periods=len(breakout_frame), freq="B").strftime("%Y-%m-%d 00:00:00")
    volume_frame = _volume_spike_frame()
    volume_frame["datetime"] = pd.date_range("2024-05-01", periods=len(volume_frame), freq="B").strftime("%Y-%m-%d 00:00:00")
    frame = pd.concat([bull_frame, breakout_frame, volume_frame], ignore_index=True)
    cfg = BullPullbackConfig(
        pullback_pct=0.08,
        breakout_window=5,
        breakout_pullback_lookback=3,
        breakout_pullback_pct=0.02,
        breakout_reclaim_pct=0.01,
        volume_spike_window=5,
    )

    result = scan_stock_signal_opportunities(frame, cfg)

    assert {"bull_pullback_continuation", "breakout_pullback_continuation", "volume_spike_up"}.issubset(set(result["signal_type"]))
