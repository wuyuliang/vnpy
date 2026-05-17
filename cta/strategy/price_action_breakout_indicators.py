"""Indicators and data helpers for price action breakout strategy."""
from __future__ import annotations

import math
from pathlib import Path
from typing import Dict

import numpy as np
import pandas as pd

from cta.config.skill_tight_range_breakout_config import SYMBOLS_LIST_PATH
from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database

WARMUP_START = "2014-01-01"
BACKTEST_START = "2015-01-01"
BACKTEST_END = "2023-12-31"

SYMBOLS_CSV_PATH = str(SYMBOLS_LIST_PATH)
OUTPUT_DIR = "output_price_action_breakout"
TOP_N_LOW_VOL = 10
INITIAL_CAPITAL = 1_000_000.0
MONTHLY_STOP_LOSS_PCT = -0.02

RISK_PCT_GOOD = 0.005
RISK_PCT_NORMAL = 0.0025

BREAKOUT_WINDOW = 20
ATR_WINDOW = 14
EMA_WINDOW = 20
EMA_SLOW_WINDOW = 60
SWING_LOW_WINDOW = 5
VOL_MA_WINDOW = 20

INITIAL_STOP_ATR = 1.5
TRAIL_STOP_ATR = 2.0
BREAKEVEN_R = 1.0
MIN_EXPECTED_RR_GOOD = 1.8
MIN_EXPECTED_RR_NORMAL = 1.5

MAX_SIGNAL_BAR_RANGE_ATR = 3.0
SIGNAL_CLOSE_NEAR_HIGH = 0.30
VOL_EXPAND_RATIO_GOOD = 1.5
VOL_EXPAND_RATIO_NORMAL = 1.2

CONTRACT_CONFIG: Dict[str, Dict[str, float]] = {}


def ensure_dir(path: str | Path) -> Path:
    p = Path(path)
    p.mkdir(parents=True, exist_ok=True)
    return p


def safe_div(a: float, b: float, default: float = np.nan) -> float:
    if b is None or b == 0 or pd.isna(b):
        return default
    return a / b


def parse_exchange(exchange_str: str) -> Exchange:
    s = str(exchange_str).strip()
    if s.startswith("Exchange."):
        s = s.split(".", 1)[1]
    try:
        return Exchange[s]
    except Exception:
        for e in Exchange:
            if str(e.value).upper() == s.upper():
                return e
    raise ValueError(f"无法识别交易所: {exchange_str}")


def get_contract_meta(symbol: str, exchange: str) -> Dict[str, float]:
    vt_symbol = f"{symbol}.{exchange}"
    conf = CONTRACT_CONFIG.get(vt_symbol, {})
    return {
        "size": float(conf.get("size", 1.0)),
        "rate": float(conf.get("rate", 0.0)),
        "slippage": float(conf.get("slippage", 0.0)),
    }


def bars_to_df(bars) -> pd.DataFrame:
    rows = []
    for bar in bars:
        dt = pd.Timestamp(bar.datetime)
        if dt.tzinfo is not None:
            dt = dt.tz_localize(None)
        rows.append(
            {
                "datetime": dt,
                "open": float(bar.open_price),
                "high": float(bar.high_price),
                "low": float(bar.low_price),
                "close": float(bar.close_price),
                "volume": float(getattr(bar, "volume", 0.0)),
                "turnover": float(getattr(bar, "turnover", 0.0)),
                "open_interest": float(getattr(bar, "open_interest", 0.0)),
            }
        )
    df = pd.DataFrame(rows)
    if df.empty:
        return df
    df = df.sort_values("datetime").drop_duplicates("datetime").reset_index(drop=True)
    df = df[(df["open"] > 0) & (df["high"] > 0) & (df["low"] > 0) & (df["close"] > 0)].copy()
    return df


def load_daily_data(symbol: str, exchange: Exchange, start: str, end: str) -> pd.DataFrame:
    db = get_database()
    bars = db.load_bar_data(
        symbol=symbol,
        exchange=exchange,
        interval=Interval.DAILY,
        start=pd.Timestamp(start).to_pydatetime(),
        end=pd.Timestamp(end).to_pydatetime(),
    )
    return bars_to_df(bars)


