"""Optional bar-count exits shared by replay strategies."""
from __future__ import annotations

import math
from typing import Any

from cta.config.replay_common import BaseReplayConfig

from .drawdown import _peak_unrealized_r
from .models import _Position


def timed_exit_reason(
    position: _Position,
    bar: Any,
    config: BaseReplayConfig,
) -> str:
    """Fold the completed bar into time-exit state and return its pending reason."""
    no_progress_bars = int(config.no_progress_bars)
    follow_bars = int(config.follow_through_bars)
    if no_progress_bars <= 0 and follow_bars <= 0:
        return ""
    bars_since_entry = int(bar.get("_bar_index", 0)) - position.entry_bar_index
    if bars_since_entry <= 0:
        return ""

    if follow_bars > 0 and bars_since_entry <= follow_bars:
        atr_value = bar.get("atr", bar.get("atr14", math.nan))
        body = float(bar["open"]) - float(bar["close"])
        if (
            math.isfinite(float(atr_value))
            and float(atr_value) > 0
            and body >= float(config.follow_through_body_atr_mult) * float(atr_value)
        ):
            position.follow_through_seen = True

    peak_r = _peak_unrealized_r(position)
    if (
        no_progress_bars > 0
        and bars_since_entry >= no_progress_bars
        and math.isfinite(peak_r)
        and peak_r < float(config.no_progress_min_r)
    ):
        return "NO_PROGRESS_TIME_STOP"

    if (
        follow_bars > 0
        and bars_since_entry >= follow_bars
        and not position.follow_through_seen
    ):
        position.no_follow_through_target_active = True
    if (
        position.no_follow_through_target_active
        and math.isfinite(peak_r)
        and peak_r >= 1.0
    ):
        return "NO_FOLLOW_THROUGH_TARGET"
    return ""


__all__ = ["timed_exit_reason"]
