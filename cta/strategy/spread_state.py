"""Runtime spread-statistics state."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field

import numpy as np

from cta.config.spread_pair_registry import SpreadPair


@dataclass
class SpreadState:
    """Rolling spread state for one pair."""

    pair: SpreadPair
    rolling_window_days: int
    spread_history: deque[float] = field(default_factory=deque)

    def __post_init__(self) -> None:
        window = int(self.rolling_window_days)
        if window <= 1:
            raise ValueError("rolling_window_days must be > 1")
        self.rolling_window_days = window
        if not isinstance(self.spread_history, deque):
            self.spread_history = deque(self.spread_history)

    def is_warmup_done(self) -> bool:
        return len(self.spread_history) >= int(self.rolling_window_days)

    def update(self, new_spread: float) -> None:
        value = float(new_spread)
        if not np.isfinite(value):
            return
        self.spread_history.append(value)
        while len(self.spread_history) > int(self.rolling_window_days):
            self.spread_history.popleft()

    def zscore(self, current_spread: float) -> float:
        if not self.is_warmup_done():
            return float("nan")
        window = np.asarray(list(self.spread_history), dtype=float)
        std = float(np.nanstd(window, ddof=0))
        if not np.isfinite(std) or std <= 0.0:
            return float("nan")
        mean = float(np.nanmean(window))
        return float((float(current_spread) - mean) / std)


__all__ = ["SpreadState"]