def add_indicators(df: pd.DataFrame) -> pd.DataFrame:
    df = df.copy()
    prev_close = df["close"].shift(1)
    tr1 = df["high"] - df["low"]
    tr2 = (df["high"] - prev_close).abs()
    tr3 = (df["low"] - prev_close).abs()
    df["tr"] = pd.concat([tr1, tr2, tr3], axis=1).max(axis=1)
    df["atr"] = df["tr"].rolling(ATR_WINDOW, min_periods=ATR_WINDOW).mean()

    df["ema"] = df["close"].ewm(span=EMA_WINDOW, adjust=False).mean()
    df["ema_slow"] = df["close"].ewm(span=EMA_SLOW_WINDOW, adjust=False).mean()
    df["hh_prev"] = df["high"].shift(1).rolling(BREAKOUT_WINDOW, min_periods=BREAKOUT_WINDOW).max()
    df["ll_prev"] = df["low"].shift(1).rolling(BREAKOUT_WINDOW, min_periods=BREAKOUT_WINDOW).min()
    df["swing_low_prev"] = df["low"].shift(1).rolling(SWING_LOW_WINDOW, min_periods=1).min()
    df["vol_ma"] = df["volume"].shift(1).rolling(VOL_MA_WINDOW, min_periods=VOL_MA_WINDOW).mean()
    df["bar_range"] = df["high"] - df["low"]
    df["body"] = (df["close"] - df["open"]).abs()
    df["close_near_high"] = (df["high"] - df["close"]) / df["bar_range"].replace(0, np.nan)
    df["close_near_low"] = (df["close"] - df["low"]) / df["bar_range"].replace(0, np.nan)
    df["close_near_high"] = df["close_near_high"].fillna(1.0)
    df["close_near_low"] = df["close_near_low"].fillna(1.0)
    body_ratio = (df["body"] / df["bar_range"].replace(0, np.nan)).fillna(0)
    df["bull_strong"] = (
        (df["close"] > df["open"])
        & (body_ratio >= 0.55)
        & (df["close_near_high"] <= 0.25)
    )
    df["bear_strong"] = (
        (df["close"] < df["open"])
        & (body_ratio >= 0.55)
        & (df["close_near_low"] <= 0.25)
    )
    return df


def calc_annualized_vol(df: pd.DataFrame, start: str, end: str) -> float:
    sub = df[(df["datetime"] >= pd.Timestamp(start)) & (df["datetime"] <= pd.Timestamp(end))].copy()
    if len(sub) < 60:
        return np.nan
    ret = sub["close"].pct_change().dropna()
    if len(ret) < 30:
        return np.nan
    return float(ret.std(ddof=0) * math.sqrt(252))


def round_money(x) -> float:
    if pd.isna(x):
        return np.nan
    return round(float(x), 2)


def pct_str(x) -> str:
    if pd.isna(x):
        return ""
    return f"{float(x) * 100:.2f}%"


__all__ = [
    "WARMUP_START",
    "BACKTEST_START",
    "BACKTEST_END",
    "SYMBOLS_CSV_PATH",
    "OUTPUT_DIR",
    "TOP_N_LOW_VOL",
    "INITIAL_CAPITAL",
    "MONTHLY_STOP_LOSS_PCT",
    "RISK_PCT_GOOD",
    "RISK_PCT_NORMAL",
    "BREAKOUT_WINDOW",
    "ATR_WINDOW",
    "EMA_WINDOW",
    "EMA_SLOW_WINDOW",
    "SWING_LOW_WINDOW",
    "VOL_MA_WINDOW",
    "INITIAL_STOP_ATR",
    "TRAIL_STOP_ATR",
    "BREAKEVEN_R",
    "MIN_EXPECTED_RR_GOOD",
    "MIN_EXPECTED_RR_NORMAL",
    "MAX_SIGNAL_BAR_RANGE_ATR",
    "SIGNAL_CLOSE_NEAR_HIGH",
    "VOL_EXPAND_RATIO_GOOD",
    "VOL_EXPAND_RATIO_NORMAL",
    "ensure_dir",
    "safe_div",
    "parse_exchange",
    "get_contract_meta",
    "bars_to_df",
    "load_daily_data",
    "add_indicators",
    "calc_annualized_vol",
    "round_money",
    "pct_str",
]

