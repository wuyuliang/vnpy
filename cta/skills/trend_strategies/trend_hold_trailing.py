"""§03-05 trend hold and trailing."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd


@dataclass
class TrailingState:
    stop_price: float
    stage: Literal["initial", "break_even", "chandelier", "micro_channel"]
    R_multiple: float


def update_trailing(
    state: TrailingState,
    bar: pd.Series,
    entry_price: float,
    entry_stop: float,
    atr: float,
) -> TrailingState:
    """
    Update trailing stage and stop by R progression.

    Stages:
    - initial: R < 1
    - break_even: 1 <= R < 2
    - chandelier: 2 <= R < 3
    - micro_channel: R >= 3
    """
    high = float(bar.get("high", np.nan))
    low = float(bar.get("low", np.nan))
    close = float(bar.get("close", np.nan))
    if not np.isfinite(close):
        return state

    risk = max(abs(float(entry_price) - float(entry_stop)), 1e-9)
    r = (close - float(entry_price)) / risk
    a = max(float(atr), 1e-9)

    stage: Literal["initial", "break_even", "chandelier", "micro_channel"] = "initial"
    stop = float(state.stop_price)
    if r >= 3.0:
        stage = "micro_channel"
        if np.isfinite(low):
            stop = max(stop, low)
    elif r >= 2.0:
        stage = "chandelier"
        if np.isfinite(high):
            stop = max(stop, high - 3.0 * a)
    elif r >= 1.0:
        stage = "break_even"
        stop = max(stop, float(entry_price))
    else:
        stage = "initial"
        stop = min(stop, float(entry_stop)) if state.stage == "initial" else stop

    return TrailingState(stop_price=float(stop), stage=stage, R_multiple=float(r))


def decide_add_on(
    core_position_R: float,
    fresh_signal: dict,
    max_add_count: int = 2,
) -> Optional[dict]:
    """Add-on sizing decision from fresh continuation setup."""
    if int(fresh_signal.get("add_count", 0)) >= int(max_add_count):
        return None
    if (not bool(fresh_signal.get("valid", False))) or float(core_position_R) < 0.5:
        return None
    return {"size": float(fresh_signal.get("size", 0.5))}


def fast_exit_if_stalled(
    bars_since_entry: int,
    current_R: float,
    max_bars: int = 5,
) -> bool:
    """Exit stalled trade if time exceeded and progress too small."""
    return int(bars_since_entry) > int(max_bars) and float(current_R) < 0.5

