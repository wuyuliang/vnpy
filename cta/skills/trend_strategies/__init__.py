"""§03 trend_strategies implementations."""
from __future__ import annotations

from .atr_breakout import ATRChannelSignal, compute_atr_channel, decide_atr_channel_trade
from .cross_sectional_momentum import XSPortfolioTarget, build_xs_portfolio, compute_xs_momentum
from .donchian_breakout import DonchianSignal, compute_donchian, decide_donchian_trade
from .ma_trend_following import MASignal, compute_ma_features, ma_decision
from .trend_hold_trailing import TrailingState, decide_add_on, fast_exit_if_stalled, update_trailing

__all__ = [
    "ATRChannelSignal",
    "DonchianSignal",
    "MASignal",
    "TrailingState",
    "XSPortfolioTarget",
    "build_xs_portfolio",
    "compute_atr_channel",
    "compute_donchian",
    "compute_ma_features",
    "compute_xs_momentum",
    "decide_add_on",
    "decide_atr_channel_trade",
    "decide_donchian_trade",
    "fast_exit_if_stalled",
    "ma_decision",
    "update_trailing",
]

