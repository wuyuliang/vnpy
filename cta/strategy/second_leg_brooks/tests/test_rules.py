from __future__ import annotations

from datetime import time

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
)
from cta.strategy.common import bar_shapes
from cta.strategy.second_leg_brooks.config import SecondLegBrooksConfig
from cta.strategy.second_leg_brooks.rules import build_brooks_features, match_second_leg


def _sessions() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec(
            "day",
            False,
            (SessionSegment("day", time(9), time(15), time(9)),),
        ),
    )


def test_vectorized_pullback_primitive_finds_causal_three_part_structure() -> None:
    lows = pd.Series([9.8, 8.8, 7.8, 6.8, 6.0, 6.3, 6.7, 6.4, 5.8])
    highs = pd.Series([10.5, 9.5, 8.5, 7.5, 6.5, 6.8, 7.5, 7.0, 6.6])
    result = bar_shapes.causal_low_pullback_structure(
        lows,
        highs,
        pd.Series([1] * len(lows)),
        pd.Series([1.0] * len(lows)),
        first_leg_lookback=4,
        first_leg_atr_mult=1.5,
        pullback_min_bars=2,
        pullback_max_bars=4,
        pullback_min_ratio=0.2,
        pullback_max_ratio=0.9,
    )

    row = result.iloc[8]
    assert bool(row["pullback_matched"])
    assert row["swing_index"] == 4
    assert row["pullback_bars"] == 4
    assert row["first_leg"] == pytest.approx(4.5)
    assert row["rally_high"] == pytest.approx(7.5)
    assert row["pullback_ratio"] == pytest.approx(1.5 / 4.5)


def test_first_leg_lookback_cannot_borrow_a_high_across_session_break() -> None:
    lows = pd.Series([20.0, 19.0, 10.0, 9.0, 9.3, 9.5, 8.8])
    highs = pd.Series([21.0, 20.0, 10.5, 9.5, 9.8, 10.0, 9.6])
    result = bar_shapes.causal_low_pullback_structure(
        lows,
        highs,
        pd.Series([1, 1, 2, 2, 2, 2, 2]),
        pd.Series([1.0] * len(lows)),
        first_leg_lookback=2,
        first_leg_atr_mult=1.5,
        pullback_min_bars=2,
        pullback_max_bars=4,
        pullback_min_ratio=0.2,
        pullback_max_ratio=0.9,
    )

    assert not bool(result.iloc[6]["pullback_matched"])


def test_feature_builder_keeps_pullback_structure_inside_each_segment() -> None:
    end = list(
        pd.date_range("2026-01-05 14:58", periods=2, freq="min", tz="Asia/Shanghai")
    ) + list(
        pd.date_range("2026-01-06 09:01", periods=5, freq="min", tz="Asia/Shanghai")
    )
    bars = pd.DataFrame(
        {
            "bar_end": end,
            "open": [20.5, 19.5, 10.2, 9.4, 9.5, 9.7, 9.5],
            "high": [21.0, 20.0, 10.5, 9.5, 9.8, 10.0, 9.6],
            "low": [20.0, 19.0, 10.0, 9.0, 9.3, 9.5, 8.8],
            "close": [20.1, 19.1, 10.1, 9.1, 9.4, 9.6, 8.9],
            "volume": [10.0] * 7,
        }
    )
    config = SecondLegBrooksConfig(
        atr_period=2,
        first_leg_lookback=2,
        pullback_min_bars=2,
        pullback_max_bars=4,
        volume_baseline_min_samples=1,
    )

    features = build_brooks_features(bars, _sessions(), config)

    assert pd.isna(features.loc[3, "swing_window_high"])
    assert match_second_leg(features, 6, config) is None



