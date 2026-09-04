"""Strict minute/hour timeframe values for the cycle_v1 CLI."""

from __future__ import annotations

from dataclasses import dataclass
import re


_TIMEFRAME_PATTERN = re.compile(r"^(?P<count>[1-9]\d*)(?P<unit>m|min|h|hour)$")


@dataclass(frozen=True)
class Timeframe:
    minutes: int
    canonical: str

    def __post_init__(self) -> None:
        if self.minutes <= 0 or not self.canonical:
            raise ValueError("timeframe must be positive and named")


@dataclass(frozen=True)
class TimeframeSet:
    long: Timeframe
    medium: Timeframe
    short: Timeframe

    def __post_init__(self) -> None:
        if not self.long.minutes > self.medium.minutes > self.short.minutes:
            raise ValueError("timeframes must satisfy long > medium > short")

    @classmethod
    def from_values(cls, long: str, medium: str, short: str) -> TimeframeSet:
        return cls(
            long=parse_timeframe(long),
            medium=parse_timeframe(medium),
            short=parse_timeframe(short),
        )

    @property
    def minutes(self) -> tuple[int, int, int]:
        return self.long.minutes, self.medium.minutes, self.short.minutes

    def to_dict(self) -> dict[str, str]:
        return {
            "long": self.long.canonical,
            "medium": self.medium.canonical,
            "short": self.short.canonical,
        }


def parse_timeframe(value: str) -> Timeframe:
    normalized = str(value).strip().lower()
    match = _TIMEFRAME_PATTERN.fullmatch(normalized)
    if match is None:
        raise ValueError("timeframe must use a positive integer with m/min or h/hour")
    count = int(match.group("count"))
    unit = match.group("unit")
    if unit in {"m", "min"}:
        return Timeframe(count, f"{count}min")
    return Timeframe(count * 60, f"{count}hour")


__all__ = ["Timeframe", "TimeframeSet", "parse_timeframe"]
