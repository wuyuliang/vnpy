"""Daily VaR budget state tracker (W10)."""
from __future__ import annotations

import pandas as pd

from cta.risk.base import normalize_cluster


class DailyVaRTracker:
    """Track account and cluster daily pnl with session-level reset."""

    def __init__(self, *, reset_at_session_open: bool = True) -> None:
        self.reset_at_session_open = bool(reset_at_session_open)
        self._trading_day: pd.Timestamp | None = None
        self._account_daily_pnl: float = 0.0
        self._cluster_daily_pnl: dict[str, float] = {}

    def on_trade(self, trade: dict) -> None:
        dt = pd.Timestamp(trade.get("dt", pd.Timestamp.now()))
        self._ensure_day(dt)
        cluster = normalize_cluster(trade.get("cluster"))
        if not cluster:
            cluster = "other"
        try:
            pnl = float(trade.get("net_pnl", 0.0))
        except (TypeError, ValueError):
            pnl = 0.0
        self._account_daily_pnl += pnl
        self._cluster_daily_pnl[cluster] = self._cluster_daily_pnl.get(cluster, 0.0) + pnl

    def account_daily_pnl(self, now: pd.Timestamp) -> float:
        self._ensure_day(pd.Timestamp(now))
        return float(self._account_daily_pnl)

    def cluster_daily_pnl(self, cluster: str, now: pd.Timestamp) -> float:
        self._ensure_day(pd.Timestamp(now))
        cl = normalize_cluster(cluster) or "other"
        return float(self._cluster_daily_pnl.get(cl, 0.0))

    def _ensure_day(self, now: pd.Timestamp) -> None:
        day = pd.Timestamp(now).normalize()
        if self._trading_day is None:
            self._trading_day = day
            return
        if self.reset_at_session_open and day != self._trading_day:
            self._trading_day = day
            self._account_daily_pnl = 0.0
            self._cluster_daily_pnl = {}


__all__ = ["DailyVaRTracker"]
