from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from math import ceil

import numpy as np
import pandas as pd

from .config import StrategyConfig


class MarketState(StrEnum):
    """Confirmed portfolio exposure regime."""

    RISK_ON = "risk_on"
    CAUTION = "caution"
    RISK_OFF = "risk_off"


@dataclass(frozen=True)
class RiskPosition:
    """Close-of-day position snapshot used by the pure risk planner."""

    symbol: str
    quantity: int
    price: float
    stop_price: float
    state: str
    holding_rank: int
    ema5_slope: float
    industry: str

    @property
    def market_value(self) -> float:
        return self.quantity * self.price

    @property
    def stop_risk(self) -> float:
        return self.quantity * max(self.price - self.stop_price, 0.0)


@dataclass
class MarketStateTracker:
    """Promote a candidate regime only after consecutive confirmations."""

    state: MarketState
    confirmation_days: int
    candidate: MarketState | None = None
    candidate_days: int = 0

    def advance(self, candidate: MarketState) -> MarketState:
        if candidate == self.state:
            self.candidate = None
            self.candidate_days = 0
            return self.state
        if candidate != self.candidate:
            self.candidate = candidate
            self.candidate_days = 1
            return self.state
        self.candidate_days += 1
        if self.candidate_days >= self.confirmation_days:
            self.state = candidate
            self.candidate = None
            self.candidate_days = 0
        return self.state


def calculate_breadth(daily: pd.DataFrame) -> float | None:
    """Return the share of valid trading ETFs closing above EMA20."""
    if daily.empty or not {"close", "ema20"}.issubset(daily.columns):
        return None
    trading = (
        daily["is_trading"].fillna(False).astype(bool)
        if "is_trading" in daily
        else pd.Series(True, index=daily.index)
    )
    close = pd.to_numeric(daily["close"], errors="coerce")
    ema20 = pd.to_numeric(daily["ema20"], errors="coerce")
    valid = trading & np.isfinite(close) & np.isfinite(ema20)
    if not valid.any():
        return None
    return float((close.loc[valid] > ema20.loc[valid]).mean())


def classify_market_candidate(
    benchmark_row: pd.Series,
    broad_risk_on: bool,
    breadth: float | None,
    config: StrategyConfig,
) -> MarketState:
    """Classify one signal day before persistence confirmation."""
    if broad_risk_on:
        return MarketState.RISK_ON
    below_ema20 = float(benchmark_row["close"]) < float(benchmark_row["ema20"])
    breadth_is_weak = breadth is None or breadth < config.risk_off_breadth_threshold
    if below_ema20 and breadth_is_weak:
        return MarketState.RISK_OFF
    return MarketState.CAUTION


def correlation_clusters(
    symbols: list[str] | set[str],
    returns: pd.DataFrame,
    signal_date: pd.Timestamp,
    config: StrategyConfig,
) -> list[set[str]]:
    """Build deterministic point-in-time correlation connected components."""
    nodes = sorted(set(symbols))
    if not nodes:
        return []
    adjacency = {symbol: set() for symbol in nodes}
    if config.correlation_threshold is not None:
        history = returns.loc[returns.index <= signal_date].tail(
            config.correlation_lookback
        )
        available = [symbol for symbol in nodes if symbol in history.columns]
        for left_index, left in enumerate(available):
            for right in available[left_index + 1 :]:
                pair = history[[left, right]].dropna()
                if len(pair) < config.min_correlation_observations:
                    continue
                correlation = pair[left].corr(pair[right])
                if (
                    pd.notna(correlation)
                    and correlation + 1e-12 >= config.correlation_threshold
                ):
                    adjacency[left].add(right)
                    adjacency[right].add(left)

    components: list[set[str]] = []
    remaining = set(nodes)
    while remaining:
        root = min(remaining)
        component = {root}
        frontier = [root]
        while frontier:
            current = frontier.pop()
            for neighbour in sorted(adjacency[current] - component):
                component.add(neighbour)
                frontier.append(neighbour)
        remaining -= component
        components.append(component)
    return components


