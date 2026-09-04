from __future__ import annotations

from dataclasses import replace
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.model.feature.candidate_schema import standardize_candidate_events
from cta.strategy import multi_timeframe_trend_strategy as strategy_module
from cta.strategy.multi_timeframe_trend_rules import PullbackState, SignalCandidate, advance_pullback_state, advance_trailing_stop, assess_obstacle, attach_confirmed_pivots, build_daily_context, build_intraday_context, detect_always_in, size_for_risk, two_r_target
from cta.strategy.multi_timeframe_trend_strategy import (
    InstrumentSpec,
    generate_multi_timeframe_candidates,
)


def _bars(
    close: list[float],
    *,
    freq: str = "D",
    start: str = "2024-01-01",
    high_pad: float = 1.0,
    low_pad: float = 1.0,
) -> pd.DataFrame:
    values = np.asarray(close, dtype=float)
    return pd.DataFrame(
        {
            "bar_end": pd.date_range(start, periods=len(values), freq=freq, tz="Asia/Shanghai"),
            "open": values,
            "high": values + high_pad,
            "low": values - low_pad,
            "close": values,
            "volume": np.arange(len(values), dtype=float) + 100.0,
        }
    )


def test_config_rejects_risk_above_two_percent() -> None:
    with pytest.raises(ValueError, match="risk_per_trade"):
        MultiTimeframeTrendConfig(risk_per_trade=0.021)


def test_config_defaults_always_in_long_daily_ema_gap_min_ratio() -> None:
    config = MultiTimeframeTrendConfig()

    assert config.always_in_long_daily_ema_gap_min_ratio == pytest.approx(0.02)


@pytest.mark.parametrize(
    "value",
    [-0.01, 1.0, float("nan"), float("inf"), None, "invalid"],
)
def test_config_rejects_invalid_always_in_long_daily_ema_gap_min_ratio(
    value: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="always_in_long_daily_ema_gap_min_ratio",
    ):
        MultiTimeframeTrendConfig(always_in_long_daily_ema_gap_min_ratio=value)


def test_config_rejects_bool_always_in_long_daily_ema_gap_min_ratio() -> None:
    with pytest.raises(
        ValueError,
        match="always_in_long_daily_ema_gap_min_ratio",
    ):
        MultiTimeframeTrendConfig(always_in_long_daily_ema_gap_min_ratio=False)


def test_config_normalizes_always_in_long_daily_ema_gap_min_ratio_to_float() -> None:
    config = MultiTimeframeTrendConfig(
        always_in_long_daily_ema_gap_min_ratio="0.02"
    )

    assert type(config.always_in_long_daily_ema_gap_min_ratio) is float
    assert config.always_in_long_daily_ema_gap_min_ratio == pytest.approx(0.02)


def test_config_accepts_zero_always_in_long_daily_ema_gap_min_ratio() -> None:
    config = MultiTimeframeTrendConfig(always_in_long_daily_ema_gap_min_ratio=0.0)

    assert config.always_in_long_daily_ema_gap_min_ratio == pytest.approx(0.0)


def test_config_defaults_first_trend_entry_daily_breakout_buffer_ratio() -> None:
    config = MultiTimeframeTrendConfig()

    # 设计文档给的候选值是 0.0005，实际发布的默认值是 0.002。
    # 这里钉住"当前生效"的值：改策略参数应当是一次显式决定，不应为了让测试变绿而改。
    assert config.first_trend_entry_daily_breakout_buffer_ratio == pytest.approx(
        0.002
    )


def test_config_normalizes_first_trend_entry_daily_breakout_buffer_ratio() -> None:
    config = MultiTimeframeTrendConfig(
        first_trend_entry_daily_breakout_buffer_ratio="0.0005"
    )

    assert type(config.first_trend_entry_daily_breakout_buffer_ratio) is float
    assert config.first_trend_entry_daily_breakout_buffer_ratio == pytest.approx(0.0005)


def test_config_accepts_zero_first_trend_entry_daily_breakout_buffer_ratio() -> None:
    config = MultiTimeframeTrendConfig(
        first_trend_entry_daily_breakout_buffer_ratio=0
    )

    assert type(config.first_trend_entry_daily_breakout_buffer_ratio) is float
    assert config.first_trend_entry_daily_breakout_buffer_ratio == pytest.approx(0.0)


@pytest.mark.parametrize(
    "value",
    [False, True, -0.0001, 1.0, float("nan"), float("inf"), None, "invalid"],
)
def test_config_rejects_invalid_first_trend_entry_daily_breakout_buffer_ratio(
    value: object,
) -> None:
    with pytest.raises(
        ValueError,
        match="first_trend_entry_daily_breakout_buffer_ratio",
    ):
        MultiTimeframeTrendConfig(
            first_trend_entry_daily_breakout_buffer_ratio=value
        )


