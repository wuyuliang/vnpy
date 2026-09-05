"""Portfolio equity, margin, and position-capacity calculations."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math
from typing import Any

from cta.config.replay_common import BaseReplayConfig

from .models import _PendingOrder, _Position


@dataclass
class _IncrementalPortfolioEquity:
    """Cache per-position marked PnL components between portfolio events."""

    _components: dict[str, tuple[float, float]] = field(default_factory=dict)
    _dirty: bool = True
    _cached_cash: float = math.nan
    _cached_roots: tuple[str, ...] = ()
    _cached_equity: float = math.nan

    def update_position(
        self,
        root_symbol: str,
        position: _Position,
        mark: float,
    ) -> None:
        direction = int(position.pending.candidate["direction"])
        multiplier = float(position.pending.metadata.contract_size)
        fee_per_lot = float(
            position.pending.metadata.stressed_round_trip_fee_cash
        )
        components = (
            direction
            * (float(mark) - position.entry_price)
            * position.quantity
            * multiplier,
            fee_per_lot * position.quantity,
        )
        if self._components.get(root_symbol) != components:
            self._components[root_symbol] = components
            self._dirty = True

    def remove_position(self, root_symbol: str) -> None:
        if self._components.pop(root_symbol, None) is not None:
            self._dirty = True

    def marked_equity(
        self,
        cash: float,
        positions: dict[str, _Position],
    ) -> float:
        roots = tuple(positions)
        if (
            not self._dirty
            and cash == self._cached_cash
            and roots == self._cached_roots
        ):
            return self._cached_equity
        # Preserve the full calculation's position order and floating-point order.
        equity = cash
        for root_symbol in roots:
            marked_pnl, fees = self._components[root_symbol]
            equity += marked_pnl
            equity -= fees
        self._dirty = False
        self._cached_cash = cash
        self._cached_roots = roots
        self._cached_equity = equity
        return equity

    def reconcile(
        self,
        cash: float,
        positions: dict[str, _Position],
        marks: dict[str, float],
        *,
        trade_date: date,
    ) -> None:
        self._dirty = True
        incremental = self.marked_equity(cash, positions)
        expected = _portfolio_marked_equity(cash, positions, marks)
        tolerance = max(1e-9, abs(expected) * 1e-12)
        if abs(incremental - expected) > tolerance:
            raise RuntimeError(
                "incremental portfolio equity drift "
                f"on {trade_date}: incremental={incremental:.17g};"
                f"expected={expected:.17g};tolerance={tolerance:.17g}"
            )


def _portfolio_entry_quantity(
    *,
    pending: _PendingOrder,
    requested_quantity: int,
    fill_price: float,
    equity: float,
    positions: dict[str, _Position],
    marks: dict[str, float],
    config: BaseReplayConfig,
    portfolio_margin_utilization: float,
) -> tuple[int, str]:
    if len(positions) >= config.max_concurrent_positions:
        return 0, "MAX_CONCURRENT_POSITIONS"
    direction = int(pending.candidate["direction"])
    margin_rate = (
        float(pending.metadata.daily.margin_rate_long)
        if direction > 0
        else float(pending.metadata.daily.margin_rate_short)
    )
    margin_per_lot = (
        fill_price * float(pending.metadata.contract_size) * margin_rate
    )
    if not math.isfinite(margin_per_lot) or margin_per_lot <= 0 or equity <= 0:
        return 0, "PORTFOLIO_MARGIN_LIMIT"
    symbol_lots = math.floor(
        equity * config.max_symbol_margin_utilization / margin_per_lot
    )
    if symbol_lots < 1:
        return 0, "SYMBOL_MARGIN_LIMIT"
    used_margin = _portfolio_margin_used(positions, marks)
    portfolio_lots = math.floor(
        max(0.0, equity * portfolio_margin_utilization - used_margin)
        / margin_per_lot
    )
    if portfolio_lots < 1:
        return 0, "PORTFOLIO_MARGIN_LIMIT"
    return min(requested_quantity, symbol_lots, portfolio_lots), ""


def _portfolio_marked_equity(
    cash: float,
    positions: dict[str, _Position],
    marks: dict[str, float],
) -> float:
    equity = cash
    for root_symbol, position in positions.items():
        mark = marks.get(root_symbol, position.entry_price)
        direction = int(position.pending.candidate["direction"])
        multiplier = float(position.pending.metadata.contract_size)
        equity += (
            direction
            * (mark - position.entry_price)
            * position.quantity
            * multiplier
        )
        equity -= (
            float(position.pending.metadata.stressed_round_trip_fee_cash)
            * position.quantity
        )
    return equity


def _portfolio_margin_used(
    positions: dict[str, _Position],
    marks: dict[str, float],
) -> float:
    return sum(
        _position_margin_per_lot(root_symbol, position, marks)
        * position.quantity
        for root_symbol, position in positions.items()
    )


def _position_margin_per_lot(
    root_symbol: str,
    position: _Position,
    marks: dict[str, float],
) -> float:
    direction = int(position.pending.candidate["direction"])
    margin_rate = (
        float(position.current_metadata.daily.margin_rate_long)
        if direction > 0
        else float(position.current_metadata.daily.margin_rate_short)
    )
    return (
        marks.get(root_symbol, position.entry_price)
        * float(position.pending.metadata.contract_size)
        * margin_rate
    )


def _portfolio_equity_row(
    *,
    date_value: date,
    cash: float,
    positions: dict[str, _Position],
    marks: dict[str, float],
    marked_equity: float | None = None,
    overnight: bool = False,
) -> dict[str, Any]:
    equity = (
        _portfolio_marked_equity(cash, positions, marks)
        if marked_equity is None
        else marked_equity
    )
    margin = _portfolio_margin_used(positions, marks)
    unrealized = equity - cash
    open_risk = sum(
        position.initial_risk_cash
        * position.quantity
        / position.initial_quantity
        for position in positions.values()
    )
    utilization = margin / equity if equity > 0 else math.nan
    symbol_utilizations = []
    if equity > 0:
        for root_symbol, position in positions.items():
            symbol_utilizations.append(
                _portfolio_margin_used(
                    {root_symbol: position},
                    marks,
                )
                / equity
            )
    return {
        "date": date_value,
        "equity": equity,
        "cash": cash,
        "unrealized_pnl": unrealized,
        "margin_used": margin,
        "margin_utilization": utilization,
        "open_risk": open_risk,
        "peak_margin_utilization": utilization,
        "peak_overnight_margin_utilization": utilization if overnight else 0.0,
        "peak_symbol_margin_utilization": max(symbol_utilizations, default=0.0),
        "max_concurrent_positions": len(positions),
    }


def _merge_portfolio_daily_row(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    if previous is None:
        return current
    merged = dict(current)
    for column in (
        "peak_margin_utilization",
        "peak_overnight_margin_utilization",
        "peak_symbol_margin_utilization",
        "max_concurrent_positions",
    ):
        merged[column] = max(float(previous[column]), float(current[column]))
    return merged
