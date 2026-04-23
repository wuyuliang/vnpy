"""§02 price_action implementations."""
from __future__ import annotations

from .breakout_pullback import PullbackSetup, detect_breakout_pullback, pullback_entry_trigger
from .bull_bear_flag import FlagSetup, detect_flag, flag_breakout_trigger
from .channel_state import ChannelState, channel_based_trailing, compute_channel_state
from .failed_breakout import FailedBreakoutSetup, detect_failed_breakout, failed_breakout_entry
from .hl_structure import HLSignal, detect_hl_signals, resolve_hl_entry
from .tight_range_breakout import TightRangeSetup, detect_tight_range, resolve_breakout_trigger

__all__ = [
    "ChannelState",
    "FailedBreakoutSetup",
    "FlagSetup",
    "HLSignal",
    "PullbackSetup",
    "TightRangeSetup",
    "channel_based_trailing",
    "compute_channel_state",
    "detect_breakout_pullback",
    "detect_failed_breakout",
    "detect_flag",
    "detect_hl_signals",
    "detect_tight_range",
    "failed_breakout_entry",
    "flag_breakout_trigger",
    "pullback_entry_trigger",
    "resolve_breakout_trigger",
    "resolve_hl_entry",
]

