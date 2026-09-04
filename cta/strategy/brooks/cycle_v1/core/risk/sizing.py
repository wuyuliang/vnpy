"""Position sizing from executable structural-stop loss."""
from __future__ import annotations

from dataclasses import dataclass
import math

from ...config import BrooksCycleConfig
from ..types import MarketCycle


CYCLE_MULTIPLIER = {
    MarketCycle.STRONG_BULL_BREAKOUT: 1.0,
    MarketCycle.STRONG_BEAR_BREAKOUT: 1.0,
    MarketCycle.BULL_TIGHT_CHANNEL: 0.8,
    MarketCycle.BEAR_TIGHT_CHANNEL: 0.8,
    MarketCycle.BULL_BROAD_CHANNEL: 0.6,
    MarketCycle.BEAR_BROAD_CHANNEL: 0.6,
    MarketCycle.TRADING_RANGE: 0.4,
    MarketCycle.TRANSITION: 0.0,
    MarketCycle.UNAVAILABLE: 0.0,
}


@dataclass(frozen=True)
class SizingDecision:
    quantity: int
    risk_budget: float
    loss_per_lot: float
    estimated_open_risk: float
    reason: str = ""


def size_position(
    *,
    equity: float,
    entry: float,
    stop: float,
    contract_multiplier: float,
    estimated_entry_cost: float,
    stressed_exit_cost: float,
    lot_step: int,
    cycle: MarketCycle,
    large_confidence: float,
    medium_confidence: float,
    drawdown_multiplier: float,
    new_order_risk_multiplier: float,
    config: BrooksCycleConfig,
) -> SizingDecision:
    positive_values = (equity, entry, stop, contract_multiplier)
    costs = (estimated_entry_cost, stressed_exit_cost)
    controls = (
        large_confidence,
        medium_confidence,
        drawdown_multiplier,
        new_order_risk_multiplier,
    )
    if not all(math.isfinite(value) and value > 0 for value in positive_values):
        raise ValueError("equity, prices, and multiplier must be finite and positive")
    if lot_step <= 0:
        raise ValueError("lot step must be positive")
    if not all(math.isfinite(value) and value >= 0 for value in costs):
        raise ValueError("costs must be finite and nonnegative")
    if not all(math.isfinite(value) for value in controls):
        raise ValueError("confidence and risk multipliers must be finite")
    confidence_gate = float(
        large_confidence >= config.risk.min_cycle_confidence
        and medium_confidence >= config.risk.min_cycle_confidence
    )
    budget = min(
        equity * config.risk.max_trade_risk,
        equity
        * config.risk.risk_per_trade
        * CYCLE_MULTIPLIER[cycle]
        * max(0.0, min(1.0, drawdown_multiplier))
        * max(0.0, min(1.0, new_order_risk_multiplier))
        * confidence_gate,
    )
    loss_per_lot = (
        abs(entry - stop) * contract_multiplier
        + estimated_entry_cost
        + stressed_exit_cost
    )
    if loss_per_lot <= 0:
        raise ValueError("loss per lot must be positive")
    raw_quantity = math.floor(budget / loss_per_lot)
    quantity = (raw_quantity // lot_step) * lot_step
    reason = ""
    if budget <= 0:
        reason = "RISK_GATE_CLOSED"
    elif quantity == 0:
        reason = "ONE_LOT_EXCEEDS_RISK_BUDGET"
    return SizingDecision(
        quantity=quantity,
        risk_budget=budget,
        loss_per_lot=loss_per_lot,
        estimated_open_risk=quantity * loss_per_lot,
        reason=reason,
    )


__all__ = ["CYCLE_MULTIPLIER", "SizingDecision", "size_position"]