def test_config_defaults_dynamic_position_scaling() -> None:
    config = MultiTimeframeTrendConfig()

    assert config.symbol_loss_streak == 2
    assert config.symbol_position_scale == pytest.approx(1.0)
    assert config.portfolio_drawdown_threshold == pytest.approx(0.01)
    assert config.portfolio_position_scale == pytest.approx(1.0)


def test_config_defaults_symbol_loss_cooldown() -> None:
    config = MultiTimeframeTrendConfig()

    assert config.symbol_loss_cooldown_enabled is True
    assert config.symbol_loss_pair_window_hours == 48
    assert config.symbol_loss_cooldown_hours == 24


def test_config_accepts_disabled_symbol_loss_cooldown() -> None:
    config = MultiTimeframeTrendConfig(symbol_loss_cooldown_enabled=False)

    assert config.symbol_loss_cooldown_enabled is False


def test_config_accepts_one_hour_symbol_loss_cooldown_windows() -> None:
    config = MultiTimeframeTrendConfig(
        symbol_loss_pair_window_hours=1,
        symbol_loss_cooldown_hours=1,
    )

    assert config.symbol_loss_pair_window_hours == 1
    assert config.symbol_loss_cooldown_hours == 1


@pytest.mark.parametrize("value", [1, 0, "true", None])
def test_config_rejects_non_bool_symbol_loss_cooldown_enabled(
    value: object,
) -> None:
    with pytest.raises(ValueError, match="symbol_loss_cooldown_enabled"):
        MultiTimeframeTrendConfig(symbol_loss_cooldown_enabled=value)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("symbol_loss_pair_window_hours", 0),
        ("symbol_loss_pair_window_hours", -1),
        ("symbol_loss_pair_window_hours", 1.5),
        ("symbol_loss_pair_window_hours", True),
        ("symbol_loss_cooldown_hours", 0),
        ("symbol_loss_cooldown_hours", -1),
        ("symbol_loss_cooldown_hours", 1.5),
        ("symbol_loss_cooldown_hours", True),
    ],
)
def test_config_rejects_invalid_symbol_loss_cooldown_hours(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        MultiTimeframeTrendConfig(**{field: value})


def test_config_defaults_candidate_blacklist() -> None:
    config = MultiTimeframeTrendConfig()

    assert config.candidate_setup_blacklist == ("pullback_breakout",)
    assert config.candidate_direction_blacklist == (-1,)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        (
            {"candidate_setup_blacklist": ("unknown",)},
            "candidate_setup_blacklist",
        ),
        (
            {"candidate_setup_blacklist": ("always_in", "always_in")},
            "candidate_setup_blacklist",
        ),
        (
            {"candidate_direction_blacklist": (0,)},
            "candidate_direction_blacklist",
        ),
        (
            {"candidate_direction_blacklist": (-1, -1)},
            "candidate_direction_blacklist",
        ),
    ],
)
def test_config_rejects_invalid_candidate_blacklist(
    overrides: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        MultiTimeframeTrendConfig(**overrides)


@pytest.mark.parametrize(
    ("overrides", "message"),
    [
        ({"symbol_loss_streak": 0}, "symbol_loss_streak"),
        ({"symbol_position_scale": 0.0}, "symbol_position_scale"),
        ({"portfolio_drawdown_threshold": 0.0}, "portfolio_drawdown_threshold"),
        ({"portfolio_position_scale": 1.1}, "portfolio_position_scale"),
    ],
)
def test_config_rejects_invalid_dynamic_position_scaling(
    overrides: dict[str, float],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        MultiTimeframeTrendConfig(**overrides)


def test_daily_context_uses_strict_ema_order() -> None:
    config = MultiTimeframeTrendConfig(
        daily_ema_fast=2,
        daily_ema_mid=3,
        daily_ema_slow=4,
        atr_period=2,
    )
    rising = build_daily_context(_bars([10, 11, 12, 13, 14, 15]), config)
    falling = build_daily_context(_bars([15, 14, 13, 12, 11, 10]), config)

    assert rising.iloc[-1]["daily_direction"] == 1
    assert falling.iloc[-1]["daily_direction"] == -1


def test_daily_context_assigns_causal_bull_trend_segment_ids() -> None:
    config = MultiTimeframeTrendConfig(
        daily_ema_fast=2,
        daily_ema_mid=3,
        daily_ema_slow=4,
        atr_period=2,
    )
    close = [10, 11, 12, 13, 8, 9, 10, 11, 12]

    full = build_daily_context(_bars(close), config)
    prefix = build_daily_context(_bars(close[:6]), config)

    assert full["daily_direction"].tolist() == [0, 1, 1, 1, -1, -1, -1, 1, 1]
    assert full["daily_bull_trend_id"].tolist() == [0, 1, 1, 1, 0, 0, 0, 2, 2]
    pd.testing.assert_series_equal(
        prefix["daily_bull_trend_id"],
        full.loc[:5, "daily_bull_trend_id"],
    )


def test_daily_context_uses_prior_five_completed_bodies_not_wicks() -> None:
    config = MultiTimeframeTrendConfig(
        daily_ema_fast=2,
        daily_ema_mid=3,
        daily_ema_slow=4,
        atr_period=2,
    )
    daily = _bars([80, 82, 84, 86, 88, 90])
    daily.loc[:4, "high"] = 500.0
    daily.loc[:4, "low"] = 1.0
    daily.loc[5, "open"] = 1_000.0

    context = build_daily_context(daily, config)

    assert context.loc[5, "prior_5d_high"] == pytest.approx(88.0)
    assert context.loc[5, "prior_5d_low"] == pytest.approx(80.0)


def test_confirmed_pivot_is_unknown_until_right_bars_close() -> None:
    bars = _bars([6, 5, 2, 5, 6, 7, 8], freq="5min", low_pad=1.0)
    enriched = attach_confirmed_pivots(bars, left=2, right=2)

    assert pd.isna(enriched.loc[3, "latest_swing_low"])
    assert enriched.loc[4, "latest_swing_low"] == pytest.approx(1.0)
    assert enriched.loc[4, "latest_swing_low_pivot_time"] == bars.loc[2, "bar_end"]
    assert enriched.loc[4, "latest_swing_low_known_at"] == bars.loc[4, "bar_end"]


def test_always_in_long_requires_progress_and_uses_confirmed_swing_stop() -> None:
    config = MultiTimeframeTrendConfig(
        atr_period=2,
        always_in_window=6,
        always_in_min_progress=4,
    )
    bars = _bars([100, 101, 102, 103, 104, 105], freq="5min", high_pad=0.5)
    bars["atr14"] = 2.0
    bars["latest_swing_low"] = 98.0
    bars["latest_swing_low_known_at"] = bars["bar_end"]

    candidate = detect_always_in(
        bars,
        index=5,
        direction=1,
        config=config,
        tick_size=0.5,
    )

    assert candidate is not None
    assert candidate.setup_type == "always_in"
    assert candidate.direction == 1
    assert candidate.trigger == pytest.approx(106.0)
    assert candidate.stop_price == pytest.approx(97.5)


def test_pullback_breakout_uses_prior_volume_quantile() -> None:
    config = MultiTimeframeTrendConfig(
        atr_period=2,
        pullback_min_bars=3,
        pullback_max_bars=5,
        volume_lookback=3,
        volume_quantile=0.80,
    )
    bars = _bars(
        [10.0, 9.0, 9.2, 9.4, 11.0],
        freq="5min",
        high_pad=0.2,
        low_pad=0.2,
    )
    bars["volume"] = [100.0, 100.0, 100.0, 100.0, 1_000.0]
    frame = build_intraday_context(bars, config)
    state = PullbackState()
    candidate = None
    for index in range(len(frame)):
        state, candidate = advance_pullback_state(
            frame,
            index=index,
            direction=1,
            state=state,
            config=config,
            tick_size=0.1,
        )

    assert frame.loc[4, "volume_threshold"] == pytest.approx(100.0)
    assert bool(frame.loc[4, "volume_expanded"])
    assert candidate is not None
    assert candidate.setup_type == "pullback_breakout"
    assert candidate.trigger == pytest.approx(11.3)
    assert candidate.stop_price == pytest.approx(8.7)


def test_pullback_resets_after_breakout_without_volume_confirmation() -> None:
    config = MultiTimeframeTrendConfig(
        atr_period=2,
        pullback_min_bars=3,
        pullback_max_bars=5,
        volume_lookback=3,
    )
    bars = _bars(
        [10.0, 9.0, 9.2, 9.4, 11.0],
        freq="5min",
        high_pad=0.2,
        low_pad=0.2,
    )
    bars["volume"] = 100.0
    frame = build_intraday_context(bars, config)
    state = PullbackState()
    candidate = None
    for index in range(len(frame)):
        state, candidate = advance_pullback_state(
            frame,
            index=index,
            direction=1,
            state=state,
            config=config,
            tick_size=0.1,
        )

    assert candidate is None
    assert state.direction == 0
    assert state.bars == 0


def test_near_daily_resistance_rejects_long_candidate() -> None:
    signal_time = pd.Timestamp("2024-01-05 15:00", tz="Asia/Shanghai")
    daily = pd.DataFrame(
        {
            "bar_end": pd.date_range(
                "2024-01-01 15:00", periods=5, freq="D", tz="Asia/Shanghai"
            ),
            "high": [97.0, 98.0, 99.0, 100.5, 100.0],
            "low": [90.0, 91.0, 92.0, 93.0, 94.0],
            "daily_atr14": [2.0] * 5,
            "latest_swing_high": [np.nan] * 5,
            "latest_swing_low": [np.nan] * 5,
        }
    )
    candidate = SignalCandidate(
        setup_type="always_in",
        direction=1,
        signal_index=10,
        signal_time=signal_time,
        known_at=signal_time,
        trigger=100.0,
        stop_price=95.0,
    )

    decision = assess_obstacle(candidate, daily, MultiTimeframeTrendConfig())

    assert decision.rejected
    assert decision.reason == "HTF_OBSTACLE_NEAR"
    assert decision.price == pytest.approx(100.5)
    assert decision.distance_atr == pytest.approx(0.25)


def test_obstacle_filter_excludes_pivots_older_than_lookback() -> None:
    bar_end = pd.date_range(
        "2024-01-01 15:00", periods=25, freq="D", tz="Asia/Shanghai"
    )
    old_pivot_time = bar_end[0]
    daily = pd.DataFrame(
        {
            "bar_end": bar_end,
            "high": [100.5] * 5 + [99.0] * 20,
            "low": [90.0] * 25,
            "daily_atr14": [2.0] * 25,
            "latest_swing_high": [100.5] * 25,
            "latest_swing_high_pivot_time": [old_pivot_time] * 25,
            "latest_swing_low": [np.nan] * 25,
            "latest_swing_low_pivot_time": [None] * 25,
        }
    )
    candidate = SignalCandidate(
        setup_type="always_in",
        direction=1,
        signal_index=10,
        signal_time=bar_end[-1],
        known_at=bar_end[-1],
        trigger=100.0,
        stop_price=95.0,
    )

    decision = assess_obstacle(candidate, daily, MultiTimeframeTrendConfig())

    assert not decision.rejected


def test_sizing_includes_stressed_round_trip_cost() -> None:
    decision = size_for_risk(
        equity=100_000.0,
        entry=100.0,
        stop=98.0,
        multiplier=10.0,
        stressed_round_trip_cost=5.0,
        risk_per_trade=0.01,
    )

    assert decision.quantity == 40
    assert decision.risk_budget == pytest.approx(1_000.0)
    assert decision.loss_per_lot == pytest.approx(25.0)


def test_trailing_stop_never_widens() -> None:
    stop = advance_trailing_stop(
        current_stop=100.0,
        direction=1,
        confirmed_swing=99.0,
        atr_value=2.0,
        buffer_atr=0.2,
        tick_size=1.0,
    )

    assert stop == pytest.approx(100.0)


def test_pullback_target_is_two_r_and_symmetric() -> None:
    assert two_r_target(100.0, 98.0, 1, 2.0) == pytest.approx(104.0)
    assert two_r_target(100.0, 102.0, -1, 2.0) == pytest.approx(96.0)


def _strategy_inputs() -> tuple[pd.DataFrame, pd.DataFrame, MultiTimeframeTrendConfig]:
    daily = _bars(
        [80, 82, 84, 86, 88, 90],
        start="2024-01-01",
        high_pad=1.0,
        low_pad=1.0,
    )
    minute5 = _bars(
        [100, 99, 97, 99, 100, 101, 102, 103, 104, 105, 106, 107, 108, 109],
        freq="5min",
        start="2024-01-07 09:00",
        high_pad=0.5,
        low_pad=0.5,
    )
    minute5["volume"] = 100.0
    config = MultiTimeframeTrendConfig(
        daily_ema_fast=2,
        daily_ema_mid=3,
        daily_ema_slow=4,
        atr_period=2,
        daily_obstacle_lookback=5,
        volume_lookback=3,
        # 这些合成序列早于入场质量规则，关掉后仍测原有过滤链
        max_entry_volume_ratio=0.0,
        max_entry_stop_distance_atr=0.0,
        max_entry_range_position=0.0,
        min_entry_range_width_atr=0.0,
    )
    return daily, minute5, config


def _instrument(tick_size: float = 0.5) -> InstrumentSpec:
    return InstrumentSpec(
        symbol="RB0",
        exchange="SHFE",
        contract="RB2405",
        tick_size=tick_size,
        multiplier=10.0,
        stressed_round_trip_cost=5.0,
        metadata_asof=pd.Timestamp("2023-12-01", tz="Asia/Shanghai"),
    )


def test_generate_candidates_carries_bull_trend_segment_id() -> None:
    daily, minute5, config = _strategy_inputs()

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )

    assert not out.empty
    assert "daily_bull_trend_id" in out.columns
    assert set(out["daily_bull_trend_id"]) == {1}


def test_always_in_long_is_not_filtered_when_other_checks_pass() -> None:
    daily, minute5, config = _strategy_inputs()
    minute5["open"] = minute5["close"] / 1.001

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )
    first = out.loc[out["signal_type"] == "always_in"].iloc[0]

    assert first["filtered_reason"] == ""


