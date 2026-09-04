from __future__ import annotations

from dataclasses import replace
from datetime import date, time

import numpy as np
import pandas as pd

from cta.strategy.brooks.cycle_v1.backtest.data_loader import LoadedSymbol
from cta.strategy.brooks.cycle_v1.backtest.scanner import (
    build_symbol_replay_frames,
    scan_loaded_symbols,
)
from cta.strategy.brooks.cycle_v1.backtest.timeframes import TimeframeSet
from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
)


def _loaded_symbol() -> LoadedSymbol:
    rows: list[dict[str, object]] = []
    trade_dates = pd.date_range("2026-01-05", periods=10, freq="B")
    sequence = 0
    for day_number, trade_day in enumerate(trade_dates):
        close_base = 100.0 + day_number
        for minute in range(1, 61):
            bar_end = pd.Timestamp(
                trade_day.strftime("%Y-%m-%d") + " 09:00",
                tz="Asia/Shanghai",
            ) + pd.Timedelta(minutes=minute)
            close = close_base + minute / 1_000
            adjustment_known_at = pd.Timestamp(
                "2026-01-05 09:00", tz="Asia/Shanghai"
            )
            rows.append(
                {
                    "bar_end": bar_end,
                    "feature_sequence": sequence,
                    "open": close - 0.05,
                    "high": close + 0.10,
                    "low": close - 0.10,
                    "close": close,
                    "volume": 100.0 + minute,
                    "turnover": close * (100.0 + minute),
                    "open_interest": 1_000.0,
                    "contract_code": "RB2605.SHF",
                    "exchange_trade_date": trade_day.date(),
                    "price_tick": 1.0,
                    "base_round_trip_cost_price": 0.2,
                    "signal_open": close - 0.05,
                    "signal_high": close + 0.10,
                    "signal_low": close - 0.10,
                    "signal_close": close,
                    "adjustment_scale": 1.0,
                    "adjustment_offset": 0.0,
                    "adjustment_known_at": adjustment_known_at,
                    "adjustment_version": "RB:UNADJUSTED",
                    "adjustment_source": "INITIAL_RAW_CONTRACT",
                }
            )
            sequence += 1
    return LoadedSymbol(
        root_symbol="RB",
        exchange="SHFE",
        vt_symbol="RB0.SHFE",
        minute_bars=pd.DataFrame(rows),
        sessions=(
            SessionSpec(
                session_id="day",
                is_night=False,
                segments=(SessionSegment("continuous", time(9), time(10), time(9)),),
            ),
        ),
        source_files=(),
    )


def _small_config():
    config = load_config()
    return replace(
        config,
        features=replace(
            config.features,
            atr_period=2,
            adx_period=2,
            atr_compression_window=2,
            momentum_window=2,
            ema_fast=2,
            ema_slow=3,
            ema_slope_lookback=1,
            structure_window=3,
            percentile_lookback=2,
            pivot_left=1,
            pivot_right=1,
            structure_pivot_count=2,
            volume_bucket_lookback=2,
        ),
        cycle=replace(
            config.cycle,
            bo_recent_window=2,
            channel_min_bars=2,
            tight_ema_cross_window=2,
            range_window=3,
            confirm_bars=1,
            min_state_bars=1,
        ),
    )


def test_scanner_filters_after_features_and_aligns_completed_cycles() -> None:
    artifacts = scan_loaded_symbols(
        (_loaded_symbol(),),
        config=_small_config(),
        timeframes=TimeframeSet.from_values("15min", "5min", "1min"),
        start=date(2026, 1, 5),
        end=date(2026, 1, 16),
    )

    assert artifacts.universe_daily["eligible"].sum() == 5
    assert not artifacts.cycle_snapshots.empty
    assert artifacts.cycle_snapshots["exchange_trade_date"].min() == date(2026, 1, 12)
    assert (
        artifacts.cycle_snapshots["ema_asof_trade_date"].max()
        < artifacts.cycle_snapshots["exchange_trade_date"].max()
    )
    assert {
        "short_cycle",
        "medium_cycle",
        "long_cycle",
        "short_feature_asof",
        "medium_feature_asof",
        "long_feature_asof",
    }.issubset(artifacts.cycle_snapshots)
    assert (
        artifacts.cycle_snapshots["medium_feature_asof"]
        <= artifacts.cycle_snapshots["short_feature_asof"]
    ).all()
    assert (
        artifacts.cycle_snapshots["long_feature_asof"]
        <= artifacts.cycle_snapshots["short_feature_asof"]
    ).all()
    assert np.isfinite(artifacts.cycle_snapshots["ema5"]).all()
    assert set(artifacts.replay_frames) == {"RB"}
    assert not artifacts.replay_frames["RB"].short.empty


