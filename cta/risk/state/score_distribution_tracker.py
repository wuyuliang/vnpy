"""Rolling score distribution tracker for drift monitoring (W9)."""
from __future__ import annotations

from collections import deque

import pandas as pd


def normalize_score_value(score: float | int | None) -> float | None:
    """Normalize score to [0, 1] probability domain.

    - [0, 1] values keep as-is
    - (1, 100] values are treated as percentile and converted by /100
    - NaN / invalid values return None
    """
    if score is None:
        return None
    try:
        value = float(score)
    except (TypeError, ValueError):
        return None
    if value != value:  # NaN
        return None
    if value > 1.0:
        value = value / 100.0
    if value < 0.0:
        value = 0.0
    if value > 1.0:
        value = 1.0
    return value


class ScoreDistributionTracker:
    """Track rolling score observations in a fixed time window."""

    def __init__(self, *, rolling_window_hours: int = 4) -> None:
        if rolling_window_hours < 1:
            raise ValueError(
                f"rolling_window_hours must be >= 1, got {rolling_window_hours}"
            )
        self.rolling_window_hours = int(rolling_window_hours)
        self._events: deque[tuple[pd.Timestamp, float]] = deque()

    def observe(self, score: float | int | None, dt: pd.Timestamp) -> bool:
        value = normalize_score_value(score)
        if value is None:
            return False
        ts = pd.Timestamp(dt)
        self._events.append((ts, value))
        self._evict(now=ts)
        return True

    def values(self, now: pd.Timestamp | None = None) -> list[float]:
        self._evict(now=now)
        return [v for _, v in self._events]

    def count(self, now: pd.Timestamp | None = None) -> int:
        self._evict(now=now)
        return len(self._events)

    def _evict(self, now: pd.Timestamp | None = None) -> None:
        if not self._events:
            return
        ref = pd.Timestamp(now) if now is not None else pd.Timestamp(self._events[-1][0])
        cutoff = ref - pd.Timedelta(hours=int(self.rolling_window_hours))
        while self._events and pd.Timestamp(self._events[0][0]) < cutoff:
            self._events.popleft()


__all__ = ["ScoreDistributionTracker", "normalize_score_value"]