def test_always_in_long_daily_ema_gap_threshold_is_inclusive() -> None:
    daily, minute5, config = _strategy_inputs()
    minute5["open"] = minute5["close"] / 1.001
    baseline = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=replace(config, always_in_long_daily_ema_gap_min_ratio=0.0),
        equity=100_000.0,
    )
    baseline_candidate = baseline.loc[
        (baseline["signal_type"] == "always_in")
        & (baseline["filtered_reason"] == "")
    ].iloc[0]
    daily_ema5 = float(baseline_candidate["daily_ema5"])
    daily_ema20 = float(baseline_candidate["daily_ema20"])
    gap = (daily_ema5 - daily_ema20) / daily_ema20

    blocked = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=replace(
            config,
            always_in_long_daily_ema_gap_min_ratio=gap + 1e-6,
        ),
        equity=100_000.0,
    )
    blocked_candidate = blocked.loc[
        (blocked["signal_type"] == "always_in")
        & (blocked["signal_datetime"] == baseline_candidate["signal_datetime"])
    ].iloc[0]

    assert blocked_candidate["filtered_reason"] == "DAILY_EMA_GAP_BELOW_MIN"
    assert blocked_candidate["rejection_code"] == "DAILY_EMA_GAP_BELOW_MIN"
    assert blocked_candidate["quantity"] == 0

    boundary = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=replace(config, always_in_long_daily_ema_gap_min_ratio=gap),
        equity=100_000.0,
    )
    boundary_candidate = boundary.loc[
        (boundary["signal_type"] == "always_in")
        & (boundary["signal_datetime"] == baseline_candidate["signal_datetime"])
    ].iloc[0]

    assert boundary_candidate["filtered_reason"] == ""
    assert boundary_candidate["rejection_code"] == ""


