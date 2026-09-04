from __future__ import annotations

import pytest

from cta.strategy.brooks.cycle_v1.backtest.timeframes import (
    TimeframeSet,
    parse_timeframe,
)


@pytest.mark.parametrize(
    ("value", "minutes", "canonical"),
    [
        ("5m", 5, "5min"),
        ("15min", 15, "15min"),
        ("1h", 60, "1hour"),
        ("4hour", 240, "4hour"),
    ],
)
def test_parse_timeframe_accepts_only_minute_and_hour_units(
    value: str,
    minutes: int,
    canonical: str,
) -> None:
    parsed = parse_timeframe(value)

    assert parsed.minutes == minutes
    assert parsed.canonical == canonical


@pytest.mark.parametrize("value", ["", "0min", "1d", "30", "1.5h", "hour"])
def test_parse_timeframe_rejects_ambiguous_or_nonpositive_values(value: str) -> None:
    with pytest.raises(ValueError, match="timeframe"):
        parse_timeframe(value)


def test_timeframe_set_requires_strict_long_medium_short_order() -> None:
    timeframes = TimeframeSet.from_values("1hour", "30min", "5min")

    assert timeframes.minutes == (60, 30, 5)

    with pytest.raises(ValueError, match="long > medium > short"):
        TimeframeSet.from_values("30min", "30min", "5min")
