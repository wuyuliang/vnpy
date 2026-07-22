from __future__ import annotations

from dataclasses import dataclass
from math import floor, isfinite, sqrt
from sys import float_info
from typing import Any

import pandas as pd

from .config import StrategyConfig
from .ema5_open_strategy import BAR_COLUMNS, prepare_symbol_bars
from .portfolio import Portfolio

SIGNAL_COLUMNS = BAR_COLUMNS + [
    "ema5",
    "ema10",
    "ema_slow",
    "ema10_slope",
    "slow_slope",
    "previous_ema5",
    "previous_ema10",
    "previous_slow",
    "previous_ema10_slope",
    "previous_slow_slope",
    "enter_half",
    "enter_full",
    "reduce_half",
    "exit_flat",
    "ready",
    "risk_increase_blocked",
    "days_since_transition",
    "target_weight",
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
    "target_weight",
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
class TrendAllocationConfig:
    """Parameters for the preregistered three-level ETF trend strategy."""

    symbol: str = "159915.SZ"
    initial_capital: float = 1_000_000.0
    lot_size: int = 100
    commission_rate: float = 0.0003
    min_commission: float = 5.0
    slippage_rate: float = 0.0005
    slow_period: int = 20
    confirmation_days: int = 2
    slope_lookback: int = 3
    risk_increase_cooldown_days: int = 10

    def __post_init__(self) -> None:
        if not isinstance(self.symbol, str) or not self.symbol.strip():
            raise ValueError("symbol must not be empty")
        if not isfinite(self.initial_capital):
            raise ValueError("initial_capital must be finite")
        if self.initial_capital <= 0:
            raise ValueError("initial_capital must be positive")
        if type(self.lot_size) is not int or self.lot_size != 100:
            raise ValueError("lot_size must be Python int 100")
        costs = {
            "commission_rate": self.commission_rate,
            "min_commission": self.min_commission,
            "slippage_rate": self.slippage_rate,
        }
        for name, value in costs.items():
            if not isfinite(value):
                raise ValueError(f"{name} execution cost must be finite")
        if min(self.commission_rate, self.min_commission, self.slippage_rate) < 0:
            raise ValueError("execution costs must not be negative")
        if type(self.slow_period) is not int or self.slow_period not in {20, 30}:
            raise ValueError("slow_period must be 20 or 30")
        if type(self.confirmation_days) is not int or self.confirmation_days not in {
            1,
            2,
        }:
            raise ValueError("confirmation_days must be 1 or 2")
        if type(self.slope_lookback) is not int or self.slope_lookback not in {3, 5}:
            raise ValueError("slope_lookback must be 3 or 5")
        if (
            type(self.risk_increase_cooldown_days) is not int
            or self.risk_increase_cooldown_days <= 0
        ):
            raise ValueError("risk_increase_cooldown_days must be a positive int")


@dataclass
class TrendAllocationResult:
    """Auditable strategy signals, executions, holdings and performance."""

    signals: pd.DataFrame
    trades: pd.DataFrame
    positions: pd.DataFrame
    equity_curve: pd.DataFrame
    summary: dict[str, Any]


def transition_target_weight(
    current_weight: float,
    *,
    exit_flat: bool,
    reduce_half: bool,
    enter_half: bool,
    enter_full: bool,
) -> float:
    """Apply risk-first transitions between flat, half and full weights."""
    if isinstance(current_weight, bool) or current_weight not in {0.0, 0.5, 1.0}:
        raise ValueError("current_weight must be 0.0, 0.5, or 1.0")
    if exit_flat:
        return 0.0
    if current_weight == 1.0 and reduce_half:
        return 0.5
    if current_weight == 0.5 and enter_full:
        return 1.0
    if current_weight == 0.0 and enter_half:
        return 0.5
    return current_weight


def build_trend_signals(
    bars: pd.DataFrame,
    config: TrendAllocationConfig,
) -> pd.DataFrame:
    """Build open-time trend conditions using only completed daily bars."""
    result = prepare_symbol_bars(bars, config.symbol)
    minimum_rows = config.slow_period + config.slope_lookback + 1
    if len(result) < minimum_rows:
        raise ValueError(f"ETF daily data requires at least {minimum_rows} rows")

    result["ema5"] = (
        result["close"]
        .ewm(
            span=5,
            adjust=False,
            min_periods=5,
        )
        .mean()
    )
    result["ema10"] = (
        result["close"]
        .ewm(
            span=10,
            adjust=False,
            min_periods=10,
        )
        .mean()
    )
    result["ema_slow"] = (
        result["close"]
        .ewm(
            span=config.slow_period,
            adjust=False,
            min_periods=config.slow_period,
        )
        .mean()
    )
    result["ema10_slope"] = (
        result["ema10"] / result["ema10"].shift(config.slope_lookback) - 1
    )
    result["slow_slope"] = (
        result["ema_slow"] / result["ema_slow"].shift(config.slope_lookback) - 1
    )
    result["previous_ema5"] = result["ema5"].shift(1)
    result["previous_ema10"] = result["ema10"].shift(1)
    result["previous_slow"] = result["ema_slow"].shift(1)
    result["previous_ema10_slope"] = result["ema10_slope"].shift(1)
    result["previous_slow_slope"] = result["slow_slope"].shift(1)

    fast_bull = result["ema5"] > result["ema10"]
    medium_bull = fast_bull & (result["ema10"] > result["ema_slow"])
    fast_bear = result["ema5"] <= result["ema10"]
    medium_bear = result["ema10"] <= result["ema_slow"]
    days = config.confirmation_days
    fast_bull_confirmed = fast_bull.rolling(days, min_periods=days).min().shift(1)
    medium_bull_confirmed = medium_bull.rolling(days, min_periods=days).min().shift(1)
    fast_bear_confirmed = fast_bear.rolling(days, min_periods=days).min().shift(1)
    medium_bear_confirmed = medium_bear.rolling(days, min_periods=days).min().shift(1)

    previous_columns = [
        "previous_ema5",
        "previous_ema10",
        "previous_slow",
        "previous_ema10_slope",
        "previous_slow_slope",
    ]
    confirmations = pd.concat(
        [
            fast_bull_confirmed,
            medium_bull_confirmed,
            fast_bear_confirmed,
            medium_bear_confirmed,
        ],
        axis=1,
    )
    ready = result[previous_columns].notna().all(axis=1) & confirmations.notna().all(
        axis=1
    )
    result["enter_half"] = (
        ready
        & (result["open"] > result["previous_ema10"])
        & fast_bull_confirmed.eq(1.0)
        & (result["previous_ema10_slope"] > 0)
    )
    result["enter_full"] = (
        ready
        & (result["open"] > result["previous_ema5"])
        & medium_bull_confirmed.eq(1.0)
        & (result["previous_slow_slope"] > 0)
    )
    result["reduce_half"] = ready & (
        (result["open"] < result["previous_ema10"]) | fast_bear_confirmed.eq(1.0)
    )
    result["exit_flat"] = ready & (
        (result["open"] < result["previous_slow"]) | medium_bear_confirmed.eq(1.0)
    )
    result["ready"] = ready
    result["risk_increase_blocked"] = False
    result["days_since_transition"] = pd.NA
    result["target_weight"] = 0.0
    result["action"] = "flat"
    return result[SIGNAL_COLUMNS]


def calculate_target_quantity(
    equity: float,
    raw_price: float,
    weight: float,
    lot_size: int,
) -> int:
    """Convert a target portfolio weight to a raw-price whole-lot quantity."""
    if isinstance(equity, bool) or not isfinite(equity) or equity <= 0:
        raise ValueError("equity must be finite and positive")
    if isinstance(raw_price, bool) or not isfinite(raw_price) or raw_price <= 0:
        raise ValueError("raw_price must be finite and positive")
    if isinstance(weight, bool) or weight not in {0.0, 0.5, 1.0}:
        raise ValueError("weight must be 0.0, 0.5, or 1.0")
    if type(lot_size) is not int or lot_size != 100:
        raise ValueError("lot_size must be Python int 100")
    return floor(equity * weight / raw_price / lot_size) * lot_size


def _portfolio_config(config: TrendAllocationConfig) -> StrategyConfig:
    return StrategyConfig(
        initial_capital=config.initial_capital,
        lot_size=config.lot_size,
        commission_rate=config.commission_rate,
        min_commission=config.min_commission,
        slippage_rate=config.slippage_rate,
        max_position_weight=1.0,
    )


def _affordable_buy_quantity(
    cash: float,
    raw_price: float,
    desired_quantity: int,
    config: TrendAllocationConfig,
) -> int:
    fill_price = raw_price * (1 + config.slippage_rate)
    quantity = desired_quantity
    while quantity > 0:
        notional = fill_price * quantity
        commission = max(notional * config.commission_rate, config.min_commission)
        if notional + commission <= cash:
            return quantity
        quantity -= config.lot_size
    return 0


def execute_target_weights(
    signals: pd.DataFrame,
    config: TrendAllocationConfig,
) -> TrendAllocationResult:
    """Execute precomputed trend conditions as a daily three-level state machine."""
    audited_signals = signals.copy()
    portfolio = Portfolio(config.initial_capital, _portfolio_config(config))
    position_rows: list[dict[str, object]] = []
    equity_rows: list[dict[str, object]] = []
    current_weight = 0.0
    last_transition_ordinal: int | None = None
    previous_equity = config.initial_capital
    peak_equity = config.initial_capital
    audited_signals["risk_increase_blocked"] = False
    audited_signals["days_since_transition"] = pd.Series(
        pd.array([pd.NA] * len(audited_signals), dtype="Int64"),
        index=audited_signals.index,
    )

    for trading_day_ordinal, (index, row) in enumerate(audited_signals.iterrows()):
        proposed_weight = transition_target_weight(
            current_weight,
            exit_flat=bool(row["exit_flat"]),
            reduce_half=bool(row["reduce_half"]),
            enter_half=bool(row["enter_half"]),
            enter_full=bool(row["enter_full"]),
        )
        days_since_transition = (
            None
            if last_transition_ordinal is None
            else trading_day_ordinal - last_transition_ordinal - 1
        )
        risk_increase_blocked = bool(
            proposed_weight > current_weight
            and days_since_transition is not None
            and days_since_transition < config.risk_increase_cooldown_days
        )
        target_weight = current_weight if risk_increase_blocked else proposed_weight
        position = portfolio.positions.get(config.symbol)
        held_quantity = position.quantity if position is not None else 0
        raw_open = float(row["open"])
        action = "hold"
        if target_weight != current_weight:
            pre_trade_equity = portfolio.cash + held_quantity * raw_open
            target_quantity = calculate_target_quantity(
                pre_trade_equity,
                raw_open,
                target_weight,
                config.lot_size,
            )
            reason = f"target_weight_{current_weight:g}_to_{target_weight:g}"
            action = (
                "flat"
                if position is None
                else {
                    0.0: "flat",
                    0.5: "hold_half",
                    1.0: "hold_full",
                }[current_weight]
            )
            is_upgrade = target_weight > current_weight
            upgrade_succeeded = target_quantity > 0 and held_quantity >= target_quantity

            if target_quantity > held_quantity:
                buy_quantity = _affordable_buy_quantity(
                    portfolio.cash,
                    raw_open,
                    target_quantity - held_quantity,
                    config,
                )
                if buy_quantity > 0:
                    if position is None:
                        portfolio.buy(
                            config.symbol,
                            row["datetime"],
                            raw_open,
                            buy_quantity,
                            0.0,
                            reason,
                        )
                    else:
                        portfolio.increase(
                            config.symbol,
                            row["datetime"],
                            raw_open,
                            buy_quantity,
                            reason,
                        )
                    action = "buy_to_half" if target_weight == 0.5 else "buy_to_full"
                    upgrade_succeeded = True
            elif target_quantity < held_quantity:
                portfolio.sell_quantity(
                    config.symbol,
                    row["datetime"],
                    raw_open,
                    held_quantity - target_quantity,
                    reason,
                )
                action = "sell_to_flat" if target_weight == 0.0 else "sell_to_half"

            if is_upgrade:
                if upgrade_succeeded and action not in {"buy_to_half", "buy_to_full"}:
                    action = "hold_half" if target_weight == 0.5 else "hold_full"
                elif not upgrade_succeeded:
                    target_weight = current_weight
            if target_weight != current_weight:
                last_transition_ordinal = trading_day_ordinal
            current_weight = target_weight
        audited_signals.loc[index, "risk_increase_blocked"] = risk_increase_blocked
        audited_signals.loc[index, "days_since_transition"] = days_since_transition
        audited_signals.loc[index, "target_weight"] = target_weight
        audited_signals.loc[index, "action"] = action

        position = portfolio.positions.get(config.symbol)
        market_value = (
            position.quantity * float(row["close"]) if position is not None else 0.0
        )
        equity = portfolio.cash + market_value
        daily_return = equity / previous_equity - 1
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
                    "symbol": config.symbol,
                    "quantity": position.quantity,
                    "average_price": position.average_price,
                    "market_value": market_value,
                    "equity": equity,
                    "weight": market_value / equity,
                    "target_weight": target_weight,
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
        config,
        current_weight,
        config.symbol in portfolio.positions,
    )
    return TrendAllocationResult(
        audited_signals,
        trades,
        positions,
        equity_curve,
        summary,
    )