@pytest.mark.parametrize(
    ("daily_ema5", "daily_ema20"),
    [
        (np.nan, 100.0),
        (np.inf, 100.0),
        (pd.NA, 100.0),
        (110.0, np.nan),
        (110.0, np.inf),
        (110.0, 0.0),
        (110.0, -1.0),
    ],
)
def test_always_in_long_daily_ema_gap_fails_closed(
    daily_ema5: object,
    daily_ema20: object,
) -> None:
    signal_time = pd.Timestamp("2024-01-07 09:00", tz="Asia/Shanghai")
    candidate = SignalCandidate(
        setup_type="always_in",
        direction=1,
        signal_index=5,
        signal_time=signal_time,
        known_at=signal_time,
        trigger=110.0,
        stop_price=100.0,
    )
    row = pd.Series(
        {
            "daily_ema5": daily_ema5,
            "daily_ema20": daily_ema20,
            "prior_5d_high": 100.0,
            "prior_5d_low": 80.0,
        }
    )
    daily_context = pd.DataFrame(
        {
            "bar_end": [pd.Timestamp("2024-01-06", tz="Asia/Shanghai")],
            "high": [100.0],
            "low": [80.0],
            "daily_atr14": [2.0],
            "latest_swing_high": [np.nan],
            "latest_swing_low": [np.nan],
        }
    )

    result = strategy_module._candidate_row(
        candidate=candidate,
        row=row,
        daily_context=daily_context,
        instrument=_instrument(),
        config=MultiTimeframeTrendConfig(),
        equity=100_000.0,
        forced_reason="",
    )

    assert result["filtered_reason"] == "DAILY_EMA_GAP_BELOW_MIN"
    assert result["rejection_code"] == "DAILY_EMA_GAP_BELOW_MIN"
    assert result["quantity"] == 0


