"""§04 range_strategies implementations."""
from __future__ import annotations

from .false_breakout_reversal import FBRTrade, build_fbr_trade, manage_fbr_exit
from .mean_reversion import MRSignal, compute_zscore, mr_decision
from .noise_filtering import FilterResult, combine_filters, make_time_window_filter, make_volume_filter
from .range_boundary_reversal import BoundaryReversalSetup, detect_boundary_reversal, place_boundary_order

__all__ = [
    "BoundaryReversalSetup",
    "FBRTrade",
    "FilterResult",
    "MRSignal",
    "build_fbr_trade",
    "combine_filters",
    "compute_zscore",
    "detect_boundary_reversal",
    "make_time_window_filter",
    "make_volume_filter",
    "manage_fbr_exit",
    "mr_decision",
    "place_boundary_order",
]

