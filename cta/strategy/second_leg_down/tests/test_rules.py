from __future__ import annotations

from datetime import time

import pandas as pd

from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
)
from cta.strategy.second_leg_down.config import SecondLegDownConfig
from cta.strategy.second_leg_down.rules import (
    build_second_leg_features,
    match_second_leg_pattern,
)


def _sessions() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec(
            "day",
            False,
            (SessionSegment("day", time(9), time(15), time(9)),),
        ),
    )


def _bars(rows: int = 30) -> pd.DataFrame:
    end = pd.date_range(
        "2026-01-05 09:01",
        periods=rows,
        freq="min",
        tz="Asia/Shanghai",
    )
    frame = pd.DataFrame(
        {
            "bar_end": end,
            "open": [110.0 - index * 0.1 for index in range(rows)],
            "high": [110.05 - index * 0.1 for index in range(rows)],
            "low": [109.85 - index * 0.1 for index in range(rows)],
            "close": [109.9 - index * 0.1 for index in range(rows)],
            "volume": [10.0] * rows,
        }
    )
    return frame


def _daily_bars(*, bearish: bool = True, include_future: bool = False) -> pd.DataFrame:
    end = pd.date_range(
        end="2026-01-04 15:00",
        periods=30,
        freq="D",
        tz="Asia/Shanghai",
    )
    closes = (
        [130.0 - index for index in range(len(end))]
        if bearish
        else [100.0 + index for index in range(len(end))]
    )
    frame = pd.DataFrame({"bar_end": end, "close": closes})
    if include_future:
        frame.loc[len(frame)] = [
            pd.Timestamp("2026-01-05 15:00", tz="Asia/Shanghai"),
            1_000.0,
        ]
    return frame


def _features(
    bars: pd.DataFrame,
    config: SecondLegDownConfig,
    *,
    daily_bars: pd.DataFrame | None = None,
) -> pd.DataFrame:
    return build_second_leg_features(
        bars,
        _sessions(),
        config,
        daily_bars=_daily_bars() if daily_bars is None else daily_bars,
    )


def test_features_use_prior_atr_and_prior_volume_only() -> None:
    config = SecondLegDownConfig(
        atr_period=3,
        volume_baseline_bars=5,
        volume_baseline_min_samples=3,
    )
    original = _bars()
    changed = original.copy()
    changed.loc[10, ["high", "low", "volume"]] = [1_000.0, 1.0, 10_000.0]

    before = build_second_leg_features(original, _sessions(), config)
    after = build_second_leg_features(changed, _sessions(), config)

    assert before.loc[10, "atr"] == after.loc[10, "atr"]
    assert before.loc[10, "volume_baseline"] == after.loc[10, "volume_baseline"]


def test_two_bar_pattern_matches_all_default_rules() -> None:
    config = SecondLegDownConfig(
        body_mode="per_bar",
        atr_period=3,
        big_body_atr_mult=1.0,
        volume_surge_mult=2.0,
        volume_baseline_min_samples=5,
        entry_block_minutes_after_open=10,
        entry_block_minutes_before_close=20,
    )
    bars = _bars()
    bars.loc[27, ["open", "high", "low", "close", "volume"]] = [
        108.0, 108.05, 106.9, 107.0, 30.0
    ]
    bars.loc[28, ["open", "high", "low", "close", "volume"]] = [
        107.0, 107.02, 105.95, 106.0, 30.0
    ]
    features = _features(bars, config)

    match = match_second_leg_pattern(features, 28, config)

    assert match is not None
    assert match.pattern_type == "two_bar"
    assert match.first_index == 27
    assert match.last_index == 28


def test_three_bar_pattern_requires_middle_bar_not_to_break_up() -> None:
    config = SecondLegDownConfig(
        body_mode="per_bar",
        atr_period=3,
        big_body_atr_mult=1.0,
        volume_surge_mult=2.0,
        volume_baseline_min_samples=5,
    )
    bars = _bars()
    bars.loc[26, ["open", "high", "low", "close", "volume"]] = [
        108.0, 108.05, 106.9, 107.0, 30.0
    ]
    bars.loc[27, ["open", "high", "low", "close"]] = [
        107.0, 108.05, 106.9, 107.0
    ]
    bars.loc[28, ["open", "high", "low", "close", "volume"]] = [
        107.0, 107.02, 105.95, 106.0, 30.0
    ]
    features = _features(bars, config)
    assert match_second_leg_pattern(features, 28, config) is not None

    bars.loc[27, "high"] = 108.051
    features = _features(bars, config)
    assert match_second_leg_pattern(features, 28, config) is None


def test_entry_window_is_relative_to_segment() -> None:
    config = SecondLegDownConfig(
        body_mode="per_bar",
        atr_period=3,
        big_body_atr_mult=0.01,
        volume_surge_mult=1.0,
        volume_baseline_min_samples=1,
    )
    features = _features(_bars(10), config)

    assert match_second_leg_pattern(features, 9, config) is None


