from __future__ import annotations

from dataclasses import dataclass
from math import floor
from typing import Any

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .portfolio import Portfolio

BAR_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume"]
SIGNAL_COLUMNS = BAR_COLUMNS + [
    "ema5",
    "previous_ema5",
    "target_invested",
    "action",
]
TRADE_COLUMNS = [
    "datetime",
    "symbol",
    "side",
    "quantity",
    "raw_price",
    "fill_price",
    "commission",
    "slippage_cost",
    "primary_reason",
    "all_reasons",
    "realized_pnl",
]
POSITION_COLUMNS = [
    "datetime",
    "symbol",
    "quantity",
    "average_price",
    "market_value",
    "equity",
    "weight",
]
EQUITY_COLUMNS = [
    "datetime",
    "cash",
    "market_value",
    "equity",
    "daily_return",
    "drawdown",
]


@dataclass(frozen=True)
class Ema5OpenConfig:
    """Execution settings for the single-ETF EMA5 open strategy."""

    symbol: str = "159915.SZ"
    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    slippage_rate: float = 0.0005

    def __post_init__(self) -> None:
        if not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if self.initial_capital <= 0 or self.lot_size <= 0:
            raise ValueError("initial_capital and lot_size must be positive")
        if min(self.commission_rate, self.min_commission, self.slippage_rate) < 0:
            raise ValueError("execution costs must not be negative")


@dataclass
class Ema5OpenResult:
    """Auditable signals, trades, positions, equity and summary outputs."""

    signals: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: dict[str, Any]


def prepare_symbol_bars(daily: pd.DataFrame, symbol: str) -> pd.DataFrame:
    """Validate and return one ETF's date-ordered daily OHLCV rows."""
    missing = set(BAR_COLUMNS) - set(daily.columns)
    if missing:
        raise ValueError(f"ETF daily data missing columns: {sorted(missing)}")
    frame = daily.loc[daily["symbol"].astype(str).eq(symbol), BAR_COLUMNS].copy()
    if frame.empty:
        raise ValueError(f"ETF daily data missing symbol: {symbol}")
    frame["datetime"] = pd.to_datetime(frame["datetime"], errors="raise")
    if frame["datetime"].isna().any():
        raise ValueError(f"ETF daily data contains missing dates for {symbol}")
    frame["datetime"] = frame["datetime"].dt.normalize()
    for column in BAR_COLUMNS[2:]:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if frame["datetime"].duplicated().any():
        raise ValueError(f"ETF daily data contains duplicate dates for {symbol}")
    prices = frame[["open", "high", "low", "close"]]
    valid = (
        np.isfinite(prices).all(axis=1)
        & (prices > 0).all(axis=1)
        & (frame["high"] >= frame[["open", "close"]].max(axis=1))
        & (frame["low"] <= frame[["open", "close"]].min(axis=1))
        & np.isfinite(frame["volume"])
        & (frame["volume"] >= 0)
    )
    if not valid.all():
        raise ValueError(f"ETF daily data contains invalid OHLCV rows for {symbol}")
    frame = frame.sort_values("datetime", ignore_index=True)
    if len(frame) < 6:
        raise ValueError("ETF daily data requires at least 6 rows for previous EMA5")
    return frame


def build_ema5_open_signals(bars: pd.DataFrame) -> pd.DataFrame:
    """Compare each open with the previous completed close EMA5."""
    result = bars.copy()
    result["ema5"] = result["close"].ewm(span=5, adjust=False, min_periods=5).mean()
    result["previous_ema5"] = result["ema5"].shift(1)
    result["target_invested"] = result["previous_ema5"].notna() & (
        result["open"] > result["previous_ema5"]
    )
    result["action"] = "flat"
    return result[SIGNAL_COLUMNS]


def _portfolio_config(config: Ema5OpenConfig) -> StrategyConfig:
    return StrategyConfig(
        initial_capital=config.initial_capital,
        lot_size=config.lot_size,
        commission_rate=config.commission_rate,
        min_commission=config.min_commission,
        slippage_rate=config.slippage_rate,
        max_position_weight=1.0,
    )


def calculate_full_position_quantity(
    available_cash: float,
    raw_price: float,
    config: Ema5OpenConfig,
) -> int:
    """Return the largest cash-funded whole-lot buy quantity."""
    if available_cash <= 0 or raw_price <= 0:
        return 0
    fill_price = raw_price * (1 + config.slippage_rate)
    quantity = floor(available_cash / fill_price / config.lot_size) * config.lot_size
    while quantity > 0:
        notional = quantity * fill_price
        commission = max(notional * config.commission_rate, config.min_commission)
        if notional + commission <= available_cash + 1e-9:
            return quantity
        quantity -= config.lot_size
    return 0