# ---------------------------------------------------------------------------
# ③ 入场根自己的几道门 + ② 回抽的上下界 + 同一结构去重
# ---------------------------------------------------------------------------
def _structure_bars(
    *,
    entry_open: float = 9.6,
    entry_close: float = 8.7,
    entry_low: float | None = None,
    entry_volume: float = 30.0,
    rally_high: float = 9.9,
) -> pd.DataFrame:
    """①9.0 的第一段 → ②回抽到 rally_high → ③跌破 9.0 的入场根。"""
    lows = [12.0, 11.0, 10.0, 9.0, 9.2, 9.4]
    highs = [12.5, 11.5, 10.5, 9.6, 9.7, rally_high]
    opens = [12.4, 11.4, 10.4, 9.5, 9.3, 9.5]
    closes = [11.1, 10.1, 9.1, 9.2, 9.4, 9.6]
    volumes = [10.0] * 6
    entry_low = entry_close - 0.05 if entry_low is None else entry_low
    return pd.DataFrame({
        "bar_end": pd.date_range(
            "2026-01-05 09:20", periods=7, freq="min", tz="Asia/Shanghai"
        ),
        "open": [*opens, entry_open],
        "high": [*highs, entry_open + 0.05],
        "low": [*lows, entry_low],
        "close": [*closes, entry_close],
        "volume": [*volumes, entry_volume],
    })


def _structure_config(**overrides) -> SecondLegBrooksConfig:
    return SecondLegBrooksConfig(
        atr_period=3,
        first_leg_lookback=3,
        first_leg_atr_mult=1.0,
        pullback_min_bars=2,
        pullback_max_bars=4,
        volume_baseline_bars=5,
        volume_baseline_min_samples=1,
        entry_body_atr_mult=0.5,
        entry_volume_mult=2.0,
        structure_lookback_bars=3,
        **overrides,
    )


def _matches(bars, config) -> bool:
    features = build_brooks_features(bars, _sessions(), config)
    return match_second_leg(features, len(bars) - 1, config) is not None


def test_baseline_structure_matches() -> None:
    assert _matches(_structure_bars(), _structure_config())


def test_entry_bar_must_be_bearish_enough() -> None:
    # 实体只有 0.05，够不到 entry_body_atr_mult × ATR
    assert not _matches(
        _structure_bars(entry_open=8.75, entry_close=8.70), _structure_config()
    )


def test_entry_bar_must_close_near_its_low() -> None:
    # 长下影：收盘离最低点太远，说明卖压当根就被吃掉了
    assert not _matches(
        _structure_bars(entry_low=7.5), _structure_config()
    )


def test_entry_bar_must_carry_volume() -> None:
    assert not _matches(
        _structure_bars(entry_volume=10.0), _structure_config()
    )


def test_entry_bar_must_break_the_swing_low() -> None:
    """没跌破第一段低点 → 第二段还没开始。"""
    config = _structure_config()
    assert not _matches(
        _structure_bars(entry_open=9.6, entry_close=9.05, entry_low=9.02), config
    )
    # 关掉这道门，同一批 K 线就应该放行
    assert _matches(
        _structure_bars(entry_open=9.6, entry_close=9.05, entry_low=9.02),
        _structure_config(require_break_of_swing_low=False),
    )


def test_pullback_must_not_be_too_shallow() -> None:
    """几乎没回抽 → 还是同一段，不是第二段。"""
    assert not _matches(
        _structure_bars(rally_high=9.05),
        _structure_config(pullback_min_ratio=0.30, pullback_max_ratio=0.90),
    )


def test_pullback_must_not_undo_the_first_leg() -> None:
    """回抽吃掉整段 → 趋势已经被否定。"""
    assert not _matches(
        _structure_bars(rally_high=12.4),
        _structure_config(pullback_min_ratio=0.20, pullback_max_ratio=0.60),
    )


def test_one_candidate_per_structure_drops_the_repeat_entries() -> None:
    """结构成立后的连续几根会反复匹配，止损位完全相同 —— 只做第一根。"""
    from cta.strategy.second_leg_brooks.rules import iter_matches

    bars = _structure_bars()
    # 再接一根同样跌破的阴线：它会命中同一个 swing + 同一个 rally_high
    extra = bars.iloc[[-1]].copy()
    extra["bar_end"] = bars["bar_end"].iloc[-1] + pd.Timedelta(minutes=1)
    extra["open"] = 8.7
    extra["close"] = 8.3
    extra["high"] = 8.75
    extra["low"] = 8.28
    bars = pd.concat([bars, extra], ignore_index=True)

    config = _structure_config()
    features = build_brooks_features(bars, _sessions(), config)
    deduped = list(iter_matches(features, config))
    raw = list(iter_matches(
        features, _structure_config(one_candidate_per_structure=False)
    ))
    assert len(raw) >= len(deduped)
    keys = {(m.first_index, m.extras["rally_high"]) for _, m in deduped}
    assert len(keys) == len(deduped)
