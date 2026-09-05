"""Causal features and pattern matching for the Brooks second leg down.

The read has three parts and the code follows them literally:

    ① 第一段   a swing low that was the lowest point of its own lookback,
               with enough range behind it to count as a leg
    ② 回抽     a rally off that low that neither fizzles nor undoes the leg
    ③ 第二段   an ordinary bear bar that closes near its low and takes out
               the swing low — this is the entry bar

Every value is read from completed bars only. The entry bar is judged at its
own close and the order lives from the next execution bar.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from cta.strategy.common.bar_shapes import (
    bearish_ema_alignment,
    causal_low_pullback_structure,
    has_small_lower_wick,
    is_volume_surge,
)
from cta.strategy.common.intraday_features import (
    build_intraday_features,
    entry_window_open,
)
from cta.strategy.common.setup_pipeline import PatternMatch

from .config import SecondLegBrooksConfig


def build_brooks_features(
    bars: pd.DataFrame,
    sessions: tuple[Any, ...],
    config: SecondLegBrooksConfig,
) -> pd.DataFrame:
    """Feature frame plus the rolling extremes the three parts are read from."""
    frame = build_intraday_features(
        bars,
        sessions,
        atr_period=config.atr_period,
        atr_method=config.atr_method,
        ema_fast=config.ema_fast,
        ema_mid=config.ema_mid,
        ema_slow=config.ema_slow,
        volume_baseline_bars=config.volume_baseline_bars,
        structure_lookback_bars=config.structure_lookback_bars,
    )
    # 第一段、回抽、入场根必须落在同一个交易时段内。
    frame["segment_id"] = frame["is_segment_first"].astype(bool).cumsum()
    structure = causal_low_pullback_structure(
        frame["low"],
        frame["high"],
        frame["segment_id"],
        frame["atr"],
        first_leg_lookback=config.first_leg_lookback,
        first_leg_atr_mult=config.first_leg_atr_mult,
        pullback_min_bars=config.pullback_min_bars,
        pullback_max_bars=config.pullback_max_bars,
        pullback_min_ratio=config.pullback_min_ratio,
        pullback_max_ratio=config.pullback_max_ratio,
    )
    for column in structure:
        frame[column] = structure[column]
    return frame


def _entry_bar_ok(row: Any, config: SecondLegBrooksConfig) -> bool:
    """The entry bar's own requirements — deliberately modest."""
    atr = float(row["atr"])
    if not np.isfinite(atr) or atr <= 0:
        return False
    if not bearish_ema_alignment(
        float(row["ema_fast"]), float(row["ema_mid"]), float(row["ema_slow"])
    ):
        return False
    body = float(row["close"]) - float(row["open"])
    if body >= 0 or abs(body) < config.entry_body_atr_mult * atr:
        return False
    if not has_small_lower_wick(
        float(row["open"]), float(row["close"]), float(row["low"]),
        config.entry_lower_wick_ratio,
    ):
        return False
    if not is_volume_surge(
        float(row["volume"]),
        float(row["volume_baseline"]),
        int(row["volume_baseline_samples"]),
        config.entry_volume_mult,
        config.volume_baseline_min_samples,
    ):
        return False
    return entry_window_open(
        row,
        block_minutes_after_open=config.entry_block_minutes_after_open,
        block_minutes_before_close=config.entry_block_minutes_before_close,
    )


def match_second_leg(
    frame: pd.DataFrame,
    entry_index: int,
    config: SecondLegBrooksConfig,
) -> PatternMatch | None:
    """Match ① → ② → ③ ending on ``entry_index``, or return None.

    Candidate swing lows are scanned nearest-first: the most recent pullback
    is the one being resumed. Scanning from the far end instead would let an
    ancient leg claim an entry that belongs to a newer, shallower structure.
    """
    if entry_index < 1 or entry_index >= len(frame):
        return None
    entry = frame.iloc[entry_index]
    if not _entry_bar_ok(entry, config):
        return None
    if not bool(entry["pullback_matched"]):
        return None
    swing_index = int(entry["swing_index"])
    swing_low = float(entry["swing_low"])
    if config.require_break_of_swing_low and float(entry["low"]) >= swing_low:
        return None
    rally_high = float(entry["rally_high"])
    if rally_high <= float(entry["close"]):
        return None
    return PatternMatch(
        pattern_type="pullback",
        first_index=swing_index,
        last_index=entry_index,
        extras={
            "rally_high": rally_high,
            "first_leg": float(entry["first_leg"]),
            "pullback_ratio": float(entry["pullback_ratio"]),
            "pullback_bars": int(entry["pullback_bars"]),
            "swing_low": swing_low,
        },
    )


def iter_matches(frame: pd.DataFrame, config: SecondLegBrooksConfig):
    """Yield ``(entry_index, PatternMatch)`` for every completed setup.

    With ``one_candidate_per_structure`` (the default) each swing low/rally
    pair fires once. The bars right after a structure resumes keep re-matching
    it — same swing, same rally high, therefore the same stop — so emitting all
    of them would place the same trade several times over at several times the
    intended risk. The first bar to resume the move is the one the setup is
    about; the rest are the trade already running.
    """
    seen: set[tuple[int, float]] = set()
    for entry_index in np.flatnonzero(frame["pullback_matched"].to_numpy(dtype=bool)):
        match = match_second_leg(frame, entry_index, config)
        if match is None:
            continue
        if config.one_candidate_per_structure:
            key = (match.first_index, float(match.extras["rally_high"]))
            if key in seen:
                continue
            seen.add(key)
        yield entry_index, match
