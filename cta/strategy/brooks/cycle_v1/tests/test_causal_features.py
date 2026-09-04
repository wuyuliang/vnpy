from __future__ import annotations

from dataclasses import replace

import numpy as np
import pandas as pd

from cta.strategy.brooks.cycle_v1.config import load_config
from cta.strategy.brooks.cycle_v1.core.features.causal import (
    add_causal_features,
    confirmed_pivots,
)


TZ = "Asia/Shanghai"


def _bars(count: int = 320) -> pd.DataFrame:
    end = pd.date_range("2025-01-02 09:05", periods=count, freq="5min", tz=TZ)
    trend = np.linspace(100.0, 130.0, count)
    wave = np.sin(np.arange(count) / 3.0)
    close = trend + wave
    return pd.DataFrame(
        {
            "bar_end": end,
            "feature_sequence": np.arange(count) * 10,
            "contract_code": "RB2605.SHF",
            "open": close - np.cos(np.arange(count)) * 0.3,
            "high": close + 0.8,
            "low": close - 0.9,
            "close": close,
            "volume": 100 + np.arange(count) % 20,
            "open_interest": 10_000.0,
            "session_bucket": np.arange(count) % 48,
        }
    )


def test_features_use_frozen_boundaries_and_are_prefix_invariant() -> None:
    base = load_config()
    config = replace(base, features=replace(base.features, percentile_lookback=30))
    bars = _bars()
    full = add_causal_features(bars, config, price_tick=1.0)

    expected_prior_high = bars.loc[:19, "high"].max()
    assert full.loc[20, "prior_high"] == expected_prior_high
    assert full.loc[40, "range_high"] == bars.loc[:39, "high"].max()

    for end in (80, 157, 250):
        prefix = add_causal_features(bars.iloc[:end], config, price_tick=1.0)
        pd.testing.assert_frame_equal(
            prefix.reset_index(drop=True),
            full.iloc[:end].reset_index(drop=True),
            check_dtype=False,
            rtol=1e-12,
            atol=1e-12,
        )


def test_features_use_point_in_time_tick_series_without_rewriting_prefix() -> None:
    base = load_config()
    config = replace(base, features=replace(base.features, percentile_lookback=30))
    bars = _bars()
    ticks = pd.Series(2.0, index=bars.index)
    ticks.loc[160:] = 1.0

    full = add_causal_features(bars, config, price_tick=ticks)
    prefix = add_causal_features(
        bars.iloc[:160],
        config,
        price_tick=ticks.iloc[:160],
    )

    pd.testing.assert_frame_equal(
        prefix.reset_index(drop=True),
        full.iloc[:160].reset_index(drop=True),
        check_dtype=False,
        rtol=1e-12,
        atol=1e-12,
    )


def test_confirmed_pivot_is_visible_only_at_right_confirmation_bar() -> None:
    frame = pd.DataFrame(
        {
            "bar_end": pd.date_range("2026-01-05 09:05", periods=5, freq="5min", tz=TZ),
            "high": [1.0, 2.0, 5.0, 2.0, 1.0],
            "low": [0.0, 0.5, 1.0, 0.5, 0.0],
        }
    )

    pivots = confirmed_pivots(frame, left=2, right=2)

    high = pivots.loc[pivots["kind"] == "HIGH"].iloc[0]
    assert high["pivot_bar_end"] == frame.loc[2, "bar_end"]
    assert high["known_at"] == frame.loc[4, "bar_end"]


def test_adx_auxiliary_gate_and_bear_channel_slope_follow_frozen_formulas() -> None:
    base = load_config()
    config = replace(base, features=replace(base.features, percentile_lookback=30))
    bars = _bars()
    features = add_causal_features(bars, config, price_tick=1.0)

    assert features["adx"].notna().sum() > 0
    available = features["adx_percentile"].notna() & features[
        "atr_compression_percentile"
    ].notna()
    expected_gate = (
        features["adx_percentile"].le(config.cycle.range_max_adx_percentile)
        & features["atr_compression_percentile"].le(
            config.cycle.range_max_atr_compression_percentile
        )
    )
    pd.testing.assert_series_equal(
        features.loc[available, "range_aux_gate"].astype(bool),
        expected_gate.loc[available],
        check_names=False,
    )

    falling = bars.copy()
    falling_close = np.linspace(130.0, 100.0, len(falling)) + np.sin(np.arange(len(falling)) / 3)
    falling["open"] = falling_close + 0.2
    falling["high"] = falling_close + 0.8
    falling["low"] = falling_close - 0.9
    falling["close"] = falling_close
    falling_features = add_causal_features(falling, config, price_tick=1.0)
    assert falling_features.iloc[-1]["bear_channel_slope_atr"] < 0


def test_range_width_uses_explicit_point_in_time_round_trip_cost() -> None:
    base = load_config()
    config = replace(base, features=replace(base.features, percentile_lookback=30))
    bars = _bars(100)
    costs = pd.Series(np.linspace(1.0, 2.0, len(bars)), index=bars.index)

    features = add_causal_features(
        bars,
        config,
        price_tick=1.0,
        round_trip_cost_price=costs,
    )

    expected = (
        features.loc[60, "range_high"] - features.loc[60, "range_low"]
    ) / costs.loc[60]
    assert features.loc[60, "range_width_cost_multiple"] == expected

    prefix = add_causal_features(
        bars.iloc[:80],
        config,
        price_tick=1.0,
        round_trip_cost_price=costs.iloc[:80],
    )
    pd.testing.assert_series_equal(
        prefix["range_width_cost_multiple"],
        features.iloc[:80]["range_width_cost_multiple"],
    )
