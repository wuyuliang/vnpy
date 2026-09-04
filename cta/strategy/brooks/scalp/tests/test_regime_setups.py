from __future__ import annotations

from types import SimpleNamespace

import numpy as np
import pandas as pd

from cta.strategy.brooks.scalp.features import add_causal_features
from cta.strategy.brooks.scalp.regime import (
    RegimeSnapshot,
    RegimeState,
    classify_latest_regime,
)
from cta.strategy.brooks.scalp.setups import (
    SecondEntryState,
    SecondEntryTracker,
    detect_setups,
)


def _bars(count: int = 30) -> pd.DataFrame:
    end = pd.date_range("2026-01-05 09:05", periods=count, freq="5min", tz="Asia/Shanghai")
    close = np.linspace(100.0, 112.0, count)
    return pd.DataFrame(
        {
            "bar_end": end,
            "source_max_bar_end": end,
            "contract_code": "RB2605.SHF",
            "open": close - 0.3,
            "high": close + 0.6,
            "low": close - 0.7,
            "close": close,
            "volume": np.linspace(100.0, 200.0, count),
            "turnover": np.linspace(1_000_000.0, 2_000_000.0, count),
            "open_interest": 10_000.0,
        }
    )


def test_frozen_atr_ema_and_local_features_are_prefix_invariant() -> None:
    prefix = _bars(80)
    prefix.loc[0, ["open", "high", "low", "close"]] = [100.0, 102.0, 99.0, 101.0]
    got = add_causal_features(prefix)

    true_range = [3.0]
    for i in range(1, 14):
        row = prefix.iloc[i]
        prev_close = float(prefix.iloc[i - 1]["close"])
        true_range.append(
            max(
                float(row["high"] - row["low"]),
                abs(float(row["high"]) - prev_close),
                abs(float(row["low"]) - prev_close),
            )
        )
    assert np.isnan(got.loc[12, "atr_14"])
    assert got.loc[13, "atr_14"] == pytest_approx(np.mean(true_range))
    assert got.loc[0, "ema_20"] == prefix.loc[0, "close"]
    assert np.isnan(got.loc[19, "volume_ratio_prev20"])
    assert np.isfinite(got.loc[20, "volume_ratio_prev20"])

    future = _bars(20)
    future["bar_end"] += pd.Timedelta(days=10)
    future["source_max_bar_end"] = future["bar_end"]
    future[["open", "high", "low", "close", "volume"]] *= 25
    extended = add_causal_features(pd.concat([prefix, future], ignore_index=True))
    pd.testing.assert_frame_equal(
        got.reset_index(drop=True),
        extended.iloc[: len(prefix)].reset_index(drop=True),
        check_dtype=False,
    )


def pytest_approx(value: float):
    import pytest

    return pytest.approx(value, rel=1e-12, abs=1e-12)


def _regime_frame(direction: int = 1) -> pd.DataFrame:
    frame = add_causal_features(_bars(30))
    if direction < 0:
        ceiling = 250.0
        old_high = frame["high"].copy()
        frame["high"] = ceiling - frame["low"]
        frame["low"] = ceiling - old_high
        frame["open"] = ceiling - frame["open"]
        frame["close"] = ceiling - frame["close"]
        frame = add_causal_features(frame.drop(columns=[c for c in frame.columns if c not in _bars(1).columns]))
    frame["pa_always_in_dir"] = 0.20 * direction
    frame["pa_trend_strength_20"] = 5.0 * direction
    frame["pa_ema_slope_20"] = 0.5 * direction
    frame["pa_trend_bar_net"] = 0.25 * direction
    frame["pa_overlap_ratio_10"] = 0.30
    frame["pa_overlap_ratio_20"] = 0.30
    frame["pa_buy_climax"] = 0
    frame["pa_sell_climax"] = 0
    frame["pa_momentum_decay"] = 0.0
    frame["pa_barb_wire"] = 0
    if direction > 0:
        frame["ema_20"] = frame["close"] - 1.0
        frame["ema_60"] = frame["close"] - 2.0
    else:
        frame["ema_20"] = frame["close"] + 1.0
        frame["ema_60"] = frame["close"] + 2.0
    return frame


def test_regime_uses_prior_20_bar_frozen_range_and_mirrors_direction() -> None:
    up = _regime_frame(1)
    snap_up = classify_latest_regime(up, source_contract="RB2605.SHF", warmup_bars=20)
    assert snap_up.state is RegimeState.STRONG_TREND_UP
    assert snap_up.direction == 1
    assert snap_up.range_high == pytest_approx(float(up.iloc[-21:-1]["high"].max()))
    assert snap_up.range_low == pytest_approx(float(up.iloc[-21:-1]["low"].min()))

    down = _regime_frame(-1)
    snap_down = classify_latest_regime(down, source_contract="RB2605.SHF", warmup_bars=20)
    assert snap_down.state is RegimeState.STRONG_TREND_DOWN
    assert snap_down.direction == -1


