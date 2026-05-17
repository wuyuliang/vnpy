"""Trading rules for price action breakout strategy."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, Tuple

import numpy as np
import pandas as pd

from cta.strategy.price_action_breakout_indicators import (
    INITIAL_STOP_ATR,
    MAX_SIGNAL_BAR_RANGE_ATR,
    MIN_EXPECTED_RR_GOOD,
    MIN_EXPECTED_RR_NORMAL,
    RISK_PCT_GOOD,
    RISK_PCT_NORMAL,
    SIGNAL_CLOSE_NEAR_HIGH,
    VOL_EXPAND_RATIO_GOOD,
    VOL_EXPAND_RATIO_NORMAL,
    safe_div,
)


@dataclass
class TradeRecord:
    symbol: str
    exchange: str
    vt_symbol: str
    setup_type: str
    entry_date: str
    exit_date: str
    entry_price: float
    exit_price: float
    quantity: int
    turnover_entry: float
    turnover_exit: float
    pnl: float
    return_pct: float
    holding_days: int
    entry_reason: str
    exit_reason: str
    initial_stop: float
    final_stop: float
    highest_since_entry: float
    risk_per_unit: float
    risk_budget: float
    r_multiple: float


def classify_setup(df: pd.DataFrame, y_idx: int) -> Tuple[bool, Dict[str, float | str]]:
    """Use previous bar to decide whether next bar can open position."""
    y = df.iloc[y_idx]
    need_cols = ["atr", "ema", "ema_slow", "hh_prev", "ll_prev", "vol_ma", "bar_range"]
    if any(pd.isna(y[c]) for c in need_cols):
        return False, {}

    breakout = y["close"] > y["hh_prev"]
    bull_close = (y["close"] > y["open"]) and (y["close_near_high"] <= SIGNAL_CLOSE_NEAR_HIGH)
    above_fast_ema = y["close"] > y["ema"]
    above_slow_ema = y["close"] > y["ema_slow"]
    ema_trend_up = y["ema"] > y["ema_slow"]
    range_ok = safe_div(y["bar_range"], y["atr"], 999) <= MAX_SIGNAL_BAR_RANGE_ATR
    vol_ratio = safe_div(y["volume"], y["vol_ma"], 0.0)

    if not (breakout and bull_close and above_fast_ema and above_slow_ema and ema_trend_up and range_ok):
        return False, {}

    stop_from_signal_low = y["low"] - 0.10 * y["atr"]
    stop_from_atr = y["close"] - INITIAL_STOP_ATR * y["atr"]
    initial_stop_ref = max(stop_from_signal_low, stop_from_atr)
    measured_target = y["hh_prev"] + max(0.0, y["hh_prev"] - y["ll_prev"])

    body_ratio = safe_div(abs(y["close"] - y["open"]), y["bar_range"], 0.0)
    is_good = (
        vol_ratio >= VOL_EXPAND_RATIO_GOOD
        and body_ratio >= 0.60
        and y["close_near_high"] <= 0.20
        and safe_div(y["close"] - y["ema"], y["atr"], 0.0) >= 0.30
    )
    if is_good:
        return True, {
            "setup_type": "GOOD_SETUP",
            "risk_pct": RISK_PCT_GOOD,
            "min_rr": MIN_EXPECTED_RR_GOOD,
            "vol_ratio": float(vol_ratio),
            "entry_reason": "good_breakout_with_volume_expansion",
            "initial_stop_ref": float(initial_stop_ref),
            "measured_target": float(measured_target),
        }

    is_normal = vol_ratio >= VOL_EXPAND_RATIO_NORMAL
    if is_normal:
        return True, {
            "setup_type": "NORMAL_SETUP",
            "risk_pct": RISK_PCT_NORMAL,
            "min_rr": MIN_EXPECTED_RR_NORMAL,
            "vol_ratio": float(vol_ratio),
            "entry_reason": "normal_breakout_with_volume_expansion",
            "initial_stop_ref": float(initial_stop_ref),
            "measured_target": float(measured_target),
        }
    return False, {}


def gen_exit_signal(df: pd.DataFrame, y_idx: int, highest_since_entry: float, entry_price: float) -> Tuple[bool, str]:
    if y_idx < 2:
        return False, ""
    y = df.iloc[y_idx]
    p1 = df.iloc[y_idx - 1]
    outside_down = (
        (y["high"] > p1["high"])
        and (y["low"] < p1["low"])
        and (y["close"] < p1["low"])
        and (y["close"] < y["open"])
    )
    two_bear_bars = bool(y["bear_strong"]) and bool(p1["bear_strong"])
    failed_breakout = (
        (highest_since_entry > entry_price * 1.005)
        and (not pd.isna(y["hh_prev"]))
        and (y["close"] < y["hh_prev"])
    )
    lose_ema = (y["close"] < y["ema"]) and (y["close"] < y["open"])

    if outside_down:
        return True, "outside_down_reversal_exit_next_open"
    if two_bear_bars:
        return True, "two_strong_bear_bars_exit_next_open"
    if failed_breakout:
        return True, "failed_breakout_exit_next_open"
    if lose_ema:
        return True, "lose_ema_exit_next_open"
    return False, ""


def calc_position_size(
    capital: float,
    risk_pct: float,
    entry_price: float,
    stop_price: float,
    size: float,
) -> Tuple[int, float, float]:
    """Compute quantity from risk budget."""
    risk_budget = capital * risk_pct
    risk_per_unit = entry_price - stop_price
    if risk_per_unit <= 0:
        return 0, 0.0, 0.0
    qty = int(risk_budget // (risk_per_unit * size))
    if qty < 1:
        return 0, risk_budget, risk_per_unit
    return qty, risk_budget, risk_per_unit


__all__ = [
    "TradeRecord",
    "classify_setup",
    "gen_exit_signal",
    "calc_position_size",
]

