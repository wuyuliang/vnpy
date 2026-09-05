"""Causal one-timeframe feature frame shared by intraday price-action setups.

Every column here is answerable at the close of its own bar: ATR is shifted,
the volume baseline only sees prior bars, and the structure low excludes the
current bar. Strategies differ in how they *read* these columns, not in how
the columns are built, so the builder takes plain keyword arguments rather
than any one strategy's config class.
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from cta.feature.trend import ema
from cta.strategy.common.bar_shapes import causal_atr, causal_volume_baseline
from cta.strategy.common.session_edges import (
    is_segment_first_bar,
    minutes_since_segment_open,
    minutes_until_segment_close,
)

REQUIRED_BAR_COLUMNS = ("bar_end", "open", "high", "low", "close", "volume")

FEATURE_COLUMNS = (
    "atr",
    "ema_fast",
    "ema_mid",
    "ema_slow",
    "is_segment_first",
    "volume_baseline",
    "volume_baseline_samples",
    "minutes_since_open",
    "minutes_until_close",
    "structure_low",
)


def build_intraday_features(
    bars: pd.DataFrame,
    sessions: tuple[Any, ...],
    *,
    atr_period: int,
    atr_method: str = "sma",
    ema_fast: int = 5,
    ema_mid: int = 10,
    ema_slow: int = 20,
    volume_baseline_bars: int,
    structure_lookback_bars: int,
) -> pd.DataFrame:
    """Attach the causal feature columns to a sorted copy of ``bars``."""
    missing = sorted(set(REQUIRED_BAR_COLUMNS).difference(bars.columns))
    if missing:
        raise ValueError("bars are missing: " + ",".join(missing))
    frame = (
        bars.copy().sort_values("bar_end", kind="stable").reset_index(drop=True)
    )
    frame["bar_end"] = pd.to_datetime(frame["bar_end"], errors="raise")
    if frame["bar_end"].dt.tz is None:
        raise ValueError("bar_end must be timezone-aware")
    frame["atr"] = causal_atr(
        frame["high"], frame["low"], frame["close"],
        period=atr_period, method=atr_method,
    )
    frame["ema_fast"] = ema(frame["close"], ema_fast)
    frame["ema_mid"] = ema(frame["close"], ema_mid)
    frame["ema_slow"] = ema(frame["close"], ema_slow)
    frame["is_segment_first"] = frame["bar_end"].map(
        lambda value: is_segment_first_bar(value, sessions)
    )
    baseline, samples = causal_volume_baseline(
        frame["volume"], frame["is_segment_first"], window=volume_baseline_bars
    )
    frame["volume_baseline"] = baseline
    frame["volume_baseline_samples"] = samples
    frame["minutes_since_open"] = frame["bar_end"].map(
        lambda value: minutes_since_segment_open(value, sessions)
    )
    frame["minutes_until_close"] = frame["bar_end"].map(
        lambda value: minutes_until_segment_close(value, sessions)
    )
    # 结构低点排除当根：当根的低点是判定对象，不能同时当作被突破的基准
    frame["structure_low"] = (
        frame["low"]
        .shift(1)
        .rolling(structure_lookback_bars, min_periods=structure_lookback_bars)
        .min()
    )
    return frame


def entry_window_open(
    row: Any,
    *,
    block_minutes_after_open: int,
    block_minutes_before_close: int,
) -> bool:
    """Whether an order placed on the bar *after* ``row`` may be sent.

    The window governs the moment the order goes live, not the bar the setup
    finished on: orders always activate on the next execution bar, so a setup
    closing exactly ``block_minutes_after_open`` minutes into the session puts
    its order one minute later, which is outside the blackout.
    """
    since_open = row["minutes_since_open"]
    until_close = row["minutes_until_close"]
    if pd.isna(since_open) or pd.isna(until_close):
        return False
    if int(since_open) + 1 <= block_minutes_after_open:
        return False
    return int(until_close) - 1 >= block_minutes_before_close
