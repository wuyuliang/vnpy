"""Causal one-minute features and Second Leg Down pattern rules."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import pandas as pd

from cta.feature.trend import ema
from cta.strategy.common.bar_shapes import (
    bearish_ema_alignment,
    breaks_structure_low,
    causal_atr,
    causal_volume_baseline,
    has_small_lower_wick,
    has_small_upper_wick,
    is_big_bear_body,
    is_leg_drop,
    is_small_body,
    is_volume_surge,
)
from cta.strategy.common.session_edges import (
    is_segment_first_bar,
    minutes_since_segment_open,
    minutes_until_segment_close,
)

from .config import SecondLegDownConfig


@dataclass(frozen=True)
class PatternMatch:
    pattern_type: str
    first_index: int
    last_index: int


def build_second_leg_features(
    minute_bars: pd.DataFrame,
    sessions: tuple[Any, ...],
    config: SecondLegDownConfig,
) -> pd.DataFrame:
    """Build features available at each completed one-minute bar."""
    required = {"bar_end", "open", "high", "low", "close", "volume"}
    missing = sorted(required.difference(minute_bars.columns))
    if missing:
        raise ValueError("minute bars are missing: " + ",".join(missing))
    frame = minute_bars.copy().sort_values("bar_end", kind="stable").reset_index(
        drop=True
    )
    frame["bar_end"] = pd.to_datetime(frame["bar_end"], errors="raise")
    if frame["bar_end"].dt.tz is None:
        raise ValueError("bar_end must be timezone-aware")
    frame["atr"] = causal_atr(
        frame["high"],
        frame["low"],
        frame["close"],
        period=config.atr_period,
        method=config.atr_method,
    )
    frame["ema_fast"] = ema(frame["close"], config.ema_fast)
    frame["ema_mid"] = ema(frame["close"], config.ema_mid)
    frame["ema_slow"] = ema(frame["close"], config.ema_slow)
    frame["is_segment_first"] = frame["bar_end"].map(
        lambda value: is_segment_first_bar(value, sessions)
    )
    baseline, samples = causal_volume_baseline(
        frame["volume"],
        frame["is_segment_first"],
        window=config.volume_baseline_bars,
    )
    frame["volume_baseline"] = baseline
    frame["volume_baseline_samples"] = samples
    frame["minutes_since_open"] = frame["bar_end"].map(
        lambda value: minutes_since_segment_open(value, sessions)
    )
    frame["minutes_until_close"] = frame["bar_end"].map(
        lambda value: minutes_until_segment_close(value, sessions)
    )
    frame["structure_low"] = (
        frame["low"]
        .shift(1)
        .rolling(config.structure_lookback_bars, min_periods=config.structure_lookback_bars)
        .min()
    )
    return frame


def _body_strength_ok(
    first: Any,
    last: Any,
    config: SecondLegDownConfig,
) -> bool:
    """Check the pattern's downward push under the configured body mode.

    ``per_bar`` asks each of the two big bars to clear the full multiple on its
    own. ``leg`` asks the whole leg — first bar's open down to the last bar's
    close, spanning two or three bars — to clear it, with each big bar still
    clearing ``leg_per_bar_fraction`` of it so one spike beside a flat bar
    cannot pass. The leg reading is what the setup is actually about, and it
    admits the common "big bar plus follow-through" shape that the per-bar
    reading throws away.
    """
    if config.body_mode == "per_bar":
        return all(
            is_big_bear_body(
                float(row["open"]),
                float(row["close"]),
                float(row["atr"]),
                config.big_body_atr_mult,
            )
            for row in (first, last)
        )
    # ATR 取最后一根的：整条腿用同一个尺子量，否则第一根和最后一根会用
    # 两把不同刻度的尺，腿越长偏差越大。
    atr_value = float(last["atr"])
    if not is_leg_drop(
        float(first["open"]),
        float(last["close"]),
        atr_value,
        config.leg_body_atr_mult,
    ):
        return False
    per_bar_mult = config.leg_body_atr_mult * config.leg_per_bar_fraction
    return all(
        is_big_bear_body(
            float(row["open"]),
            float(row["close"]),
            atr_value,
            per_bar_mult,
        )
        for row in (first, last)
    )


def _common_pattern_rules(
    frame: pd.DataFrame,
    first_index: int,
    last_index: int,
    config: SecondLegDownConfig,
) -> bool:
    first = frame.iloc[first_index]
    last = frame.iloc[last_index]
    if not bearish_ema_alignment(
        float(last["ema_fast"]), float(last["ema_mid"]), float(last["ema_slow"])
    ):
        return False
    if not _body_strength_ok(first, last, config):
        return False
    for row in (first, last):
        if not is_volume_surge(
            float(row["volume"]),
            float(row["volume_baseline"]),
            int(row["volume_baseline_samples"]),
            config.volume_surge_mult,
            config.volume_baseline_min_samples,
        ):
            return False
    if not has_small_upper_wick(
        float(first["open"]),
        float(first["high"]),
        float(first["close"]),
        config.wick_body_ratio,
    ):
        return False
    if not has_small_lower_wick(
        float(last["open"]),
        float(last["close"]),
        float(last["low"]),
        config.wick_body_ratio,
    ):
        return False
    since_open = last["minutes_since_open"]
    until_close = last["minutes_until_close"]
    if pd.isna(since_open) or pd.isna(until_close):
        return False
    # 时间窗管的是**下单那一刻**，不是形态收尾那一根。订单永远从下一根 1 分钟
    # K 线才活，所以判据要挪一分钟：09:10 收尾的形态，单子挂在 09:11，距开盘
    # 11 分钟，不该被"开盘 10 分钟内不交易"吃掉。之前按形态根判、且用 <=，
    # 等于把边界上的那一根白白作废，而开盘头十分钟恰好是大实体放量最密集的地方。
    order_since_open = int(since_open) + 1
    order_until_close = int(until_close) - 1
    if order_since_open <= config.entry_block_minutes_after_open:
        return False
    # 收盘侧不放宽：不在收盘前 20 分钟**建仓**，说的就是下单那一刻
    if order_until_close < config.entry_block_minutes_before_close:
        return False
    structure_low = float(first["structure_low"])
    structure_break = breaks_structure_low(float(last["low"]), structure_low)
    return bool(structure_break or not config.require_structure_break)


def match_second_leg_pattern(
    frame: pd.DataFrame,
    last_index: int,
    config: SecondLegDownConfig,
) -> PatternMatch | None:
    """Match a two- or three-bar pattern ending at ``last_index``."""
    if last_index >= 1 and _common_pattern_rules(
        frame, last_index - 1, last_index, config
    ):
        return PatternMatch("two_bar", last_index - 1, last_index)
    if not config.allow_three_bar_pattern or last_index < 2:
        return None
    first_index = last_index - 2
    middle = frame.iloc[last_index - 1]
    first = frame.iloc[first_index]
    if not is_small_body(
        float(middle["open"]),
        float(middle["close"]),
        float(middle["atr"]),
        config.small_body_atr_mult,
    ):
        return None
    if float(middle["high"]) > float(first["high"]):
        return None
    if not _common_pattern_rules(frame, first_index, last_index, config):
        return None
    return PatternMatch("three_bar", first_index, last_index)


def safe_ratio(numerator: float, denominator: float) -> float:
    """Return a finite audit ratio or NaN when the denominator is unusable."""
    if not math.isfinite(denominator) or denominator <= 0:
        return math.nan
    return numerator / denominator