def test_climax_has_priority_over_other_regimes_and_needs_two_clear_bars() -> None:
    frame = _regime_frame(1)
    frame.loc[frame.index[-1], "pa_buy_climax"] = 1
    first = classify_latest_regime(frame, source_contract="RB2605.SHF", warmup_bars=20)
    assert first.state is RegimeState.CLIMAX_TRANSITION

    frame.loc[frame.index[-1], "pa_buy_climax"] = 0
    frame.loc[frame.index[-2], "pa_buy_climax"] = 1
    still_transition = classify_latest_regime(
        frame,
        source_contract="RB2605.SHF",
        warmup_bars=20,
        prior_state=RegimeState.CLIMAX_TRANSITION,
    )
    assert still_transition.state is RegimeState.CLIMAX_TRANSITION


def _regime(state: RegimeState, direction: int) -> RegimeSnapshot:
    return RegimeSnapshot(
        state=state,
        direction=direction,
        feature_asof=pd.Timestamp("2026-01-05 10:00", tz="Asia/Shanghai").to_pydatetime(),
        range_high=120.0,
        range_low=80.0,
        range_mid=100.0,
        source_contract="RB2605.SHF",
    )


def _second_entry_bar(
    minute: int,
    open_: float,
    high: float,
    low: float,
    close: float,
    *,
    bull_reversal: int = 0,
    bear_reversal: int = 0,
) -> dict[str, object]:
    return {
        "bar_end": pd.Timestamp(f"2026-01-05 09:{minute:02d}", tz="Asia/Shanghai"),
        "source_max_bar_end": pd.Timestamp(f"2026-01-05 09:{minute:02d}", tz="Asia/Shanghai"),
        "contract_code": "RB2605.SHF",
        "open": open_,
        "high": high,
        "low": low,
        "close": close,
        "atr_14": 4.0,
        "ema_20": 103.0,
        "pullback_depth_long_20": 0.30,
        "pullback_depth_short_20": 0.30,
        "pa_bull_reversal": bull_reversal,
        "pa_bear_reversal": bear_reversal,
        "pa_buy_climax": 0,
        "pa_sell_climax": 0,
    }


def test_second_entry_requires_failed_shadow_first_attempt() -> None:
    tracker = SecondEntryTracker(symbol="RB0.SHFE", direction=1, price_tick=1.0)
    regime = _regime(RegimeState.STRONG_TREND_UP, 1)
    bars = [
        _second_entry_bar(5, 100, 102, 99.5, 101.5),
        _second_entry_bar(10, 101.5, 104, 101, 103.5),
        _second_entry_bar(15, 103.5, 106, 103, 105.5),
        _second_entry_bar(20, 105.5, 106, 103, 104),
        _second_entry_bar(25, 104, 106, 102, 105.5, bull_reversal=1),
        _second_entry_bar(30, 105.5, 107.2, 99.5, 100),
        _second_entry_bar(35, 100, 101, 98.5, 99),
        _second_entry_bar(40, 100.5, 104.5, 100, 104, bull_reversal=1),
    ]
    observed = [tracker.update(bar, regime) for bar in bars]

    assert observed[4].state is SecondEntryState.FIRST_SIGNAL_ARMED
    assert observed[4].candidate is None
    assert observed[5].state is SecondEntryState.FIRST_ATTEMPT_FAILED
    assert observed[6].state is SecondEntryState.PULLBACK_LEG_2
    assert observed[7].state is SecondEntryState.SECOND_ENTRY_READY
    assert observed[7].candidate is not None
    assert observed[7].candidate.rule_id == "second_entry_continuation"
    assert observed[7].candidate.context["first_attempt_triggered"] == 1
    assert observed[7].candidate.context["first_trigger"] == 107.0
    assert observed[7].candidate.context["first_stop"] == 101.0
    assert observed[7].candidate.context["failure_bar_end"]


def test_shadow_stop_before_first_trigger_does_not_count_as_failed_attempt() -> None:
    tracker = SecondEntryTracker(symbol="RB0.SHFE", direction=1, price_tick=1.0)
    regime = _regime(RegimeState.STRONG_TREND_UP, 1)
    bars = [
        _second_entry_bar(5, 100, 102, 99.5, 101.5),
        _second_entry_bar(10, 101.5, 104, 101, 103.5),
        _second_entry_bar(15, 103.5, 106, 103, 105.5),
        _second_entry_bar(20, 105.5, 106, 103, 104),
        _second_entry_bar(25, 104, 106, 102, 105.5, bull_reversal=1),
        _second_entry_bar(30, 105.5, 106.5, 99.5, 100),
    ]
    updates = [tracker.update(bar, regime) for bar in bars]
    assert updates[-1].state is SecondEntryState.FIRST_SIGNAL_ARMED
    assert updates[-1].candidate is None