def test_long_candidate_requires_prior_five_day_body_breakout() -> None:
    daily, minute5, _ = _strategy_inputs()
    daily.loc[0, "open"] = 103.5
    daily.loc[0, "high"] = 104.0
    config = MultiTimeframeTrendConfig(
        daily_ema_fast=2,
        daily_ema_mid=3,
        daily_ema_slow=4,
        atr_period=2,
        daily_obstacle_lookback=1,
        volume_lookback=3,
    )

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )
    first = out.loc[out["signal_type"] == "always_in"].iloc[0]

    assert first["prior_5d_high"] == pytest.approx(103.5)
    assert first["trigger"] == pytest.approx(103.0)
    assert first["filtered_reason"] == "DAILY_FIVE_BAR_BREAKOUT_NOT_MET"


def test_long_candidate_allows_trigger_equal_to_prior_five_day_body_high() -> None:
    daily, minute5, _ = _strategy_inputs()
    daily.loc[0, "open"] = 103.0
    daily.loc[0, "high"] = 103.5
    config = MultiTimeframeTrendConfig(
        max_entry_volume_ratio=0.0,
        max_entry_stop_distance_atr=0.0,
        max_entry_range_position=0.0,
        min_entry_range_width_atr=0.0,
        daily_ema_fast=2,
        daily_ema_mid=3,
        daily_ema_slow=4,
        atr_period=2,
        daily_obstacle_lookback=1,
        volume_lookback=3,
    )

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )
    first = out.loc[out["signal_type"] == "always_in"].iloc[0]

    assert first["trigger"] == pytest.approx(first["prior_5d_high"])
    assert first["filtered_reason"] == ""


