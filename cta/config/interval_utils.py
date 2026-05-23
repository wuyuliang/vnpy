"""Shared interval normalization for config-layer modules.

This module intentionally avoids importing ``cta.portfolio_logic`` to keep
config dataclasses free of package import cycles.
"""
from __future__ import annotations


_ALIASES: dict[str, str] = {
    "daily": "day",
    "d": "day",
    "1d": "day",
    "minute60": "60min",
    "60m": "60min",
    "1h": "60min",
    "hour": "60min",
    "minute30": "30min",
    "30m": "30min",
    "minute15": "15min",
    "15m": "15min",
    "minute5": "5min",
    "5m": "5min",
    "minute": "min",
    "1m": "min",
    "1min": "min",
}


def normalize_portfolio_interval(interval: object) -> str:
    """Normalize to one of ``day/60min/30min/15min/5min/min`` style labels."""
    if interval is None:
        return ""
    raw = str(interval).strip().lower()
    return _ALIASES.get(raw, raw)


__all__ = ["normalize_portfolio_interval"]

