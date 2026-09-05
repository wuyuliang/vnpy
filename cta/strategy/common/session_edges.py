"""Session-segment-relative timing for completed minute bars."""
from __future__ import annotations

from datetime import time
from typing import Any

import pandas as pd


def _minute_of_day(value: time) -> int:
    return value.hour * 60 + value.minute


def _segment_offsets(
    bar_end: Any,
    sessions: tuple[Any, ...],
) -> tuple[int, int] | None:
    timestamp = pd.Timestamp(bar_end)
    if timestamp.tz is None:
        raise ValueError("bar_end must be timezone-aware")
    minute = _minute_of_day(timestamp.timetz())
    for session in sessions:
        for segment in session.segments:
            start = _minute_of_day(segment.start)
            duration = (_minute_of_day(segment.end) - start) % 1440
            since_open = (minute - start) % 1440
            if since_open <= duration:
                return since_open, duration - since_open
    return None


def minutes_since_segment_open(
    bar_end: Any,
    sessions: tuple[Any, ...],
) -> int | None:
    """Return completed minutes since the containing segment opened."""
    offsets = _segment_offsets(bar_end, sessions)
    return None if offsets is None else offsets[0]


def minutes_until_segment_close(
    bar_end: Any,
    sessions: tuple[Any, ...],
) -> int | None:
    """Return minutes from the bar end until its segment closes."""
    offsets = _segment_offsets(bar_end, sessions)
    return None if offsets is None else offsets[1]


def is_segment_first_bar(
    bar_end: Any,
    sessions: tuple[Any, ...],
) -> bool:
    """Return whether a one-minute bar is the first completed segment bar."""
    return minutes_since_segment_open(bar_end, sessions) == 1