def test_short_candidate_requires_symmetric_five_day_body_breakout() -> None:
    daily = _bars([120, 118, 116, 114, 112, 110])
    daily.loc[0, "open"] = 96.5
    daily.loc[0, "low"] = 96.0
    minute5 = _bars(
        [100, 101, 103, 101, 100, 99, 98, 97, 96, 95, 94, 93, 92, 91],
        freq="5min",
        start="2024-01-07 09:00",
        high_pad=0.5,
        low_pad=0.5,
    )
    minute5["volume"] = 100.0
    config = MultiTimeframeTrendConfig(
        max_entry_volume_ratio=0.0,
        max_entry_stop_distance_atr=0.0,
        max_entry_range_position=0.0,
        min_entry_range_width_atr=0.0,
        daily_ema_fast=2,
        daily_ema_mid=3,
        daily_ema_slow=4,
        atr_period=2,
        daily_obstacle_lookback=1,
        volume_lookback=3,
        candidate_direction_blacklist=(),
    )

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )
    first = out.loc[out["signal_type"] == "always_in"].iloc[0]

    assert first["prior_5d_low"] == pytest.approx(96.5)
    assert first["trigger"] == pytest.approx(97.0)
    assert first["filtered_reason"] == "DAILY_FIVE_BAR_BREAKOUT_NOT_MET"

    daily.loc[0, "open"] = 97.0
    allowed = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )
    allowed_first = allowed.loc[allowed["signal_type"] == "always_in"].iloc[0]
    assert allowed_first["trigger"] == pytest.approx(allowed_first["prior_5d_low"])
    assert allowed_first["filtered_reason"] == ""


def test_candidate_is_retained_when_five_completed_daily_bars_are_unavailable() -> None:
    daily, minute5, config = _strategy_inputs()
    daily = daily.iloc[:4].copy()
    minute5["bar_end"] = minute5["bar_end"] - pd.Timedelta(days=2)

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=replace(config, always_in_long_daily_ema_gap_min_ratio=0.0),
        equity=100_000.0,
    )

    assert not out.empty
    assert set(out["filtered_reason"]) == {"DAILY_FIVE_BAR_HISTORY_UNAVAILABLE"}
    assert (out["candidate_status"] == "filtered").all()


def test_generate_candidates_emits_baseline_compatible_audit_fields() -> None:
    daily, minute5, config = _strategy_inputs()
    instrument = InstrumentSpec(
        symbol="RB0",
        exchange="SHFE",
        contract="RB2405",
        tick_size=0.5,
        multiplier=10.0,
        stressed_round_trip_cost=5.0,
        metadata_asof=pd.Timestamp("2023-12-01", tz="Asia/Shanghai"),
    )

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=instrument,
        config=config,
        equity=100_000.0,
    )

    required = {
        "datetime",
        "signal_datetime",
        "signal_type",
        "side",
        "trigger",
        "stop_price",
        "target_price",
        "candidate_status",
        "filtered_reason",
        "feature_asof",
        "known_at",
        "order_active_at",
        "quantity",
        "contract",
        "fill_time",
        "rejection_code",
    }
    assert required.issubset(out.columns)
    eligible = out.loc[
        (out["signal_type"] == "always_in")
        & (out["candidate_status"] == "not_triggered")
    ]
    assert not eligible.empty
    assert set(eligible["side"]) == {"long"}
    assert set(eligible["contract"]) == {"RB2405"}
    assert eligible["fill_time"].isna().all()
    assert (eligible["quantity"] > 0).all()


def test_future_rows_do_not_change_earlier_candidates() -> None:
    daily, minute5, config = _strategy_inputs()
    instrument = _instrument()
    prefix_bars = minute5.iloc[:12].copy()

    prefix = generate_multi_timeframe_candidates(
        daily,
        prefix_bars,
        instrument=instrument,
        config=config,
        equity=100_000.0,
    )
    full = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=instrument,
        config=config,
        equity=100_000.0,
    )
    cutoff = prefix_bars.iloc[-1]["bar_end"]
    actual = full.loc[full["signal_datetime"] <= cutoff].reset_index(drop=True)

    pd.testing.assert_frame_equal(prefix.reset_index(drop=True), actual)


def test_generator_rejects_ambiguous_datetime_without_explicit_bar_end() -> None:
    daily, minute5, config = _strategy_inputs()
    ambiguous_daily = daily.rename(columns={"bar_end": "datetime"})

    with pytest.raises(KeyError, match="explicit bar_end"):
        generate_multi_timeframe_candidates(
            ambiguous_daily,
            minute5,
            instrument=_instrument(),
            config=config,
            equity=100_000.0,
        )


def test_pullback_target_remains_virtual_until_actual_fill() -> None:
    daily, _, config = _strategy_inputs()
    minute5 = _bars(
        [100.0, 99.0, 99.2, 99.4, 101.0],
        freq="5min",
        start="2024-01-07 09:00",
        high_pad=0.2,
        low_pad=0.2,
    )
    minute5["volume"] = [100.0, 100.0, 100.0, 100.0, 1_000.0]
    instrument = _instrument(0.1)
    config = replace(config, candidate_setup_blacklist=())

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=instrument,
        config=config,
        equity=100_000.0,
    )
    candidate = out.loc[out["signal_type"] == "pullback_breakout"].iloc[0]

    assert pd.isna(candidate["target_price"])
    assert np.isfinite(candidate["target_price_virtual"])


