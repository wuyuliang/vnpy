"""Freeze scalp/swing/no-trade intent from the entry context."""
from __future__ import annotations

from .types import CycleSnapshot, MarketCycle, RangeSubtype, TradeMode


def select_trade_mode(
    cycle: CycleSnapshot,
    *,
    range_pct: float | None,
    lower_zone: float = 0.33,
    upper_zone: float = 0.67,
) -> TradeMode:
    if cycle.cycle in {MarketCycle.TRANSITION, MarketCycle.UNAVAILABLE}:
        return TradeMode.NO_TRADE
    if cycle.cycle is MarketCycle.TRADING_RANGE:
        if cycle.range_subtype is RangeSubtype.UNTRADEABLE_TIGHT:
            return TradeMode.NO_TRADE
        if cycle.range_subtype is RangeSubtype.TIGHT_BREAKOUT_MODE:
            return TradeMode.SWING
        if range_pct is None or lower_zone < range_pct < upper_zone:
            return TradeMode.NO_TRADE
        return TradeMode.SCALP
    return TradeMode.SWING


__all__ = ["select_trade_mode"]
