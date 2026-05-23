"""Shared position+trend state snapshot helpers."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

import numpy as np
import pandas as pd


def _safe_float(value: Any, default: float = float("nan")) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    return v if np.isfinite(v) else default


def _side_sign(side: str) -> float:
    return -1.0 if str(side).strip().lower() == "short" else 1.0


def regime_score(regime_label: str) -> float:
    label = str(regime_label).strip().lower()
    if label == "trend_up":
        return 1.0
    if label == "trend_down":
        return -1.0
    if label == "expansion":
        return 0.5
    if label == "expansion_down":
        return -0.5
    return 0.0


def compute_trend_score(
    *,
    ma_alignment: int,
    regime_label: str,
    current_pnl_pct: float,
) -> float:
    ma_term = 0.5 * np.sign(ma_alignment) * min(abs(int(ma_alignment)) / 2.0, 1.0)
    regime_term = 0.3 * regime_score(regime_label)
    pnl_term = 0.2 * np.sign(current_pnl_pct) * min(abs(float(current_pnl_pct)) / 0.05, 1.0)
    score = float(ma_term + regime_term + pnl_term)
    return float(np.clip(score, -1.0, 1.0))


@dataclass(frozen=True)
class PositionTrendState:
    """Position-level state snapshot used by adaptive exits/gates."""

    symbol: str
    side: str
    entry_price: float
    current_price: float
    current_pnl_pct: float
    bars_held: int
    ma_alignment: int
    regime_label: str
    realized_vol_20d: float
    trend_score: float

    def is_valid(self) -> bool:
        if str(self.side).strip().lower() not in {"long", "short"}:
            return False
        if not np.isfinite(float(self.entry_price)) or float(self.entry_price) <= 0.0:
            return False
        if not np.isfinite(float(self.current_price)) or float(self.current_price) <= 0.0:
            return False
        if not np.isfinite(float(self.current_pnl_pct)):
            return False
        if not np.isfinite(float(self.trend_score)):
            return False
        return True


def compute_position_trend_state(
    position: dict[str, Any],
    bar: dict[str, Any] | pd.Series,
    *,
    ma_alignment_lookup: Callable[[str, pd.Timestamp], int],
    regime_label_lookup: Callable[[str, pd.Timestamp], str],
    realized_vol_lookup: Callable[[str, pd.Timestamp], float],
) -> PositionTrendState:
    """Build a ``PositionTrendState`` from position snapshot + current bar."""
    row = dict(bar)
    symbol = str(position.get("symbol", "")).upper()
    side = str(position.get("side", position.get("direction", "long"))).strip().lower()
    entry_price = _safe_float(position.get("entry_price", np.nan))
    current_price = _safe_float(row.get("close", row.get("price", np.nan)))
    sign = _side_sign(side)
    if np.isfinite(entry_price) and entry_price > 0.0 and np.isfinite(current_price):
        current_pnl_pct = (current_price - entry_price) / entry_price * sign
    else:
        current_pnl_pct = float("nan")
    dt = pd.Timestamp(row.get("datetime"))
    ma_alignment = int(ma_alignment_lookup(symbol, dt))
    regime_label = str(regime_label_lookup(symbol, dt))
    realized_vol_20d = _safe_float(realized_vol_lookup(symbol, dt), default=float("nan"))
    trend = compute_trend_score(
        ma_alignment=ma_alignment,
        regime_label=regime_label,
        current_pnl_pct=current_pnl_pct,
    )
    bars_held = int(position.get("bars_held", 0) or 0)
    return PositionTrendState(
        symbol=symbol,
        side=side,
        entry_price=float(entry_price),
        current_price=float(current_price),
        current_pnl_pct=float(current_pnl_pct),
        bars_held=bars_held,
        ma_alignment=ma_alignment,
        regime_label=regime_label,
        realized_vol_20d=float(realized_vol_20d),
        trend_score=float(trend),
    )


__all__ = [
    "PositionTrendState",
    "compute_position_trend_state",
    "compute_trend_score",
    "regime_score",
]

