from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from enum import StrEnum
from math import floor

import pandas as pd

from .config import StrategyConfig


class PositionState(StrEnum):
    """Persistent lifecycle state for one ETF holding."""

    TRIAL = "trial"
    WINNER = "winner"


@dataclass
class Position:
    """Open ETF position and its execution-accounting state."""

    symbol: str
    quantity: int
    average_price: float
    stop_price: float
    entry_commission: float
    entry_price: float | None = None
    entry_atr5: float = 0.0
    state: PositionState = PositionState.TRIAL
    highest_close: float = 0.0
    weak_rank_days: int = 0
    restore_eligible: bool = False

    def __post_init__(self) -> None:
        if self.entry_price is None:
            self.entry_price = self.average_price
        if self.highest_close <= 0:
            self.highest_close = self.average_price


@dataclass
class Trade:
    """One filled ETF order."""

    datetime: pd.Timestamp
    symbol: str
    side: str
    quantity: int
    raw_price: float
    fill_price: float
    commission: float
    slippage_cost: float
    primary_reason: str
    all_reasons: str
    realized_pnl: float = 0.0

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


def _commission(notional: float, config: StrategyConfig) -> float:
    if notional <= 0:
        return 0.0
    return max(notional * config.commission_rate, config.min_commission)


def calculate_order_quantity(
    equity: float,
    available_cash: float,
    estimated_fill_price: float,
    risk_atr: float,
    config: StrategyConfig,
    *,
    max_additional_notional: float | None = None,
) -> int:
    """Calculate a long order under risk, weight, cash and lot constraints."""
    if equity <= 0 or available_cash <= 0 or estimated_fill_price <= 0 or risk_atr <= 0:
        return 0
    risk_units = floor(
        equity * config.risk_per_trade / (config.atr_stop_multiple * risk_atr)
    )
    weight_units = floor(equity * config.max_position_weight / estimated_fill_price)
    cash_units = floor(available_cash / estimated_fill_price)
    raw_units = min(risk_units, weight_units, cash_units)
    if max_additional_notional is not None:
        notional_units = floor(max(max_additional_notional, 0.0) / estimated_fill_price)
        raw_units = min(raw_units, notional_units)
    quantity = floor(raw_units / config.lot_size) * config.lot_size
    while quantity > 0:
        notional = quantity * estimated_fill_price
        if notional + _commission(notional, config) <= available_cash:
            break
        quantity -= config.lot_size
    return max(quantity, 0)


