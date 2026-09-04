"""Symbol, sector, portfolio, daily-loss, drawdown, and margin gates."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from datetime import date
import math
from types import MappingProxyType

import numpy as np
import pandas as pd

from ...config import BrooksCycleConfig


@dataclass(frozen=True)
class PortfolioRiskSnapshot:
    equity: float
    symbol_open_risk: Mapping[str, float] = field(default_factory=dict)
    sector_open_risk: Mapping[str, float] = field(default_factory=dict)
    correlation_cluster_open_risk: Mapping[str, float] = field(default_factory=dict)
    symbol_to_correlation_cluster: Mapping[str, str] = field(default_factory=dict)
    total_open_risk: float = 0.0
    margin_used_and_reserved: float = 0.0
    day_start_adjusted_equity: float = 0.0
    adjusted_equity: float = 0.0
    high_watermark_equity: float = 0.0
    risk_available: bool = True
    risk_block_reason: str = ""
    daily_loss_latched: bool = False
    daily_hard_latched: bool = False

    def __post_init__(self) -> None:
        scalar_values = (
            self.total_open_risk,
            self.margin_used_and_reserved,
            self.day_start_adjusted_equity,
            self.adjusted_equity,
            self.high_watermark_equity,
        )
        if not math.isfinite(self.equity) or self.equity <= 0:
            raise ValueError("equity must be finite and positive")
        if not all(math.isfinite(value) and value >= 0 for value in scalar_values):
            raise ValueError("portfolio risk values must be finite and nonnegative")
        risk_maps = (
            self.symbol_open_risk,
            self.sector_open_risk,
            self.correlation_cluster_open_risk,
        )
        if any(
            not isinstance(key, str)
            or not key
            or not math.isfinite(value)
            or value < 0
            for risk_map in risk_maps
            for key, value in risk_map.items()
        ):
            raise ValueError("portfolio risk maps require nonempty keys and finite nonnegative values")
        object.__setattr__(self, "symbol_open_risk", MappingProxyType(dict(self.symbol_open_risk)))
        object.__setattr__(self, "sector_open_risk", MappingProxyType(dict(self.sector_open_risk)))
        object.__setattr__(
            self,
            "correlation_cluster_open_risk",
            MappingProxyType(dict(self.correlation_cluster_open_risk)),
        )
        object.__setattr__(
            self,
            "symbol_to_correlation_cluster",
            MappingProxyType(dict(self.symbol_to_correlation_cluster)),
        )
        if not self.risk_available and not self.risk_block_reason:
            raise ValueError("unavailable risk snapshots require a block reason")


@dataclass(frozen=True)
class PortfolioRiskDecision:
    allowed: bool
    reason_codes: tuple[str, ...]
    daily_loss_ratio: float
    drawdown_ratio: float
    drawdown_multiplier: float


class PortfolioRiskGuard:
    """Latch daily loss breaches until the next exchange trade date."""

    def __init__(self, config: BrooksCycleConfig) -> None:
        self.config = config
        self._trade_date: date | None = None
        self._soft_latched = False
        self._hard_latched = False

    def apply(
        self,
        exchange_trade_date: date,
        snapshot: PortfolioRiskSnapshot,
    ) -> PortfolioRiskSnapshot:
        if self._trade_date is not None and exchange_trade_date < self._trade_date:
            raise ValueError("risk guard trade dates must be nondecreasing")
        if exchange_trade_date != self._trade_date:
            self._trade_date = exchange_trade_date
            self._soft_latched = False
            self._hard_latched = False
        daily_loss = _loss_ratio(
            snapshot.day_start_adjusted_equity,
            snapshot.adjusted_equity,
        )
        self._hard_latched = self._hard_latched or (
            daily_loss >= self.config.risk.max_daily_loss_hard
        )
        self._soft_latched = self._soft_latched or self._hard_latched or (
            daily_loss >= self.config.risk.max_daily_loss_soft
        )
        return replace(
            snapshot,
            daily_loss_latched=self._soft_latched,
            daily_hard_latched=self._hard_latched,
        )


def portfolio_allows(
    snapshot: PortfolioRiskSnapshot,
    *,
    symbol: str,
    sector: str,
    incremental_open_risk: float,
    incremental_margin: float,
    config: BrooksCycleConfig,
) -> PortfolioRiskDecision:
    increments = (incremental_open_risk, incremental_margin)
    if not all(math.isfinite(value) and value >= 0 for value in increments):
        raise ValueError("incremental risk and margin must be finite and nonnegative")
    if not snapshot.risk_available:
        return PortfolioRiskDecision(
            allowed=False,
            reason_codes=(snapshot.risk_block_reason,),
            daily_loss_ratio=0.0,
            drawdown_ratio=0.0,
            drawdown_multiplier=0.0,
        )
    reasons: list[str] = []
    equity = snapshot.equity
    if snapshot.symbol_open_risk.get(symbol, 0.0) + incremental_open_risk > (
        equity * config.risk.max_symbol_open_risk
    ):
        reasons.append("MAX_SYMBOL_OPEN_RISK")
    if snapshot.sector_open_risk.get(sector, 0.0) + incremental_open_risk > (
        equity * config.risk.max_sector_open_risk
    ):
        reasons.append("MAX_SECTOR_OPEN_RISK")
    cluster = snapshot.symbol_to_correlation_cluster.get(symbol)
    if cluster is not None and (
        snapshot.correlation_cluster_open_risk.get(cluster, 0.0) + incremental_open_risk
        > equity * config.risk.max_correlation_cluster_open_risk
    ):
        reasons.append("MAX_CORRELATION_CLUSTER_OPEN_RISK")
    if snapshot.total_open_risk + incremental_open_risk > equity * config.risk.max_total_open_risk:
        reasons.append("MAX_TOTAL_OPEN_RISK")
    if snapshot.margin_used_and_reserved + incremental_margin > (
        equity * config.risk.max_margin_utilization
    ):
        reasons.append("MAX_MARGIN_UTILIZATION")
    daily_loss = _loss_ratio(
        snapshot.day_start_adjusted_equity,
        snapshot.adjusted_equity,
    )
    drawdown = _loss_ratio(snapshot.high_watermark_equity, snapshot.adjusted_equity)
    if snapshot.daily_loss_latched or daily_loss >= config.risk.max_daily_loss_soft:
        reasons.append("DAILY_LOSS_SOFT")
    if snapshot.daily_hard_latched or daily_loss >= config.risk.max_daily_loss_hard:
        reasons.append("DAILY_LOSS_HARD_ALERT")
    if drawdown >= config.risk.max_drawdown_hard:
        reasons.append("DRAWDOWN_HARD")
    return PortfolioRiskDecision(
        allowed=not reasons,
        reason_codes=tuple(reasons),
        daily_loss_ratio=daily_loss,
        drawdown_ratio=drawdown,
        drawdown_multiplier=drawdown_risk_multiplier(drawdown, config),
    )


def drawdown_risk_multiplier(drawdown_ratio: float, config: BrooksCycleConfig) -> float:
    if drawdown_ratio >= config.risk.max_drawdown_hard:
        return 0.0
    if drawdown_ratio <= config.risk.max_drawdown_soft:
        return 1.0
    span = config.risk.max_drawdown_hard - config.risk.max_drawdown_soft
    progress = (drawdown_ratio - config.risk.max_drawdown_soft) / span
    return 1.0 - progress * (1.0 - config.risk.drawdown_floor_multiplier)


def calculate_open_risk(
    *,
    direction: int,
    quantity: int,
    mark_price: float,
    executable_stop_price: float,
    contract_multiplier: float,
    stressed_exit_cost: float,
    stop_executable: bool,
    locked_limit_stress_price: float,
) -> float:
    if direction not in (-1, 1) or quantity < 0:
        raise ValueError("invalid open-risk position")
    if contract_multiplier <= 0 or stressed_exit_cost < 0:
        raise ValueError("invalid open-risk mechanics")
    risk_price = executable_stop_price if stop_executable else locked_limit_stress_price
    return quantity * (
        abs(mark_price - risk_price) * contract_multiplier + stressed_exit_cost
    )


def estimate_correlation_clusters(
    returns: pd.DataFrame,
    config: BrooksCycleConfig,
) -> dict[str, str]:
    """Build deterministic absolute-correlation components from a trailing prefix."""
    if returns.columns.duplicated().any():
        raise ValueError("return symbols must be unique")
    if any(not isinstance(column, str) or not column for column in returns.columns):
        raise ValueError("return symbols must be nonempty strings")
    symbols = sorted(returns.columns)
    if not symbols:
        return {}
    numeric = returns.loc[:, symbols].apply(pd.to_numeric, errors="coerce").tail(
        config.risk.correlation_lookback_sessions
    )
    correlation = numeric.corr(min_periods=config.risk.correlation_min_observations)
    parent = {symbol: symbol for symbol in symbols}

    def find(symbol: str) -> str:
        while parent[symbol] != symbol:
            parent[symbol] = parent[parent[symbol]]
            symbol = parent[symbol]
        return symbol

    def union(left: str, right: str) -> None:
        left_root = find(left)
        right_root = find(right)
        if left_root == right_root:
            return
        first, second = sorted((left_root, right_root))
        parent[second] = first

    for left_index, left in enumerate(symbols):
        for right in symbols[left_index + 1 :]:
            pairwise_observations = numeric[[left, right]].dropna().shape[0]
            if pairwise_observations < config.risk.correlation_min_observations:
                raise ValueError(
                    "BLOCKED_CORRELATION_DATA: insufficient pairwise return history"
                )
            value = float(correlation.loc[left, right])
            if not np.isfinite(value):
                raise ValueError("BLOCKED_CORRELATION_DATA: pairwise correlation unavailable")
            if abs(value) >= config.risk.correlation_threshold:
                union(left, right)
    components: dict[str, list[str]] = {}
    for symbol in symbols:
        components.setdefault(find(symbol), []).append(symbol)
    result: dict[str, str] = {}
    for members in components.values():
        cluster_id = f"CORR:{'|'.join(sorted(members))}"
        result.update({symbol: cluster_id for symbol in members})
    return result


def _loss_ratio(reference: float, current: float) -> float:
    if reference <= 0:
        return 0.0
    return max(0.0, reference - current) / reference


__all__ = [
    "PortfolioRiskDecision", "PortfolioRiskGuard", "PortfolioRiskSnapshot",
    "calculate_open_risk",
    "drawdown_risk_multiplier", "estimate_correlation_clusters", "portfolio_allows",
]