def test_scanner_empty_result_keeps_machine_readable_schema() -> None:
    loaded = _loaded_symbol()
    loaded.minute_bars["close"] = 100.0
    artifacts = scan_loaded_symbols(
        (loaded,),
        config=_small_config(),
        timeframes=TimeframeSet.from_values("15min", "5min", "1min"),
        start=date(2026, 1, 5),
        end=date(2026, 1, 16),
    )

    assert artifacts.cycle_snapshots.empty
    assert {
        "root_symbol",
        "exchange_trade_date",
        "contract_code",
        "short_cycle",
        "medium_cycle",
        "long_cycle",
    }.issubset(artifacts.cycle_snapshots)


def test_replay_frames_keep_causal_features_and_completed_higher_cycles() -> None:
    frames = build_symbol_replay_frames(
        _loaded_symbol(),
        config=_small_config(),
        timeframes=TimeframeSet.from_values("15min", "5min", "1min"),
    )

    assert {
        "open",
        "high",
        "low",
        "close",
        "volume",
        "atr",
        "range_high",
        "range_low",
        "cycle",
        "direction",
        "feature_asof",
    }.issubset(frames.short)
    assert not frames.short.empty
    assert not frames.medium.empty
    assert not frames.long.empty
    aligned = pd.merge_asof(
        frames.short.sort_values("feature_asof"),
        frames.medium[["contract_code", "feature_asof"]]
        .rename(columns={"feature_asof": "medium_feature_asof"})
        .sort_values("medium_feature_asof"),
        left_on="feature_asof",
        right_on="medium_feature_asof",
        by="contract_code",
        direction="backward",
    )
    assert (
        aligned["medium_feature_asof"].dropna()
        <= aligned.loc[aligned["medium_feature_asof"].notna(), "feature_asof"]
    ).all()


def test_cycle_history_continues_across_actual_contract_roll() -> None:
    loaded = _loaded_symbol()
    roll_at = len(loaded.minute_bars) // 2
    price_columns = ["open", "high", "low", "close"]
    positions = np.arange(len(loaded.minute_bars), dtype=float)
    signal_close = 100.0 + positions * 0.01 + np.sin(positions * np.pi / 5.0)
    loaded.minute_bars["signal_open"] = signal_close - 0.05
    loaded.minute_bars["signal_high"] = signal_close + 0.20
    loaded.minute_bars["signal_low"] = signal_close - 0.20
    loaded.minute_bars["signal_close"] = signal_close
    loaded.minute_bars[price_columns] = loaded.minute_bars[
        ["signal_open", "signal_high", "signal_low", "signal_close"]
    ].to_numpy()
    loaded.minute_bars.loc[roll_at:, "contract_code"] = "RB2610.SHF"
    loaded.minute_bars.loc[roll_at:, price_columns] += 20.0
    loaded.minute_bars.loc[roll_at:, "adjustment_offset"] = -20.0
    loaded.minute_bars.loc[roll_at:, "adjustment_version"] = "RB-ROLL-V1"
    config = _small_config()
    config = replace(
        config,
        features=replace(config.features, percentile_lookback=80),
    )

    frames = build_symbol_replay_frames(
        loaded,
        config=config,
        timeframes=TimeframeSet.from_values("3min", "2min", "1min"),
    )
    post_roll = frames.long.loc[
        frames.long["contract_code"].eq("RB2610.SHF")
    ]

    assert post_roll["reason"].eq("CLASSIFIED").any()
    assert post_roll["adjustment_offset"].eq(-20.0).all()
    assert post_roll["close"].lt(200.0).all()


def test_scanner_keeps_effective_dated_price_tick_changes() -> None:
    original = _loaded_symbol()
    minute_bars = original.minute_bars.copy()
    change_at = len(minute_bars) // 2
    minute_bars.loc[: change_at - 1, "price_tick"] = 2.0
    minute_bars.loc[change_at:, "price_tick"] = 1.0
    loaded = replace(
        original,
        root_symbol="Y",
        exchange="DCE",
        vt_symbol="Y0.DCE",
        minute_bars=minute_bars,
    )

    frames = build_symbol_replay_frames(
        loaded,
        config=_small_config(),
        timeframes=TimeframeSet.from_values("3min", "2min", "1min"),
    )

    assert set(frames.short["price_tick"]) == {1.0, 2.0}
