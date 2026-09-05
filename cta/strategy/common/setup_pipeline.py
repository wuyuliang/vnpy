"""Strategy-neutral plumbing from a matched setup to an engine candidate."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class PatternMatch:
    """A matched setup, addressed by its first and last bar in the frame.

    ``extras`` carries whatever the specific pattern needs downstream — a
    pullback's rally high, a leg's drop — without every strategy having to
    invent its own match type.
    """

    pattern_type: str
    first_index: int
    last_index: int
    extras: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class SetupInstrument:
    """Execution inputs known for an actual dated futures contract."""

    symbol: str
    exchange: str
    contract: str
    tick_size: float
    multiplier: float
    stressed_round_trip_cost: float
    metadata_asof: object
    margin_rate: float | None = None

    def __post_init__(self) -> None:
        if not all(
            str(value).strip() for value in (self.symbol, self.exchange, self.contract)
        ):
            raise ValueError("symbol, exchange, and contract must not be empty")
        contract = str(self.contract).strip().upper()
        if contract == str(self.symbol).strip().upper() or not any(
            character.isdigit() for character in contract
        ):
            raise ValueError("contract must identify an actual dated contract")
        if not all(
            math.isfinite(float(value)) and float(value) > 0
            for value in (self.tick_size, self.multiplier)
        ):
            raise ValueError("tick_size and multiplier must be finite and positive")
        if (
            not math.isfinite(float(self.stressed_round_trip_cost))
            or float(self.stressed_round_trip_cost) < 0
        ):
            raise ValueError("stressed_round_trip_cost must be finite and nonnegative")
        metadata_asof = pd.Timestamp(self.metadata_asof)
        if metadata_asof is pd.NaT or metadata_asof.tzinfo is None:
            raise ValueError("metadata_asof must be a timezone-aware timestamp")
        if self.margin_rate is not None and (
            not math.isfinite(float(self.margin_rate))
            or float(self.margin_rate) <= 0
        ):
            raise ValueError("margin_rate must be finite and positive when provided")
        object.__setattr__(self, "metadata_asof", metadata_asof)


def execution_timeline(
    execution_bars: pd.DataFrame | None,
    signal_frame: pd.DataFrame,
) -> pd.DatetimeIndex:
    """Sorted, de-duplicated bar-end index the orders live on."""
    source = signal_frame if execution_bars is None else execution_bars
    index = pd.DatetimeIndex(
        pd.to_datetime(source["bar_end"], errors="raise")
    ).sort_values()
    return index.drop_duplicates() if index.has_duplicates else index


def order_window(
    signal_time: pd.Timestamp,
    *,
    execution_index: pd.DatetimeIndex,
    ttl_bars: int,
    signal_timeframe_minutes: int = 1,
) -> tuple[pd.Timestamp | None, pd.Timestamp | None]:
    """Activation and expiry on the execution timeline.

    The order goes live on the first execution bar strictly after the signal
    bar closes, and lives ``ttl_bars`` *signal* bars, converted to execution
    bars so a five-minute setup keeps a five-minute-shaped window.
    """
    position = int(execution_index.searchsorted(signal_time, side="right"))
    if position >= len(execution_index):
        return None, None
    span = max(1, int(ttl_bars) * max(1, int(signal_timeframe_minutes)))
    expires = min(position + span - 1, len(execution_index) - 1)
    return pd.Timestamp(execution_index[position]), pd.Timestamp(
        execution_index[expires]
    )


def safe_ratio(numerator: float, denominator: float) -> float:
    """A finite audit ratio, or NaN when the denominator is unusable."""
    numerator = float(numerator)
    denominator = float(denominator)
    if not math.isfinite(denominator) or denominator == 0:
        return math.nan
    value = numerator / denominator
    return value if math.isfinite(value) else math.nan
