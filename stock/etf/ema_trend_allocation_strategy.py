from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import pandas as pd

from .ema5_open_strategy import BAR_COLUMNS, prepare_symbol_bars

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
    "target_weight",
    "action",
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
    result["target_weight"] = 0.0
    result["action"] = "flat"
    return result[SIGNAL_COLUMNS]
