"""Shared limit-move gating helpers used by OOT and event-driven engines."""
from __future__ import annotations


def is_limit_move_blocked_by_flags(
    *,
    side: str,
    is_limit_up_close: bool,
    is_limit_down_close: bool,
) -> bool:
    s = str(side).strip().lower()
    if s == "long":
        return bool(is_limit_up_close)
    if s == "short":
        return bool(is_limit_down_close)
    return False


def is_limit_move_blocked_by_ohlc(
    *,
    prev_close: float,
    next_open: float,
    next_high: float,
    next_low: float,
    side: str,
    limit_move_pct: float | None,
) -> bool:
    if limit_move_pct is None or float(limit_move_pct) <= 0:
        return False
    if float(prev_close) <= 0:
        return False
    one_sided = float(next_high) == float(next_low)
    if not one_sided:
        return False
    move = (float(next_open) - float(prev_close)) / float(prev_close)
    s = str(side).strip().lower()
    if s == "long":
        return move >= float(limit_move_pct)
    if s == "short":
        return move <= -float(limit_move_pct)
    return False


__all__ = ["is_limit_move_blocked_by_flags", "is_limit_move_blocked_by_ohlc"]