def test_first_shadow_reaching_half_r_resets_without_real_candidate() -> None:
    tracker = SecondEntryTracker(symbol="RB0.SHFE", direction=1, price_tick=1.0)
    regime = _regime(RegimeState.STRONG_TREND_UP, 1)
    bars = [
        _second_entry_bar(5, 100, 102, 99.5, 101.5),
        _second_entry_bar(10, 101.5, 104, 101, 103.5),
        _second_entry_bar(15, 103.5, 106, 103, 105.5),
        _second_entry_bar(20, 105.5, 106, 103, 104),
        _second_entry_bar(25, 104, 106, 102, 105.5, bull_reversal=1),
        _second_entry_bar(30, 106.5, 111, 106, 110),
    ]
    last = None
    for bar in bars:
        last = tracker.update(bar, regime)
    assert last is not None
    assert last.state is SecondEntryState.IDLE
    assert last.candidate is None
    assert last.reset_reason == "first_attempt_mfe_reached"


def _breakout_rows(direction: int) -> pd.DataFrame:
    if direction > 0:
        rows = [
            {
                "open": 100.0,
                "high": 104.0,
                "low": 99.5,
                "close": 103.8,
                "pa_breakout_up_10": 1,
                "pa_breakout_down_10": 0,
                "pa_breakout_strength_10": 0.5,
                "pa_bo_body_ratio_10": 0.85,
                "pa_bo_close_pos_10": 0.95,
                "volume_ratio_prev20": 1.5,
            },
            {
                "open": 103.5,
                "high": 105.0,
                "low": 102.0,
                "close": 105.0,
                "pa_follow_through_10": 1,
                "pa_buy_climax": 0,
                "pa_sell_climax": 0,
                "atr_14": 4.0,
            },
        ]
    else:
        rows = [
            {
                "open": 100.0,
                "high": 100.5,
                "low": 96.0,
                "close": 96.2,
                "pa_breakout_up_10": 0,
                "pa_breakout_down_10": 1,
                "pa_breakout_strength_10": -0.5,
                "pa_bo_body_ratio_10": 0.85,
                "pa_bo_close_pos_10": 0.05,
                "volume_ratio_prev20": 1.5,
            },
            {
                "open": 96.5,
                "high": 98.0,
                "low": 95.0,
                "close": 95.0,
                "pa_follow_through_10": -1,
                "pa_buy_climax": 0,
                "pa_sell_climax": 0,
                "atr_14": 4.0,
            },
        ]
    end = pd.date_range("2026-01-05 10:05", periods=2, freq="5min", tz="Asia/Shanghai")
    frame = pd.DataFrame(rows)
    frame["bar_end"] = end
    frame["source_max_bar_end"] = end
    frame["contract_code"] = "RB2605.SHF"
    return frame


def test_breakout_follow_through_is_long_short_symmetric_and_priority_is_fixed() -> None:
    spec = SimpleNamespace(root_symbol="RB", price_tick=1.0)
    long_candidates = detect_setups(
        _breakout_rows(1),
        _regime(RegimeState.STRONG_TREND_UP, 1),
        spec,
        symbol="RB0.SHFE",
    )
    short_candidates = detect_setups(
        _breakout_rows(-1),
        _regime(RegimeState.STRONG_TREND_DOWN, -1),
        spec,
        symbol="RB0.SHFE",
    )

    assert [c.rule_id for c in long_candidates] == ["strong_breakout_follow_through"]
    assert [c.direction for c in long_candidates] == [1]
    assert [c.rule_id for c in short_candidates] == ["strong_breakout_follow_through"]
    assert [c.direction for c in short_candidates] == [-1]
    assert long_candidates[0].trigger_price == 106.0
    assert short_candidates[0].trigger_price == 94.0


def test_setups_do_not_read_model_or_composite_score_columns() -> None:
    frame = _breakout_rows(1)
    spec = SimpleNamespace(root_symbol="RB", price_tick=1.0)
    regime = _regime(RegimeState.STRONG_TREND_UP, 1)
    expected = detect_setups(frame, regime, spec, symbol="RB0.SHFE")
    frame["pa_scalp_score"] = [-999.0, 999.0]
    frame["setup_quality_score"] = [999.0, -999.0]
    frame["model_score"] = [0.0, 1.0]
    assert detect_setups(frame, regime, spec, symbol="RB0.SHFE") == expected
