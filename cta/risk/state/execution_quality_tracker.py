"""Execution quality rolling tracker (W10)."""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass
from typing import TYPE_CHECKING

import pandas as pd

from cta.risk.base import normalize_symbol

if TYPE_CHECKING:
    from cta.risk.sizing.config import ExecutionQualityConfig


@dataclass(frozen=True)
class ExecutionQualityMetrics:
    sample_count: int
    slippage_ratio: float
    reject_rate: float
    avg_price_deviation_pct: float


@dataclass(frozen=True)
class _ExecutionEvent:
    dt: pd.Timestamp
    expected_slippage_bp: float
    actual_slippage_bp: float
    is_rejected: bool
    price_deviation_pct: float


class ExecutionQualityTracker:
    """Maintain rolling execution quality stats per symbol."""

    def __init__(self, cfg: "ExecutionQualityConfig | None" = None) -> None:
        if cfg is None:
            from cta.risk.sizing.config import ExecutionQualityConfig

            cfg = ExecutionQualityConfig()
        self.cfg = cfg
        self._events: dict[str, deque[_ExecutionEvent]] = {}

    def on_execution(self, payload: dict) -> None:
        symbol = normalize_symbol(payload.get("symbol"))
        if not symbol:
            return
        dt = pd.Timestamp(payload.get("dt", pd.Timestamp.now()))
        event = _ExecutionEvent(
            dt=dt,
            expected_slippage_bp=abs(_to_float(payload.get("expected_slippage_bp", 0.0))),
            actual_slippage_bp=abs(_to_float(payload.get("actual_slippage_bp", 0.0))),
            is_rejected=bool(payload.get("is_rejected", False)),
            price_deviation_pct=abs(_to_float(payload.get("price_deviation_pct", 0.0))),
        )
        dq = self._events.setdefault(symbol, deque())
        dq.append(event)
        self._evict_for(symbol, now=dt)

    def metrics(
        self,
        symbol: str,
        now: pd.Timestamp | None = None,
    ) -> ExecutionQualityMetrics:
        sym = normalize_symbol(symbol)
        if not sym:
            return ExecutionQualityMetrics(0, 0.0, 0.0, 0.0)
        self._evict_for(sym, now=now)
        dq = self._events.get(sym)
        if not dq:
            return ExecutionQualityMetrics(0, 0.0, 0.0, 0.0)
        n = len(dq)
        expected_mean = sum(e.expected_slippage_bp for e in dq) / n
        actual_mean = sum(e.actual_slippage_bp for e in dq) / n
        reject_rate = sum(1 for e in dq if e.is_rejected) / n
        avg_deviation = sum(e.price_deviation_pct for e in dq) / n
        denom = max(expected_mean, 1e-9)
        ratio = actual_mean / denom
        return ExecutionQualityMetrics(
            sample_count=n,
            slippage_ratio=float(ratio),
            reject_rate=float(reject_rate),
            avg_price_deviation_pct=float(avg_deviation),
        )

    def _evict_for(self, symbol: str, *, now: pd.Timestamp | None = None) -> None:
        dq = self._events.get(symbol)
        if not dq:
            return
        ref = pd.Timestamp(now) if now is not None else pd.Timestamp(dq[-1].dt)
        cutoff = ref - pd.Timedelta(days=int(self.cfg.rolling_window_days))
        while dq and pd.Timestamp(dq[0].dt) < cutoff:
            dq.popleft()


def _to_float(v: object) -> float:
    try:
        return float(v)
    except (TypeError, ValueError):
        return 0.0


__all__ = ["ExecutionQualityMetrics", "ExecutionQualityTracker"]
