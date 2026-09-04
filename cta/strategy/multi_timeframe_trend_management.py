"""Post-fill position management for the multi-timeframe trend strategy."""
from __future__ import annotations

from dataclasses import dataclass, replace
from enum import Enum
import math

import numpy as np
import pandas as pd

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.strategy.multi_timeframe_trend_rules import (
    SignalCandidate,
    advance_trailing_stop,
)


class ExitReason(str, Enum):
    NONE = ""
    STOP = "STOP"
    TARGET = "TARGET"
    DAILY_DIRECTION_INVALID = "DAILY_DIRECTION_INVALID"


@dataclass(frozen=True)
class PositionState:
    setup_type: str
    direction: int
    entry_price: float
    stop_price: float
    target_price: float = math.nan
    last_structure_known_at: object | None = None


@dataclass(frozen=True)
class ExitDecision:
    should_exit: bool
    reason: ExitReason = ExitReason.NONE
    price: float = math.nan


def open_position_from_fill(
    candidate: SignalCandidate,
    *,
    actual_entry_price: float,
    config: MultiTimeframeTrendConfig,
) -> PositionState:
    """Freeze post-fill stop and mode for trailing-stop-only management."""
    entry = float(actual_entry_price)
    if not np.isfinite(entry) or entry <= 0:
        raise ValueError("actual_entry_price must be finite and positive")
    if candidate.direction not in (-1, 1):
        raise ValueError("candidate direction must be -1 or 1")
    if candidate.direction * (entry - candidate.stop_price) <= 0:
        raise ValueError("structural stop must be on the loss side of actual fill")
    if candidate.setup_type not in {"always_in", "pullback_breakout"}:
        raise ValueError(f"unsupported setup_type={candidate.setup_type}")
    return PositionState(
        setup_type=candidate.setup_type,
        direction=candidate.direction,
        entry_price=entry,
        stop_price=candidate.stop_price,
        target_price=math.nan,
        last_structure_known_at=candidate.known_at,
    )


def manage_position_on_bar(
    state: PositionState,
    bar: pd.Series,
    *,
    daily_direction: int,
    config: MultiTimeframeTrendConfig,
    tick_size: float,
) -> tuple[PositionState, ExitDecision]:
    """Evaluate existing exits, then update a newly confirmed trailing stop."""
    if daily_direction not in (-1, 0, 1):
        raise ValueError("daily_direction must be -1, 0, or 1")
    values = {name: float(bar.get(name, np.nan)) for name in ("open", "high", "low", "close")}
    if not all(np.isfinite(value) for value in values.values()):
        raise ValueError("bar requires finite open/high/low/close")
    open_price = values["open"]
    if state.direction > 0:
        stop_hit = values["low"] <= state.stop_price
        stop_fill = min(open_price, state.stop_price)
    else:
        stop_hit = values["high"] >= state.stop_price
        stop_fill = max(open_price, state.stop_price)

    if stop_hit:
        return state, ExitDecision(True, ExitReason.STOP, float(stop_fill))
    if daily_direction != state.direction:
        return state, ExitDecision(True, ExitReason.DAILY_DIRECTION_INVALID)

    swing_kind = "low" if state.direction > 0 else "high"
    swing = float(bar.get(f"latest_swing_{swing_kind}", np.nan))
    atr_value = float(bar.get("atr14", np.nan))
    known_at = bar.get(f"latest_swing_{swing_kind}_known_at")
    if not np.isfinite(swing) or not np.isfinite(atr_value) or pd.isna(known_at):
        return state, ExitDecision(False)
    last_known = state.last_structure_known_at
    if last_known is not None and pd.Timestamp(known_at) <= pd.Timestamp(last_known):
        return state, ExitDecision(False)
    stop = advance_trailing_stop(
        current_stop=state.stop_price,
        direction=state.direction,
        confirmed_swing=swing,
        atr_value=atr_value,
        buffer_atr=config.trailing_buffer_atr,
        tick_size=tick_size,
    )
    return replace(state, stop_price=stop, last_structure_known_at=known_at), ExitDecision(False)


__all__ = [
    "ExitDecision",
    "ExitReason",
    "PositionState",
    "manage_position_on_bar",
    "open_position_from_fill",
]