def test_pullback_breakout_long_ignores_always_in_daily_ema_gap() -> None:
    daily, _, config = _strategy_inputs()
    minute5 = _bars(
        [100.0, 99.0, 99.2, 99.4, 101.0],
        freq="5min",
        start="2024-01-07 09:00",
        high_pad=0.2,
        low_pad=0.2,
    )
    minute5["volume"] = [100.0, 100.0, 100.0, 100.0, 1_000.0]
    config = replace(
        config,
        candidate_setup_blacklist=(),
        always_in_long_daily_ema_gap_min_ratio=0.99,
    )

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(0.1),
        config=config,
        equity=100_000.0,
    )
    candidate = out.loc[out["signal_type"] == "pullback_breakout"].iloc[0]

    assert candidate["direction_value"] == 1
    assert candidate["filtered_reason"] != "DAILY_EMA_GAP_BELOW_MIN"
    assert candidate["rejection_code"] != "DAILY_EMA_GAP_BELOW_MIN"


def test_default_blacklist_removes_pullback_breakout_candidates() -> None:
    daily, _, config = _strategy_inputs()
    minute5 = _bars(
        [100.0, 99.0, 99.2, 99.4, 101.0],
        freq="5min",
        start="2024-01-07 09:00",
        high_pad=0.2,
        low_pad=0.2,
    )
    minute5["volume"] = [100.0, 100.0, 100.0, 100.0, 1_000.0]

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(0.1),
        config=config,
        equity=100_000.0,
    )

    assert "pullback_breakout" not in set(out["signal_type"])


def test_blacklisted_pullback_does_not_duplicate_allowed_always_in(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, minute5, config = _strategy_inputs()

    def candidate(setup_type: str, bars: pd.DataFrame, index: int) -> SignalCandidate:
        signal_time = bars.iloc[index]["bar_end"]
        return SignalCandidate(
            setup_type=setup_type,
            direction=1,
            signal_index=index,
            signal_time=signal_time,
            known_at=signal_time,
            trigger=110.0,
            stop_price=100.0,
            pullback_start_index=2,
        )

    def fake_always_in(
        bars: pd.DataFrame,
        *,
        index: int,
        **_kwargs: object,
    ) -> SignalCandidate | None:
        return candidate("always_in", bars, index) if index == 5 else None

    def fake_pullback(
        bars: pd.DataFrame,
        *,
        index: int,
        state: PullbackState,
        **_kwargs: object,
    ) -> tuple[PullbackState, SignalCandidate | None]:
        emitted = candidate("pullback_breakout", bars, index) if index == 5 else None
        return state, emitted

    monkeypatch.setattr(strategy_module, "detect_always_in", fake_always_in)
    monkeypatch.setattr(strategy_module, "advance_pullback_state", fake_pullback)

    out = strategy_module.generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )

    assert len(out) == 1
    assert out.iloc[0]["signal_type"] == "always_in"
    assert out.iloc[0]["filtered_reason"] == ""


def test_generate_candidates_use_signal_bar_instrument_fields(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    daily, minute5, config = _strategy_inputs()
    minute5["open"] = minute5["close"] / 1.001
    minute5["contract_code"] = "P2605.DCE"
    minute5["instrument_tick_size"] = 0.5
    minute5["instrument_multiplier"] = 10.0
    minute5["instrument_stressed_round_trip_cost"] = 5.0
    minute5["instrument_metadata_asof"] = pd.Timestamp(
        "2024-01-07 08:55",
        tz="Asia/Shanghai",
    )
    minute5.loc[5, "contract_code"] = "P2609.DCE"
    minute5.loc[5, "instrument_tick_size"] = 1.0
    minute5.loc[5, "instrument_multiplier"] = 20.0
    minute5.loc[5, "instrument_stressed_round_trip_cost"] = 30.0
    minute5.loc[5, "instrument_metadata_asof"] = pd.Timestamp(
        "2024-01-07 09:20",
        tz="Asia/Shanghai",
    )
    tick_sizes: list[tuple[int, float]] = []

    def fake_always_in(
        bars: pd.DataFrame,
        *,
        index: int,
        tick_size: float,
        **_kwargs: object,
    ) -> SignalCandidate | None:
        tick_sizes.append((index, tick_size))
        if index != 5:
            return None
        signal_time = bars.iloc[index]["bar_end"]
        return SignalCandidate(
            setup_type="always_in",
            direction=1,
            signal_index=index,
            signal_time=signal_time,
            known_at=signal_time,
            trigger=110.0,
            stop_price=109.0,
            pullback_start_index=2,
        )

    def fake_pullback(
        bars: pd.DataFrame,
        *,
        state: PullbackState,
        **_kwargs: object,
    ) -> tuple[PullbackState, SignalCandidate | None]:
        del bars
        return state, None

    monkeypatch.setattr(strategy_module, "detect_always_in", fake_always_in)
    monkeypatch.setattr(strategy_module, "advance_pullback_state", fake_pullback)
    monkeypatch.setattr(
        strategy_module,
        "assess_obstacle",
        lambda *args, **kwargs: SimpleNamespace(
            rejected=False,
            reason="",
            price=np.nan,
            distance_atr=np.nan,
        ),
    )

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )

    candidate = out.iloc[0]

    assert (5, 1.0) in tick_sizes
    assert candidate["contract"] == "P2609.DCE"
    assert candidate["loss_per_lot"] == pytest.approx(50.0)
    assert candidate["quantity"] == 20
    assert candidate["metadata_asof"] == pd.Timestamp(
        "2024-01-07 09:20",
        tz="Asia/Shanghai",
    )