def run_ema5_open_backtest(
    bars: pd.DataFrame,
    config: Ema5OpenConfig | None = None,
) -> Ema5OpenResult:
    """Run the all-in/all-out strategy at each valid daily open."""
    cfg = config or Ema5OpenConfig()
    prepared = prepare_symbol_bars(bars, cfg.symbol)
    signals = build_ema5_open_signals(prepared)
    portfolio = Portfolio(cfg.initial_capital, _portfolio_config(cfg))
    position_rows: list[dict[str, object]] = []
    equity_rows: list[dict[str, object]] = []
    previous_equity = cfg.initial_capital
    peak_equity = cfg.initial_capital

    for index, row in signals.iterrows():
        invested = cfg.symbol in portfolio.positions
        target = bool(row["target_invested"])
        action = "hold" if invested else "flat"
        if target and not invested:
            quantity = calculate_full_position_quantity(
                portfolio.cash,
                float(row["open"]),
                cfg,
            )
            if quantity <= 0:
                raise ValueError("available cash cannot buy one whole ETF lot")
            portfolio.buy(
                cfg.symbol,
                row["datetime"],
                float(row["open"]),
                quantity,
                0.0,
                "open_above_previous_ema5",
            )
            action = "buy"
        elif not target and invested:
            portfolio.sell(
                cfg.symbol,
                row["datetime"],
                float(row["open"]),
                "open_at_or_below_previous_ema5",
            )
            action = "sell"
        signals.loc[index, "action"] = action

        position = portfolio.positions.get(cfg.symbol)
        market_value = (
            position.quantity * float(row["close"]) if position is not None else 0.0
        )
        equity = portfolio.cash + market_value
        daily_return = equity / previous_equity - 1 if previous_equity else 0.0
        peak_equity = max(peak_equity, equity)
        equity_rows.append(
            {
                "datetime": row["datetime"],
                "cash": portfolio.cash,
                "market_value": market_value,
                "equity": equity,
                "daily_return": daily_return,
                "drawdown": equity / peak_equity - 1,
            }
        )
        if position is not None:
            position_rows.append(
                {
                    "datetime": row["datetime"],
                    "symbol": cfg.symbol,
                    "quantity": position.quantity,
                    "average_price": position.average_price,
                    "market_value": market_value,
                    "equity": equity,
                    "weight": market_value / equity,
                }
            )
        previous_equity = equity

    trades = pd.DataFrame(
        [trade.to_dict() for trade in portfolio.trades],
        columns=TRADE_COLUMNS,
    )
    positions = pd.DataFrame(position_rows, columns=POSITION_COLUMNS)
    equity_curve = pd.DataFrame(equity_rows, columns=EQUITY_COLUMNS)
    summary = _build_summary(
        equity_curve,
        trades,
        cfg,
        cfg.symbol in portfolio.positions,
    )
    return Ema5OpenResult(signals, trades, positions, equity_curve, summary)


def _build_summary(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    config: Ema5OpenConfig,
    is_open: bool,
) -> dict[str, Any]:
    final_equity = float(equity_curve.iloc[-1]["equity"])
    total_return = final_equity / config.initial_capital - 1
    years = max(len(equity_curve) / 252, 1 / 252)
    annual_return = (1 + total_return) ** (1 / years) - 1 if total_return > -1 else -1.0
    returns = equity_curve["daily_return"]
    daily_std = float(returns.std(ddof=0))
    annual_volatility = daily_std * np.sqrt(252)
    sharpe = float(returns.mean() / daily_std * np.sqrt(252)) if daily_std > 0 else 0.0
    sells = trades.loc[trades["side"].eq("sell")]
    return {
        "symbol": config.symbol,
        "requested_period": "recent_10_years",
        "actual_start_date": str(pd.Timestamp(equity_curve.iloc[0]["datetime"]).date()),
        "actual_end_date": str(pd.Timestamp(equity_curve.iloc[-1]["datetime"]).date()),
        "initial_capital": config.initial_capital,
        "final_equity": final_equity,
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": annual_volatility,
        "sharpe": sharpe,
        "max_drawdown": float(equity_curve["drawdown"].min()),
        "trade_count": int(len(trades)),
        "completed_round_trips": int(len(sells)),
        "win_rate": (
            float(sells["realized_pnl"].gt(0).mean()) if not sells.empty else 0.0
        ),
        "total_commission": float(trades["commission"].sum()),
        "total_slippage_cost": float(trades["slippage_cost"].sum()),
        "is_open": is_open,
    }
