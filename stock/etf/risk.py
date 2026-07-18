from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum

import numpy as np
import pandas as pd

from .config import StrategyConfig


class MarketState(StrEnum):
    """Confirmed portfolio exposure regime."""

    RISK_ON = "risk_on"
    CAUTION = "caution"
    RISK_OFF = "risk_off"


@dataclass
class MarketStateTracker:
    """Promote a candidate regime only after consecutive confirmations."""

    state: MarketState
    confirmation_days: int
    candidate: MarketState | None = None
    candidate_days: int = 0

    def advance(self, candidate: MarketState) -> MarketState:
        if candidate == self.state:
            self.candidate = None
            self.candidate_days = 0
            return self.state
        if candidate != self.candidate:
            self.candidate = candidate
            self.candidate_days = 1
            return self.state
        self.candidate_days += 1
        if self.candidate_days >= self.confirmation_days:
            self.state = candidate
            self.candidate = None
            self.candidate_days = 0
        return self.state


def calculate_breadth(daily: pd.DataFrame) -> float | None:
    """Return the share of valid trading ETFs closing above EMA20."""
    if daily.empty or not {"close", "ema20"}.issubset(daily.columns):
        return None
    trading = (
        daily["is_trading"].fillna(False).astype(bool)
        if "is_trading" in daily
        else pd.Series(True, index=daily.index)
    )
    close = pd.to_numeric(daily["close"], errors="coerce")
    ema20 = pd.to_numeric(daily["ema20"], errors="coerce")
    valid = trading & np.isfinite(close) & np.isfinite(ema20)
    if not valid.any():
        return None
    return float((close.loc[valid] > ema20.loc[valid]).mean())


def classify_market_candidate(
    benchmark_row: pd.Series,
    broad_risk_on: bool,
    breadth: float | None,
    config: StrategyConfig,
) -> MarketState:
    """Classify one signal day before persistence confirmation."""
    if broad_risk_on:
        return MarketState.RISK_ON
    below_ema20 = float(benchmark_row["close"]) < float(benchmark_row["ema20"])
    breadth_is_weak = breadth is None or breadth < config.risk_off_breadth_threshold
    if below_ema20 and breadth_is_weak:
        return MarketState.RISK_OFF
    return MarketState.CAUTION
