"""Replay state and result models."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

import pandas as pd


@dataclass(frozen=True)
class ReplayArtifacts:
    candidates: pd.DataFrame
    plans: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    exit_legs: pd.DataFrame
    trades: pd.DataFrame
    daily_equity: pd.DataFrame
    rejections: pd.DataFrame
    position_scaling_events: pd.DataFrame


@dataclass(frozen=True)
class PortfolioReplayInput:
    root_symbol: str
    exchange: str
    minute_bars: pd.DataFrame
    five_minute_context: pd.DataFrame
    candidates: pd.DataFrame
    sessions: tuple[Any, ...]
    roll_execution_bars: pd.DataFrame | None = None
    daily_context: pd.DataFrame | None = None


class _PlanRejected(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass
class _PendingOrder:
    candidate: dict[str, Any]
    metadata: Any
    order_id: str
    quantity: int
    risk_budget: float
    loss_per_lot: float


@dataclass
class _Position:
    pending: _PendingOrder
    quantity: int
    initial_quantity: int
    entry_time: pd.Timestamp
    entry_price: float
    entry_reference: float
    stop: float
    target: float
    initial_risk_cash: float
    entry_bar_index: int
    maximum_favorable_price: float
    maximum_adverse_price: float
    current_metadata: Any
    base_quantity: int
    symbol_quantity_scale: float
    portfolio_quantity_scale: float
    quantity_scale: float
    position_scaling_reason: str
    symbol_recovery_deficit_at_entry: float
    portfolio_recovery_deficit_at_entry: float
    is_first_trade_in_trend_segment: int
    trigger_to_prior_5d_high_ratio: float
    opened_via_chase_gate: bool = False
    profit_floor_price: float = math.nan
    follow_through_seen: bool = False
    no_follow_through_target_active: bool = False
    exit_legs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _PendingOrderDecision:
    status: str
    reason: str = ""
    match: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class _CandidateFilterDecision:
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True)
class _PositionExitDecision:
    reason: str = ""
    price: float = math.nan
    reference: float = math.nan
    metadata: Any = None
    pending_reason: str = ""
    limit_locked: bool = False


@dataclass
class _SymbolScalingState:
    active: bool = False
    consecutive_losses: int = 0
    consecutive_loss_cash: float = 0.0
    recovery_deficit: float = 0.0


@dataclass
class _PortfolioScalingState:
    high_water: float
    active: bool = False
    recovery_deficit: float = 0.0
