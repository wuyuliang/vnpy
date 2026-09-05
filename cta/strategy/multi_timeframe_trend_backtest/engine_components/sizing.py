"""Strategy-aware sizing adapter for the shared replay engine."""
from __future__ import annotations

from typing import Any

from cta.config.replay_common import BaseReplayConfig
from cta.strategy.common.sizing import size_for_risk_band
from cta.strategy.multi_timeframe_trend_rules import size_for_risk


def size_replay_order(
    *,
    equity: float,
    entry: float,
    stop: float,
    direction: int,
    metadata: Any,
    stressed_round_trip_cost: float,
    config: BaseReplayConfig,
) -> Any:
    """Use a strategy's declared risk model without weakening legacy checks."""
    if hasattr(config, "min_risk_pct") and hasattr(config, "max_capital_share"):
        margin_rate = (
            metadata.daily.margin_rate_long
            if direction > 0
            else metadata.daily.margin_rate_short
        )
        return size_for_risk_band(
            equity=equity,
            entry=entry,
            stop=stop,
            multiplier=float(metadata.contract_size),
            stressed_round_trip_cost=stressed_round_trip_cost,
            min_risk_pct=float(getattr(config, "min_risk_pct")),
            max_risk_pct=float(getattr(config, "max_risk_pct")),
            max_capital_share=float(getattr(config, "max_capital_share")),
            margin_rate=float(margin_rate),
        )
    return size_for_risk(
        equity=equity,
        entry=entry,
        stop=stop,
        multiplier=float(metadata.contract_size),
        stressed_round_trip_cost=stressed_round_trip_cost,
        risk_per_trade=config.risk_per_trade,
    )


__all__ = ["size_replay_order"]