def test_always_in_target_is_virtual_for_chart() -> None:
    daily, minute5, config = _strategy_inputs()

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )
    candidate = out.loc[out["signal_type"] == "always_in"].iloc[0]

    assert pd.isna(candidate["target_price"])
    assert np.isfinite(candidate["target_price_virtual"])


def test_generate_short_always_in_candidate_is_symmetric() -> None:
    daily = _bars([120, 118, 116, 114, 112, 110], start="2024-01-01")
    minute5 = _bars(
        [100, 101, 103, 101, 100, 99, 98, 97, 96, 95, 94, 93, 92, 91],
        freq="5min",
        start="2024-01-07 09:00",
        high_pad=0.5,
        low_pad=0.5,
    )
    minute5["volume"] = 100.0
    config = MultiTimeframeTrendConfig(
        max_entry_volume_ratio=0.0,
        max_entry_stop_distance_atr=0.0,
        max_entry_range_position=0.0,
        min_entry_range_width_atr=0.0,
        daily_ema_fast=2,
        daily_ema_mid=3,
        daily_ema_slow=4,
        atr_period=2,
        daily_obstacle_lookback=5,
        volume_lookback=3,
    )

    blocked = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )
    assert blocked.empty

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=replace(config, candidate_direction_blacklist=()),
        equity=100_000.0,
    )
    eligible = out.loc[
        (out["signal_type"] == "always_in")
        & (out["candidate_status"] == "not_triggered")
    ]

    assert not eligible.empty
    assert set(eligible["side"]) == {"short"}
    assert (eligible["stop_price"] > eligible["trigger"]).all()
    assert (eligible["quantity"] > 0).all()


def test_always_in_short_ignores_long_daily_ema_gap() -> None:
    daily = _bars([120, 118, 116, 114, 112, 110], start="2024-01-01")
    minute5 = _bars(
        [100, 101, 103, 101, 100, 99, 98, 97, 96, 95, 94, 93, 92, 91],
        freq="5min",
        start="2024-01-07 09:00",
        high_pad=0.5,
        low_pad=0.5,
    )
    minute5["volume"] = 100.0
    config = MultiTimeframeTrendConfig(
        daily_ema_fast=2,
        daily_ema_mid=3,
        daily_ema_slow=4,
        atr_period=2,
        daily_obstacle_lookback=5,
        volume_lookback=3,
        candidate_direction_blacklist=(),
        always_in_long_daily_ema_gap_min_ratio=0.99,
    )

    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )
    candidates = out.loc[out["signal_type"] == "always_in"]

    assert not candidates.empty
    assert set(candidates["side"]) == {"short"}
    assert (
        candidates["filtered_reason"] != "DAILY_EMA_GAP_BELOW_MIN"
    ).all()
    assert (candidates["rejection_code"] != "DAILY_EMA_GAP_BELOW_MIN").all()


def test_candidates_are_accepted_by_existing_candidate_schema() -> None:
    daily, minute5, config = _strategy_inputs()
    raw = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )

    standardized = standardize_candidate_events(raw)

    assert len(standardized) == len(raw)
    assert standardized["candidate_id"].is_unique
    assert set(standardized["sample_status"]) == {"not_triggered_market"}


def test_candidate_audit_times_are_causal_and_preserve_structure_time() -> None:
    daily, minute5, config = _strategy_inputs()
    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )
    always_in = out.loc[out["signal_type"] == "always_in"]

    assert (always_in["feature_asof"] <= always_in["known_at"]).all()
    assert (always_in["known_at"] <= always_in["decision_asof"]).all()
    assert (always_in["decision_asof"] == always_in["order_active_at"]).all()
    assert (always_in["decision_sequence"] < always_in["order_active_sequence"]).all()
    assert (always_in["pivot_time"] <= always_in["structure_known_at"]).all()
    assert (always_in["structure_known_at"] <= always_in["known_at"]).all()


def test_order_lifecycle_uses_event_and_bar_sequences_not_wall_clock() -> None:
    daily, minute5, config = _strategy_inputs()
    out = generate_multi_timeframe_candidates(
        daily,
        minute5,
        instrument=_instrument(),
        config=config,
        equity=100_000.0,
    )

    assert (out["order_active_at"] == out["decision_asof"]).all()
    assert (out["decision_sequence"] < out["order_active_sequence"]).all()
    assert (out["order_expire_bar_i"] == out["signal_i"] + 3).all()
    assert out["order_expire_at"].isna().all()