class Portfolio:
    """Cash portfolio that executes whole-position ETF buys, sells and stops."""

    def __init__(self, initial_cash: float, config: StrategyConfig) -> None:
        if initial_cash <= 0:
            raise ValueError("initial_cash must be positive")
        self.cash = float(initial_cash)
        self.config = config
        self.positions: dict[str, Position] = {}
        self.trades: list[Trade] = []

    def buy(
        self,
        symbol: str,
        datetime: object,
        raw_price: float,
        quantity: int,
        risk_atr: float,
        reason: str,
    ) -> Trade:
        """Buy a new position and set its fixed ATR stop."""
        if symbol in self.positions:
            raise ValueError(f"position already exists: {symbol}")
        if quantity <= 0 or quantity % self.config.lot_size:
            raise ValueError("quantity must be a positive whole lot")
        fill_price = raw_price * (1 + self.config.slippage_rate)
        notional = fill_price * quantity
        commission = _commission(notional, self.config)
        if notional + commission > self.cash + 1e-9:
            raise ValueError("insufficient cash")
        self.cash -= notional + commission
        self.positions[symbol] = Position(
            symbol=symbol,
            quantity=quantity,
            average_price=fill_price,
            stop_price=fill_price - self.config.atr_stop_multiple * risk_atr,
            entry_commission=commission,
            entry_price=fill_price,
            entry_atr5=risk_atr,
            highest_close=fill_price,
        )
        trade = Trade(
            datetime=pd.Timestamp(datetime),
            symbol=symbol,
            side="buy",
            quantity=quantity,
            raw_price=raw_price,
            fill_price=fill_price,
            commission=commission,
            slippage_cost=(fill_price - raw_price) * quantity,
            primary_reason=reason,
            all_reasons=reason,
        )
        self.trades.append(trade)
        return trade

    def increase(
        self,
        symbol: str,
        datetime: object,
        raw_price: float,
        quantity: int,
        reason: str,
    ) -> Trade:
        """Increase an existing holding without resetting its strategy state."""
        if symbol not in self.positions:
            raise ValueError(f"position does not exist: {symbol}")
        if quantity <= 0 or quantity % self.config.lot_size:
            raise ValueError("quantity must be a positive whole lot")
        position = self.positions[symbol]
        fill_price = raw_price * (1 + self.config.slippage_rate)
        notional = fill_price * quantity
        commission = _commission(notional, self.config)
        if notional + commission > self.cash + 1e-9:
            raise ValueError("insufficient cash")
        old_notional = position.average_price * position.quantity
        self.cash -= notional + commission
        position.quantity += quantity
        position.average_price = (old_notional + notional) / position.quantity
        position.entry_commission += commission
        trade = Trade(
            datetime=pd.Timestamp(datetime),
            symbol=symbol,
            side="buy",
            quantity=quantity,
            raw_price=raw_price,
            fill_price=fill_price,
            commission=commission,
            slippage_cost=(fill_price - raw_price) * quantity,
            primary_reason=reason,
            all_reasons=reason,
        )
        self.trades.append(trade)
        return trade

    def sell_quantity(
        self,
        symbol: str,
        datetime: object,
        raw_price: float,
        quantity: int,
        reason: str,
        all_reasons: str | None = None,
    ) -> Trade:
        """Sell part or all of a position and realize proportional net PnL."""
        if symbol not in self.positions:
            raise ValueError(f"position does not exist: {symbol}")
        position = self.positions[symbol]
        if (
            quantity <= 0
            or quantity > position.quantity
            or quantity % self.config.lot_size
        ):
            raise ValueError("quantity must be a held positive whole lot")
        fill_price = raw_price * (1 - self.config.slippage_rate)
        notional = fill_price * quantity
        commission = _commission(notional, self.config)
        self.cash += notional - commission
        allocated_entry_commission = (
            position.entry_commission * quantity / position.quantity
        )
        realized_pnl = (
            (fill_price - position.average_price) * quantity
            - allocated_entry_commission
            - commission
        )
        position.quantity -= quantity
        position.entry_commission -= allocated_entry_commission
        if position.quantity == 0:
            self.positions.pop(symbol)
        trade = Trade(
            datetime=pd.Timestamp(datetime),
            symbol=symbol,
            side="sell",
            quantity=quantity,
            raw_price=raw_price,
            fill_price=fill_price,
            commission=commission,
            slippage_cost=(raw_price - fill_price) * quantity,
            primary_reason=reason,
            all_reasons=all_reasons or reason,
            realized_pnl=realized_pnl,
        )
        self.trades.append(trade)
        return trade

    def sell(
        self,
        symbol: str,
        datetime: object,
        raw_price: float,
        reason: str,
        all_reasons: str | None = None,
    ) -> Trade:
        """Sell an entire position and realize its net PnL."""
        return self.sell_quantity(
            symbol,
            datetime,
            raw_price,
            self.positions[symbol].quantity,
            reason,
            all_reasons,
        )

    def update_after_close(
        self,
        symbol: str,
        close: float,
        ema10: float,
        ema20: float,
        atr5: float,
    ) -> str | None:
        """Advance winner state and tighten its close-based trailing stop."""
        position = self.positions[symbol]
        position.highest_close = max(position.highest_close, close)
        transition: str | None = None
        assert position.entry_price is not None
        promotion_price = position.entry_price + (
            self.config.winner_promotion_atr_multiple * position.entry_atr5
        )
        if (
            position.state == PositionState.TRIAL
            and position.highest_close >= promotion_price
            and close > ema10 > ema20
        ):
            position.state = PositionState.WINNER
            transition = "winner_promoted"
        if position.state == PositionState.WINNER:
            candidate_stop = (
                position.highest_close - self.config.atr_stop_multiple * atr5
            )
            position.stop_price = max(position.stop_price, candidate_stop)
        return transition

    def check_stop(
        self,
        symbol: str,
        datetime: object,
        open_price: float,
        low_price: float,
    ) -> Trade | None:
        """Execute a conservative daily gap or intraday hard stop."""
        position = self.positions.get(symbol)
        if position is None:
            return None
        if open_price <= position.stop_price:
            return self.sell(symbol, datetime, open_price, "atr_stop_gap")
        if low_price <= position.stop_price:
            return self.sell(symbol, datetime, position.stop_price, "atr_stop_intraday")
        return None

    def equity(self, close_prices: Mapping[str, float]) -> float:
        """Mark open positions to supplied close prices."""
        market_value = sum(
            position.quantity * close_prices.get(symbol, position.average_price)
            for symbol, position in self.positions.items()
        )
        return self.cash + market_value
