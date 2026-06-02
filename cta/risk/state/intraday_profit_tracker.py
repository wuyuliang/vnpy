"""Intraday profit high-water and give-back tracker (W9)."""
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

if TYPE_CHECKING:
    from cta.risk.sizing.config import ProfitGiveBackConfig


@dataclass(frozen=True)
class IntradayProfitState:
    trading_day: pd.Timestamp | None
    activated: bool
    triggered: bool
    current_pnl_pct: float
    peak_pnl_pct: float


class IntradayProfitTracker:
    """Track intraday pnl% high-water and give-back trigger state."""

    def __init__(self, cfg: "ProfitGiveBackConfig | None" = None) -> None:
        if cfg is None:
            from cta.risk.sizing.config import ProfitGiveBackConfig

            cfg = ProfitGiveBackConfig()
        self.cfg = cfg
        self._trading_day: pd.Timestamp | None = None
        self._activated = False
        self._triggered = False
        self._current_pnl_pct = 0.0
        self._peak_pnl_pct = 0.0

    def update(self, now: pd.Timestamp, pnl_pct: float) -> IntradayProfitState:
        ts = pd.Timestamp(now)
        day = ts.normalize()
        if self._trading_day is None:
            self._trading_day = day
        elif self.cfg.reset_at_session_open and day != self._trading_day:
            self._reset(day)
        current = float(pnl_pct)
        self._current_pnl_pct = current
        if current > self._peak_pnl_pct:
            self._peak_pnl_pct = current
        if not self._activated and current >= float(self.cfg.activation_pnl_pct):
            self._activated = True
            self._peak_pnl_pct = max(self._peak_pnl_pct, current)
        if self._activated and not self._triggered and self._peak_pnl_pct > 0.0:
            give_back = self._peak_pnl_pct - current
            trigger_threshold = self._peak_pnl_pct * float(self.cfg.give_back_ratio)
            if give_back >= trigger_threshold:
                self._triggered = True
        return self.snapshot()

    def snapshot(self) -> IntradayProfitState:
        return IntradayProfitState(
            trading_day=self._trading_day,
            activated=self._activated,
            triggered=self._triggered,
            current_pnl_pct=self._current_pnl_pct,
            peak_pnl_pct=self._peak_pnl_pct,
        )

    def is_triggered(self, now: pd.Timestamp | None = None) -> bool:
        if now is not None and self.cfg.reset_at_session_open and self._trading_day is not None:
            if pd.Timestamp(now).normalize() != self._trading_day:
                self._reset(pd.Timestamp(now).normalize())
        return self._triggered

    def _reset(self, day: pd.Timestamp) -> None:
        self._trading_day = day
        self._activated = False
        self._triggered = False
        self._current_pnl_pct = 0.0
        self._peak_pnl_pct = 0.0


__all__ = ["IntradayProfitState", "IntradayProfitTracker"]