# ---------------------------------------------------------------------------
# leg 口径：整条腿的净跌幅达标，单根只要求一半
# ---------------------------------------------------------------------------
def _leg_config(**overrides) -> SecondLegDownConfig:
    # ATR 窗口取 10：太短的话形态自己的大 K 线会把要衡量它的尺子撑大
    return SecondLegDownConfig(
        body_mode="leg",
        atr_period=10,
        atr_method="sma",
        leg_per_bar_fraction=0.5,
        volume_surge_mult=2.0,
        volume_baseline_min_samples=5,
        wick_body_ratio=0.5,
        **{"leg_body_atr_mult": 4.0, **overrides},
    )


def _two_bar_leg(first, last, volume=30.0):
    """两根阴线，上下影线都很短。"""
    bars = _bars()
    for index, (open_price, close_price) in ((27, first), (28, last)):
        bars.loc[index, ["open", "high", "low", "close", "volume"]] = [
            open_price, open_price + 0.05, close_price - 0.05, close_price, volume
        ]
    return bars


def test_leg_mode_accepts_a_strong_leg_of_two_ordinary_bars() -> None:
    """两根各自过不了 per_bar 的门槛，但合起来这条腿够 —— 正是要接住的形状。"""
    config = _leg_config()
    bars = _two_bar_leg((108.0, 107.4), (107.4, 106.8))
    features = _features(bars, config)
    assert match_second_leg_pattern(features, 28, config) is not None

    # 同一批 K 线，出厂的 per_bar N=3 口径直接扔掉
    strict = SecondLegDownConfig(
        body_mode="per_bar",
        atr_period=10,
        atr_method="sma",
        big_body_atr_mult=3.0,
        volume_surge_mult=2.0,
        volume_baseline_min_samples=5,
        wick_body_ratio=0.5,
    )
    assert match_second_leg_pattern(
        _features(bars, strict), 28, strict
    ) is None


def test_leg_mode_rejects_a_spike_next_to_a_flat_bar() -> None:
    """腿的长度够，但第二根几乎不动 —— 单根下限把它挡在外面。"""
    config = _leg_config()
    bars = _two_bar_leg((108.0, 105.5), (105.5, 105.45))
    features = _features(bars, config)
    leg = float(features.loc[27, "open"]) - float(features.loc[28, "close"])
    atr_value = float(features.loc[28, "atr"])
    assert leg >= config.leg_body_atr_mult * atr_value       # 腿本身达标
    assert match_second_leg_pattern(features, 28, config) is None


def test_leg_mode_rejects_a_leg_that_is_too_short() -> None:
    config = _leg_config(leg_body_atr_mult=40.0)
    bars = _two_bar_leg((108.0, 107.4), (107.4, 106.8))
    features = _features(bars, config)
    assert match_second_leg_pattern(features, 28, config) is None


def test_leg_spans_all_three_bars_of_the_three_bar_pattern() -> None:
    """三根形态的腿是 t-2 开盘 → t 收盘，中间的横盘不该把它切断。"""
    config = _leg_config()
    bars = _bars()
    bars.loc[26, ["open", "high", "low", "close", "volume"]] = [
        108.0, 108.05, 106.75, 106.8, 30.0
    ]
    bars.loc[27, ["open", "high", "low", "close", "volume"]] = [
        106.8, 106.85, 106.70, 106.75, 12.0      # 十字星
    ]
    bars.loc[28, ["open", "high", "low", "close", "volume"]] = [
        106.75, 106.80, 105.45, 105.5, 30.0
    ]
    features = _features(bars, config)
    match = match_second_leg_pattern(features, 28, config)
    assert match is not None
    assert match.pattern_type == "three_bar"
    assert match.first_index == 26
    first_open = float(features.loc[26, "open"])
    last_close = float(features.loc[28, "close"])
    atr_value = float(features.loc[28, "atr"])
    assert first_open - last_close >= config.leg_body_atr_mult * atr_value


def test_sma_and_wilder_atr_differ_and_both_stay_causal() -> None:
    bars = _bars()
    bars.loc[10, ["high", "low"]] = [1_000.0, 1.0]     # 一根暴力 K 线
    def build(method):
        return build_second_leg_features(
            bars, _sessions(),
            SecondLegDownConfig(atr_period=5, atr_method=method),
        )
    clean = build_second_leg_features(
        _bars(), _sessions(),
        SecondLegDownConfig(atr_period=5, atr_method="sma"),
    )
    sma, wilder = build("sma"), build("wilder")
    # 两条都只看到 t-1 为止：第 10 根自己的暴力不能进第 10 根的 ATR
    assert sma.loc[10, "atr"] == clean.loc[10, "atr"]
    assert wilder.loc[10, "atr"] == clean.loc[10, "atr"]
    # 暴力过去 5 根之后简单平均把它整个丢掉，Wilder 还拖着尾巴
    assert sma.loc[16, "atr"] == clean.loc[16, "atr"]
    assert wilder.loc[16, "atr"] > clean.loc[16, "atr"]