def plan_trim_quantities(
    positions: list[RiskPosition],
    *,
    equity: float,
    lot_size: int,
    gross_cap: float | None,
    industry_cap: float | None,
    correlation_clusters: list[set[str]],
    correlation_cap: float | None,
    portfolio_stop_risk_cap: float | None,
    cluster_stop_risk_cap: float | None,
) -> dict[str, int]:
    """Return deterministic sell quantities needed to satisfy hard capacities."""
    if equity <= 0 or lot_size <= 0:
        raise ValueError("equity and lot_size must be positive")
    by_symbol = {position.symbol: position for position in positions}
    remaining = {position.symbol: position.quantity for position in positions}
    planned: dict[str, int] = {}
    tolerance = max(equity, 1.0) * 1e-12

    while True:
        # Each violation stores its excess, affected symbols and per-share metric.
        violations: list[tuple[float, set[str], str]] = []
        active = {symbol for symbol, quantity in remaining.items() if quantity > 0}
        if gross_cap is not None:
            gross = sum(
                remaining[symbol] * by_symbol[symbol].price for symbol in active
            )
            excess = gross - equity * gross_cap
            if excess > tolerance:
                violations.append((excess, active, "market_value"))

        if industry_cap is not None:
            industries = {
                position.industry
                for position in positions
                if position.industry != "broad_or_other"
            }
            for industry in sorted(industries):
                members = {
                    symbol
                    for symbol in active
                    if by_symbol[symbol].industry == industry
                }
                market_value = sum(
                    remaining[symbol] * by_symbol[symbol].price for symbol in members
                )
                excess = market_value - equity * industry_cap
                if excess > tolerance:
                    violations.append((excess, members, "market_value"))

        if correlation_cap is not None:
            for cluster in correlation_clusters:
                members = active & cluster
                market_value = sum(
                    remaining[symbol] * by_symbol[symbol].price for symbol in members
                )
                excess = market_value - equity * correlation_cap
                if excess > tolerance:
                    violations.append((excess, members, "market_value"))

        if portfolio_stop_risk_cap is not None:
            stop_risk = sum(
                remaining[symbol]
                * max(
                    by_symbol[symbol].price - by_symbol[symbol].stop_price,
                    0.0,
                )
                for symbol in active
            )
            excess = stop_risk - equity * portfolio_stop_risk_cap
            if excess > tolerance:
                violations.append((excess, active, "stop_risk"))

        if cluster_stop_risk_cap is not None:
            for cluster in correlation_clusters:
                members = active & cluster
                stop_risk = sum(
                    remaining[symbol]
                    * max(
                        by_symbol[symbol].price - by_symbol[symbol].stop_price,
                        0.0,
                    )
                    for symbol in members
                )
                excess = stop_risk - equity * cluster_stop_risk_cap
                if excess > tolerance:
                    violations.append((excess, members, "stop_risk"))

        if not violations:
            break
        eligible = {
            symbol
            for _excess, members, _metric in violations
            for symbol in members
            if remaining.get(symbol, 0) >= lot_size
        }
        if not eligible:
            break

        def trim_priority(symbol: str) -> tuple[bool, int, float, str]:
            position = by_symbol[symbol]
            rank = position.holding_rank
            slope = position.ema5_slope
            return (
                position.state != "trial",
                -rank,
                slope if np.isfinite(slope) else -np.inf,
                symbol,
            )

        symbol = min(eligible, key=trim_priority)
        position = by_symbol[symbol]
        resolving_quantities: list[int] = []
        for excess, members, metric in violations:
            if symbol not in members:
                continue
            impact = (
                position.price
                if metric == "market_value"
                else max(position.price - position.stop_price, 0.0)
            )
            if impact <= 0:
                continue
            lots = max(ceil((excess - tolerance) / impact / lot_size), 1)
            resolving_quantities.append(lots * lot_size)
        if not resolving_quantities:
            break
        max_quantity = remaining[symbol] // lot_size * lot_size
        trim_quantity = min(min(resolving_quantities), max_quantity)
        remaining[symbol] -= trim_quantity
        planned[symbol] = planned.get(symbol, 0) + trim_quantity

    return planned