def run_trend_allocation_backtest(
    bars: pd.DataFrame,
    config: TrendAllocationConfig | None = None,
) -> TrendAllocationResult:
    """Build trend conditions and execute the three-level allocation strategy."""
    cfg = config or TrendAllocationConfig()
    return execute_target_weights(build_trend_signals(bars, cfg), cfg)


def calculate_period_metrics(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    start: object,
    end: object,
) -> dict[str, Any]:
    """Calculate inclusive period metrics with returns and drawdown reset."""
    start_date = pd.Timestamp(start).normalize()
    end_date = pd.Timestamp(end).normalize()
    if start_date > end_date:
        raise ValueError("start must be on or before end")
    if not {"datetime", "equity"}.issubset(equity_curve.columns):
        raise ValueError("equity_curve requires datetime and equity columns")

    equity = equity_curve.copy()
    equity["datetime"] = pd.to_datetime(equity["datetime"], errors="raise")
    try:
        equity["equity"] = pd.to_numeric(equity["equity"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("equity values must be numeric, finite, and positive") from exc
    equity_values = equity["equity"]
    if (
        equity_values.isna().any()
        or not equity_values.dropna().map(isfinite).all()
        or (equity_values <= 0).any()
    ):
        raise ValueError("equity values must be numeric, finite, and positive")

    equity_dates = equity["datetime"].dt.normalize()
    period = equity.loc[
        equity_dates.between(start_date, end_date, inclusive="both")
    ].copy()
    if period.empty:
        raise ValueError("no equity rows in requested period")

    period_returns = period["equity"].pct_change().fillna(0.0)
    drawdown = period["equity"] / period["equity"].cummax() - 1
    days = len(period)
    return_years = max((days - 1) / 252, 1 / 252)
    turnover_years = days / 252
    initial_equity = float(period.iloc[0]["equity"])
    final_equity = float(period.iloc[-1]["equity"])
    total_return = final_equity / initial_equity - 1
    annual_return = (
        (1 + total_return) ** (1 / return_years) - 1 if total_return > -1 else -1.0
    )
    daily_std = float(period_returns.std(ddof=0))
    mean_equity = float(period["equity"].mean())
    if not isfinite(mean_equity) or mean_equity <= 0:
        raise ValueError("period mean equity must be finite and positive")

    period_trades = trades.copy()
    if period_trades.empty:
        traded_notional = 0.0
    else:
        required_trade_columns = {"datetime", "fill_price", "quantity"}
        if not required_trade_columns.issubset(period_trades.columns):
            raise ValueError(
                "non-empty trades require datetime, fill_price, and quantity"
            )
        period_trades["datetime"] = pd.to_datetime(
            period_trades["datetime"], errors="raise"
        )
        trade_dates = period_trades["datetime"].dt.normalize()
        period_trades = period_trades.loc[
            trade_dates.between(
                start_date,
                end_date,
                inclusive="both",
            )
        ]
        traded_notional = float(
            (period_trades["fill_price"] * period_trades["quantity"]).abs().sum()
        )
    annual_one_way_turnover = 0.5 * traded_notional / mean_equity / turnover_years
    return {
        "start_date": str(pd.Timestamp(period.iloc[0]["datetime"]).date()),
        "end_date": str(pd.Timestamp(period.iloc[-1]["datetime"]).date()),
        "days": days,
        "trading_days": days,
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": daily_std * sqrt(252),
        "sharpe": (
            float(period_returns.mean()) / daily_std * sqrt(252)
            if daily_std > 0
            else 0.0
        ),
        "max_drawdown": float(drawdown.min()),
        "annual_one_way_turnover": annual_one_way_turnover,
        "trade_count": int(len(period_trades)),
    }


def _build_summary(
    equity_curve: pd.DataFrame,
    trades: pd.DataFrame,
    config: TrendAllocationConfig,
    target_weight: float,
    is_open: bool,
) -> dict[str, Any]:
    final_equity = float(equity_curve.iloc[-1]["equity"])
    total_return = final_equity / config.initial_capital - 1
    days = len(equity_curve)
    return_years = max((days - 1) / 252, 1 / 252)
    turnover_years = days / 252
    if total_return <= -1:
        annual_return = -1.0
    else:
        try:
            annual_return = (1 + total_return) ** (1 / return_years) - 1
        except OverflowError:
            annual_return = float_info.max
    returns = equity_curve["equity"].pct_change(fill_method=None).fillna(0.0)
    daily_std = float(returns.std(ddof=0))
    traded_notional = float((trades["fill_price"] * trades["quantity"]).abs().sum())
    return {
        "symbol": config.symbol,
        "start_date": str(pd.Timestamp(equity_curve.iloc[0]["datetime"]).date()),
        "end_date": str(pd.Timestamp(equity_curve.iloc[-1]["datetime"]).date()),
        "days": days,
        "trading_days": days,
        "initial_equity": config.initial_capital,
        "initial_capital": config.initial_capital,
        "final_equity": final_equity,
        "total_return": total_return,
        "annual_return": annual_return,
        "annual_volatility": daily_std * sqrt(252),
        "sharpe": (
            float(returns.mean()) / daily_std * sqrt(252) if daily_std > 0 else 0.0
        ),
        "max_drawdown": float(equity_curve["drawdown"].min()),
        "annual_one_way_turnover": (
            0.5
            * traded_notional
            / float(equity_curve["equity"].mean())
            / turnover_years
        ),
        "trade_count": int(len(trades)),
        "total_commission": float(trades["commission"].sum()),
        "total_slippage_cost": float(trades["slippage_cost"].sum()),
        "target_weight": target_weight,
        "is_open": is_open,
    }