# ---------------------------------------------------------------------------
# 时间窗判在"下单那一刻"，不是形态收尾那一根
# ---------------------------------------------------------------------------
def _place_leg(bars, last_index, drop=0.6, volume=30.0):
    """在 last_index 处放一条合格的腿，价格接着当前水平走，不制造跳空。"""
    base = float(bars.loc[last_index - 2, "close"])
    for index, open_price in (
        (last_index - 1, base),
        (last_index, base - drop),
    ):
        close_price = open_price - drop
        bars.loc[index, ["open", "high", "low", "close", "volume"]] = [
            open_price, open_price + 0.05, close_price - 0.05, close_price, volume
        ]
    return bars


def _morning_bars(rows=40):
    """前一日尾盘 + 次日早盘：ATR / 成交量基线才有历史可用。"""
    bars = _bars(rows)
    morning = 10
    stamps = list(
        pd.date_range("2026-01-05 14:51", periods=rows - morning,
                      freq="min", tz="Asia/Shanghai")
    ) + list(
        pd.date_range("2026-01-06 09:01", periods=morning,
                      freq="min", tz="Asia/Shanghai")
    )
    bars["bar_end"] = stamps
    return bars


def test_open_block_measures_the_order_minute_not_the_signal_bar() -> None:
    """开盘满 10 分钟的那一根收尾 → 单子挂在第 11 分钟 → 应该放行。

    真实例子：BU 2026-06-25 的 09:09/09:10 两根把八道门过了七道，只因为
    09:10 距开盘正好 10 分钟被扔掉，而单子本来就是挂在 09:11 的。
    """
    config = _leg_config(entry_block_minutes_after_open=10)
    bars = _place_leg(_morning_bars(), 39)
    features = _features(bars, config)
    assert int(features.loc[39, "minutes_since_open"]) == 10
    assert match_second_leg_pattern(features, 39, config) is not None


def test_open_block_still_rejects_inside_the_window() -> None:
    """09:09 收尾 → 单子挂在 09:10，正好第 10 分钟，仍在窗内。"""
    config = _leg_config(entry_block_minutes_after_open=10)
    bars = _place_leg(_morning_bars(), 38)
    features = _features(bars, config)
    assert int(features.loc[38, "minutes_since_open"]) == 9
    assert match_second_leg_pattern(features, 38, config) is None


def test_close_block_is_not_relaxed() -> None:
    """收盘侧管的还是建仓时刻：下单那一分钟距收盘不足 20 分钟就不建仓。"""
    config = _leg_config(entry_block_minutes_before_close=20)
    rows = 40
    bars = _place_leg(_bars(rows), rows - 1)
    bars["bar_end"] = pd.date_range(
        "2026-01-05 14:39", periods=rows, freq="min", tz="Asia/Shanghai",
    ) - pd.Timedelta(minutes=rows - 1)
    features = _features(bars, config)
    last = rows - 1
    assert int(features.loc[last, "minutes_until_close"]) == 21
    assert match_second_leg_pattern(features, last, config) is not None

    # 同一条腿晚一分钟：下单那刻距收盘只剩 20 分钟 → 拒绝
    later = features.copy()
    later["minutes_until_close"] = later["minutes_until_close"] - 1
    assert match_second_leg_pattern(later, last, config) is None


def test_daily_trend_uses_only_the_last_completed_daily_bar() -> None:
    config = _leg_config()
    bars = _place_leg(_bars(), 28)

    features = _features(
        bars,
        config,
        daily_bars=_daily_bars(include_future=True),
    )

    last = features.loc[28]
    assert last["daily_feature_asof"] == pd.Timestamp(
        "2026-01-04 15:00", tz="Asia/Shanghai"
    )
    assert int(last["daily_direction"]) == -1
    assert last["daily_ema5"] < last["daily_ema10"] < last["daily_ema20"]
    assert match_second_leg_pattern(features, 28, config) is not None


def test_pattern_requires_daily_bearish_ema_alignment() -> None:
    config = _leg_config()
    bars = _place_leg(_bars(), 28)

    features = _features(bars, config, daily_bars=_daily_bars(bearish=False))

    assert int(features.loc[28, "daily_direction"]) == 1
    assert match_second_leg_pattern(features, 28, config) is None


def test_volume_filter_can_be_disabled_without_skipping_volume_audit() -> None:
    bars = _two_bar_leg((108.0, 107.4), (107.4, 106.8), volume=10.0)
    enabled = _leg_config(volume_filter_enabled=True)
    disabled = _leg_config(volume_filter_enabled=False)

    enabled_features = _features(bars, enabled)
    disabled_features = _features(bars, disabled)

    assert match_second_leg_pattern(enabled_features, 28, enabled) is None
    assert disabled_features.loc[28, "volume_baseline"] == 10.0
    assert match_second_leg_pattern(disabled_features, 28, disabled) is not None
