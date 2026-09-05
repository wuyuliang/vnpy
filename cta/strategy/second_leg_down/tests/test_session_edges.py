from __future__ import annotations

from datetime import time

import pandas as pd

from cta.strategy.brooks.cycle_v1.instruments.sessions import (
    SessionSegment,
    SessionSpec,
)
from cta.strategy.common.session_edges import (
    is_segment_first_bar,
    minutes_since_segment_open,
    minutes_until_segment_close,
)


def _sessions() -> tuple[SessionSpec, ...]:
    return (
        SessionSpec(
            "day",
            False,
            (
                SessionSegment("morning", time(9), time(11, 30), time(9)),
                SessionSegment("afternoon", time(13, 30), time(15), time(13, 30)),
            ),
        ),
        SessionSpec(
            "night",
            True,
            (SessionSegment("night", time(21), time(2, 30), time(21)),),
        ),
    )


def test_day_segment_edges_use_relative_position() -> None:
    timestamp = pd.Timestamp("2026-01-05 13:31", tz="Asia/Shanghai")

    assert minutes_since_segment_open(timestamp, _sessions()) == 1
    assert minutes_until_segment_close(timestamp, _sessions()) == 89
    assert is_segment_first_bar(timestamp, _sessions())


def test_overnight_segment_edges_cross_midnight() -> None:
    timestamp = pd.Timestamp("2026-01-06 01:45", tz="Asia/Shanghai")

    assert minutes_since_segment_open(timestamp, _sessions()) == 285
    assert minutes_until_segment_close(timestamp, _sessions()) == 45
    assert not is_segment_first_bar(timestamp, _sessions())


def test_timestamp_outside_segments_has_no_edge_distance() -> None:
    timestamp = pd.Timestamp("2026-01-05 12:00", tz="Asia/Shanghai")

    assert minutes_since_segment_open(timestamp, _sessions()) is None
    assert minutes_until_segment_close(timestamp, _sessions()) is None
