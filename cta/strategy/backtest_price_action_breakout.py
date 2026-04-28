# -*- coding: utf-8 -*-
"""
价格行为学突破策略 V2
--------------------------------
增强点：
1. 开仓更严格：增加放量过滤 + 趋势过滤
2. 动态仓位：按单笔风险金额反推手数
3. 月度止损：当月累计亏损达到 -2% 后停止新开仓
4. 输出格式：
   - 金额保留2位小数
   - 收益率/胜率/回报率输出百分比字符串
5. 输出分层统计：
   - ALL
   - GOOD_SETUP
   - NORMAL_SETUP
"""

from __future__ import annotations

import math
import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from cta.config.skill_tight_range_breakout_config import SYMBOLS_LIST_PATH
from vnpy.trader.database import get_database
from vnpy.trader.constant import Exchange, Interval


# =========================================================
# 参数区
# =========================================================
WARMUP_START = "2014-01-01"
BACKTEST_START = "2015-01-01"
BACKTEST_END = "2023-12-31"

SYMBOLS_CSV_PATH = str(SYMBOLS_LIST_PATH)
OUTPUT_DIR = "output_price_action_breakout"

TOP_N_LOW_VOL = 10

# 总资金
INITIAL_CAPITAL = 1_000_000.0

# 单月最大亏损阈值：-2%
MONTHLY_STOP_LOSS_PCT = -0.02

# 单笔风险预算
RISK_PCT_GOOD = 0.005      # 0.5%
RISK_PCT_NORMAL = 0.0025   # 0.25%

# 策略参数
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

# 放量条件
VOL_EXPAND_RATIO_GOOD = 1.5
VOL_EXPAND_RATIO_NORMAL = 1.2

# 合约参数，未配置则默认 size=1, rate=0, slippage=0
CONTRACT_CONFIG: Dict[str, Dict[str, float]] = {
    # "rb888.SHFE": {"size": 10, "rate": 0.00002, "slippage": 1.0},
    # "au888.SHFE": {"size": 1000, "rate": 0.00002, "slippage": 0.02},
}


# =========================================================
# 数据结构
# =========================================================
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


# =========================================================
# 基础工具
# =========================================================
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
        (df["close"] > df["open"]) &
        (body_ratio >= 0.55) &
        (df["close_near_high"] <= 0.25)
    )

    df["bear_strong"] = (
        (df["close"] < df["open"]) &
        (body_ratio >= 0.55) &
        (df["close_near_low"] <= 0.25)
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


# =========================================================
# 信号模块
# =========================================================
def classify_setup(df: pd.DataFrame, y_idx: int) -> Tuple[bool, Dict[str, float | str]]:
    """
    用昨天的数据判断今天是否开仓。
    分为：
    - GOOD_SETUP
    - NORMAL_SETUP
    """
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

    # GOOD_SETUP：更强趋势 + 更大放量 + 强bar
    body_ratio = safe_div(abs(y["close"] - y["open"]), y["bar_range"], 0.0)
    is_good = (
        vol_ratio >= VOL_EXPAND_RATIO_GOOD and
        body_ratio >= 0.60 and
        y["close_near_high"] <= 0.20 and
        safe_div(y["close"] - y["ema"], y["atr"], 0.0) >= 0.30
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

    # NORMAL_SETUP：普通放量即可
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
        (y["high"] > p1["high"]) and
        (y["low"] < p1["low"]) and
        (y["close"] < p1["low"]) and
        (y["close"] < y["open"])
    )

    two_bear_bars = bool(y["bear_strong"]) and bool(p1["bear_strong"])

    failed_breakout = (
        (highest_since_entry > entry_price * 1.005) and
        (not pd.isna(y["hh_prev"])) and
        (y["close"] < y["hh_prev"])
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


# =========================================================
# 回测主逻辑
# =========================================================
def calc_position_size(capital: float, risk_pct: float, entry_price: float, stop_price: float, size: float) -> Tuple[int, float, float]:
    """
    根据总资金、风险比例、止损距离，反推下单手数
    """
    risk_budget = capital * risk_pct
    risk_per_unit = entry_price - stop_price

    if risk_per_unit <= 0:
        return 0, 0.0, 0.0

    qty = int(risk_budget // (risk_per_unit * size))
    if qty < 1:
        return 0, risk_budget, risk_per_unit

    return qty, risk_budget, risk_per_unit


def backtest_one_symbol(df_raw: pd.DataFrame, symbol: str, exchange: str) -> Tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    if df_raw.empty:
        return pd.DataFrame(), pd.DataFrame(), pd.DataFrame()

    df = add_indicators(df_raw)
    df = df[(df["datetime"] >= pd.Timestamp(WARMUP_START)) & (df["datetime"] <= pd.Timestamp(BACKTEST_END))].copy()
    df = df.reset_index(drop=True)

    meta = get_contract_meta(symbol, exchange)
    size = meta["size"]
    rate = meta["rate"]
    slippage = meta["slippage"]

    vt_symbol = f"{symbol}.{exchange}"

    position = 0
    qty = 0
    setup_type = ""

    entry_price = np.nan
    entry_date: Optional[pd.Timestamp] = None
    initial_stop = np.nan
    stop_price = np.nan
    highest_since_entry = -np.inf
    holding_days = 0
    entry_reason = ""
    risk_per_unit = 0.0
    risk_budget = 0.0

    realized_pnl = 0.0
    capital = INITIAL_CAPITAL

    equity_rows: List[Dict] = []
    trade_rows: List[TradeRecord] = []

    min_bars = max(BREAKOUT_WINDOW, ATR_WINDOW, EMA_WINDOW, EMA_SLOW_WINDOW, VOL_MA_WINDOW) + 3

    monthly_start_equity_map: Dict[str, float] = {}
    month_stop_trading_map: Dict[str, bool] = {}

    for i in range(min_bars, len(df)):
        today = df.iloc[i]
        today_dt = pd.Timestamp(today["datetime"])
        in_range = (today_dt >= pd.Timestamp(BACKTEST_START)) and (today_dt <= pd.Timestamp(BACKTEST_END))
        month_key = today_dt.strftime("%Y-%m")

        # 初始化每月起始权益
        if month_key not in monthly_start_equity_map:
            monthly_start_equity_map[month_key] = capital if not equity_rows else equity_rows[-1]["equity"]
            month_stop_trading_map[month_key] = False

        # 当前月是否已触发停手
        current_month_start_equity = monthly_start_equity_map[month_key]
        current_equity_pre = capital if not equity_rows else equity_rows[-1]["equity"]
        current_month_return_pre = safe_div(current_equity_pre - current_month_start_equity, current_month_start_equity, 0.0)

        if current_month_return_pre <= MONTHLY_STOP_LOSS_PCT:
            month_stop_trading_map[month_key] = True

        # -------------------------------------------------
        # A. 持仓开盘退出
        # -------------------------------------------------
        if position > 0:
            if today["open"] <= stop_price:
                exit_price = max(0.0, today["open"] - slippage)
                turnover_entry = entry_price * qty * size
                turnover_exit = exit_price * qty * size
                commission = (turnover_entry + turnover_exit) * rate
                pnl = (exit_price - entry_price) * qty * size - commission
                realized_pnl += pnl
                capital += pnl

                r_multiple = safe_div(exit_price - entry_price, entry_price - initial_stop, np.nan)

                trade_rows.append(
                    TradeRecord(
                        symbol=symbol,
                        exchange=exchange,
                        vt_symbol=vt_symbol,
                        setup_type=setup_type,
                        entry_date=str(entry_date.date()),
                        exit_date=str(today_dt.date()),
                        entry_price=float(entry_price),
                        exit_price=float(exit_price),
                        quantity=int(qty),
                        turnover_entry=float(turnover_entry),
                        turnover_exit=float(turnover_exit),
                        pnl=float(pnl),
                        return_pct=float((exit_price - entry_price) / entry_price),
                        holding_days=int(holding_days),
                        entry_reason=entry_reason,
                        exit_reason="gap_down_stop_at_open",
                        initial_stop=float(initial_stop),
                        final_stop=float(stop_price),
                        highest_since_entry=float(highest_since_entry),
                        risk_per_unit=float(risk_per_unit),
                        risk_budget=float(risk_budget),
                        r_multiple=float(r_multiple),
                    )
                )

                position = 0
                qty = 0
                setup_type = ""
                entry_price = np.nan
                entry_date = None
                initial_stop = np.nan
                stop_price = np.nan
                highest_since_entry = -np.inf
                holding_days = 0
                entry_reason = ""
                risk_per_unit = 0.0
                risk_budget = 0.0

            else:
                exit_signal, exit_reason = gen_exit_signal(df, i - 1, highest_since_entry, entry_price)

                if exit_signal:
                    exit_price = max(0.0, today["open"] - slippage)
                    turnover_entry = entry_price * qty * size
                    turnover_exit = exit_price * qty * size
                    commission = (turnover_entry + turnover_exit) * rate
                    pnl = (exit_price - entry_price) * qty * size - commission
                    realized_pnl += pnl
                    capital += pnl

                    r_multiple = safe_div(exit_price - entry_price, entry_price - initial_stop, np.nan)

                    trade_rows.append(
                        TradeRecord(
                            symbol=symbol,
                            exchange=exchange,
                            vt_symbol=vt_symbol,
                            setup_type=setup_type,
                            entry_date=str(entry_date.date()),
                            exit_date=str(today_dt.date()),
                            entry_price=float(entry_price),
                            exit_price=float(exit_price),
                            quantity=int(qty),
                            turnover_entry=float(turnover_entry),
                            turnover_exit=float(turnover_exit),
                            pnl=float(pnl),
                            return_pct=float((exit_price - entry_price) / entry_price),
                            holding_days=int(holding_days),
                            entry_reason=entry_reason,
                            exit_reason=exit_reason,
                            initial_stop=float(initial_stop),
                            final_stop=float(stop_price),
                            highest_since_entry=float(highest_since_entry),
                            risk_per_unit=float(risk_per_unit),
                            risk_budget=float(risk_budget),
                            r_multiple=float(r_multiple),
                        )
                    )

                    position = 0
                    qty = 0
                    setup_type = ""
                    entry_price = np.nan
                    entry_date = None
                    initial_stop = np.nan
                    stop_price = np.nan
                    highest_since_entry = -np.inf
                    holding_days = 0
                    entry_reason = ""
                    risk_per_unit = 0.0
                    risk_budget = 0.0

        # -------------------------------------------------
        # B. 空仓开仓
        # -------------------------------------------------
        if position == 0 and in_range and (not month_stop_trading_map[month_key]):
            entry_signal, info = classify_setup(df, i - 1)
            if entry_signal:
                buy_price = today["open"] + slippage
                initial_stop_candidate = float(info["initial_stop_ref"])

                if initial_stop_candidate < buy_price:
                    target = float(info["measured_target"])
                    risk = buy_price - initial_stop_candidate
                    expected_rr = safe_div(target - buy_price, risk, -999)

                    if expected_rr >= float(info["min_rr"]):
                        qty_candidate, risk_budget_candidate, risk_per_unit_candidate = calc_position_size(
                            capital=capital,
                            risk_pct=float(info["risk_pct"]),
                            entry_price=buy_price,
                            stop_price=initial_stop_candidate,
                            size=size,
                        )

                        if qty_candidate >= 1:
                            position = 1
                            qty = qty_candidate
                            setup_type = str(info["setup_type"])
                            entry_price = buy_price
                            entry_date = today_dt
                            initial_stop = initial_stop_candidate
                            stop_price = initial_stop_candidate
                            highest_since_entry = today["high"]
                            holding_days = 1
                            entry_reason = str(info["entry_reason"])
                            risk_per_unit = risk_per_unit_candidate
                            risk_budget = risk_budget_candidate

                            # 当天若直接打止损
                            if today["low"] <= stop_price:
                                exit_price = max(0.0, stop_price - slippage)
                                turnover_entry = entry_price * qty * size
                                turnover_exit = exit_price * qty * size
                                commission = (turnover_entry + turnover_exit) * rate
                                pnl = (exit_price - entry_price) * qty * size - commission
                                realized_pnl += pnl
                                capital += pnl

                                r_multiple = safe_div(exit_price - entry_price, entry_price - initial_stop, np.nan)

                                trade_rows.append(
                                    TradeRecord(
                                        symbol=symbol,
                                        exchange=exchange,
                                        vt_symbol=vt_symbol,
                                        setup_type=setup_type,
                                        entry_date=str(entry_date.date()),
                                        exit_date=str(today_dt.date()),
                                        entry_price=float(entry_price),
                                        exit_price=float(exit_price),
                                        quantity=int(qty),
                                        turnover_entry=float(turnover_entry),
                                        turnover_exit=float(turnover_exit),
                                        pnl=float(pnl),
                                        return_pct=float((exit_price - entry_price) / entry_price),
                                        holding_days=int(holding_days),
                                        entry_reason=entry_reason,
                                        exit_reason="same_day_initial_stop",
                                        initial_stop=float(initial_stop),
                                        final_stop=float(stop_price),
                                        highest_since_entry=float(highest_since_entry),
                                        risk_per_unit=float(risk_per_unit),
                                        risk_budget=float(risk_budget),
                                        r_multiple=float(r_multiple),
                                    )
                                )

                                position = 0
                                qty = 0
                                setup_type = ""
                                entry_price = np.nan
                                entry_date = None
                                initial_stop = np.nan
                                stop_price = np.nan
                                highest_since_entry = -np.inf
                                holding_days = 0
                                entry_reason = ""
                                risk_per_unit = 0.0
                                risk_budget = 0.0

        # -------------------------------------------------
        # C. 持仓更新
        # -------------------------------------------------
        mark_price = today["close"]

        if position > 0:
            highest_since_entry = max(highest_since_entry, today["high"])

            if today["low"] <= stop_price:
                exit_price = max(0.0, stop_price - slippage)
                turnover_entry = entry_price * qty * size
                turnover_exit = exit_price * qty * size
                commission = (turnover_entry + turnover_exit) * rate
                pnl = (exit_price - entry_price) * qty * size - commission
                realized_pnl += pnl
                capital += pnl

                r_multiple = safe_div(exit_price - entry_price, entry_price - initial_stop, np.nan)

                trade_rows.append(
                    TradeRecord(
                        symbol=symbol,
                        exchange=exchange,
                        vt_symbol=vt_symbol,
                        setup_type=setup_type,
                        entry_date=str(entry_date.date()),
                        exit_date=str(today_dt.date()),
                        entry_price=float(entry_price),
                        exit_price=float(exit_price),
                        quantity=int(qty),
                        turnover_entry=float(turnover_entry),
                        turnover_exit=float(turnover_exit),
                        pnl=float(pnl),
                        return_pct=float((exit_price - entry_price) / entry_price),
                        holding_days=int(holding_days),
                        entry_reason=entry_reason,
                        exit_reason="intraday_stop",
                        initial_stop=float(initial_stop),
                        final_stop=float(stop_price),
                        highest_since_entry=float(highest_since_entry),
                        risk_per_unit=float(risk_per_unit),
                        risk_budget=float(risk_budget),
                        r_multiple=float(r_multiple),
                    )
                )

                mark_price = exit_price

                position = 0
                qty = 0
                setup_type = ""
                entry_price = np.nan
                entry_date = None
                initial_stop = np.nan
                stop_price = np.nan
                highest_since_entry = -np.inf
                holding_days = 0
                entry_reason = ""
                risk_per_unit = 0.0
                risk_budget = 0.0

            else:
                holding_days += 1

                one_r = entry_price - initial_stop
                if highest_since_entry >= entry_price + BREAKEVEN_R * one_r:
                    stop_price = max(stop_price, entry_price)

                atr_trail = today["close"] - TRAIL_STOP_ATR * today["atr"] if not pd.isna(today["atr"]) else stop_price
                swing_trail = today["swing_low_prev"] if not pd.isna(today["swing_low_prev"]) else stop_price

                new_stop = max(stop_price, atr_trail, swing_trail)
                if new_stop < today["close"]:
                    stop_price = new_stop

        unrealized_pnl = 0.0
        if position > 0:
            unrealized_pnl = (mark_price - entry_price) * qty * size

        equity = capital + unrealized_pnl
        month_return = safe_div(equity - current_month_start_equity, current_month_start_equity, 0.0)

        equity_rows.append(
            {
                "datetime": today_dt,
                "symbol": symbol,
                "exchange": exchange,
                "vt_symbol": vt_symbol,
                "close": round_money(today["close"]),
                "position": int(qty),
                "setup_type": setup_type if position > 0 else "",
                "entry_price": round_money(entry_price) if position > 0 else np.nan,
                "stop_price": round_money(stop_price) if position > 0 else np.nan,
                "highest_since_entry": round_money(highest_since_entry) if position > 0 else np.nan,
                "realized_pnl": round_money(realized_pnl),
                "unrealized_pnl": round_money(unrealized_pnl),
                "equity": round_money(equity),
                "month_return": month_return,
                "month_return_pct": pct_str(month_return),
                "month_stop_trading": int(month_stop_trading_map[month_key]),
            }
        )

    if position > 0:
        last = df.iloc[-1]
        exit_dt = pd.Timestamp(last["datetime"])
        exit_price = max(0.0, last["close"] - slippage)
        turnover_entry = entry_price * qty * size
        turnover_exit = exit_price * qty * size
        commission = (turnover_entry + turnover_exit) * rate
        pnl = (exit_price - entry_price) * qty * size - commission
        realized_pnl += pnl
        capital += pnl
        r_multiple = safe_div(exit_price - entry_price, entry_price - initial_stop, np.nan)

        trade_rows.append(
            TradeRecord(
                symbol=symbol,
                exchange=exchange,
                vt_symbol=vt_symbol,
                setup_type=setup_type,
                entry_date=str(entry_date.date()),
                exit_date=str(exit_dt.date()),
                entry_price=float(entry_price),
                exit_price=float(exit_price),
                quantity=int(qty),
                turnover_entry=float(turnover_entry),
                turnover_exit=float(turnover_exit),
                pnl=float(pnl),
                return_pct=float((exit_price - entry_price) / entry_price),
                holding_days=int(holding_days),
                entry_reason=entry_reason,
                exit_reason="force_exit_last_close",
                initial_stop=float(initial_stop),
                final_stop=float(stop_price),
                highest_since_entry=float(highest_since_entry),
                risk_per_unit=float(risk_per_unit),
                risk_budget=float(risk_budget),
                r_multiple=float(r_multiple),
            )
        )

    equity_df = pd.DataFrame(equity_rows)
    trades_df = pd.DataFrame([asdict(x) for x in trade_rows])

    if not equity_df.empty:
        equity_df["daily_return"] = equity_df["equity"].pct_change().fillna(0.0)
        equity_df["daily_return_pct"] = equity_df["daily_return"].apply(pct_str)

    # 汇总摘要
    summary_rows = []
    for tag in ["ALL", "GOOD_SETUP", "NORMAL_SETUP"]:
        if tag == "ALL":
            tr = trades_df.copy()
        else:
            tr = trades_df[trades_df["setup_type"] == tag].copy()

        if equity_df.empty:
            continue

        total_return = safe_div(equity_df["equity"].iloc[-1] - INITIAL_CAPITAL, INITIAL_CAPITAL, 0.0)

        gross_profit = tr.loc[tr["pnl"] > 0, "pnl"].sum() if not tr.empty else 0.0
        gross_loss = tr.loc[tr["pnl"] < 0, "pnl"].sum() if not tr.empty else 0.0
        trade_count = len(tr)
        win_count = int((tr["pnl"] > 0).sum()) if not tr.empty else 0
        win_rate = safe_div(win_count, trade_count, np.nan)

        if gross_loss < 0:
            profit_factor = gross_profit / abs(gross_loss)
        elif gross_profit > 0 and gross_loss == 0:
            profit_factor = np.inf
        else:
            profit_factor = np.nan

        summary_rows.append(
            {
                "group_name": tag,
                "total_return": total_return,
                "total_return_pct": pct_str(total_return),
                "gross_profit": round_money(gross_profit),
                "gross_loss": round_money(gross_loss),
                "net_profit": round_money(gross_profit + gross_loss),
                "trade_count": trade_count,
                "win_count": win_count,
                "win_rate": win_rate,
                "win_rate_pct": pct_str(win_rate),
                "profit_factor": round(profit_factor, 4) if pd.notna(profit_factor) and np.isfinite(profit_factor) else profit_factor,
            }
        )

    summary_df = pd.DataFrame(summary_rows)

    if not trades_df.empty:
        money_cols = [
            "entry_price", "exit_price", "turnover_entry", "turnover_exit",
            "pnl", "initial_stop", "final_stop", "highest_since_entry",
            "risk_per_unit", "risk_budget", "r_multiple"
        ]
        for col in money_cols:
            if col in trades_df.columns:
                trades_df[col] = trades_df[col].apply(round_money)

        trades_df["return_pct_str"] = trades_df["return_pct"].apply(pct_str)

    return equity_df, trades_df, summary_df


# =========================================================
# 月度统计
# =========================================================
def calc_monthly_stats(equity_df: pd.DataFrame, trades_df: pd.DataFrame, group_name: str) -> pd.DataFrame:
    if equity_df.empty:
        return pd.DataFrame()

    eq = equity_df.copy()
    eq["month"] = eq["datetime"].dt.to_period("M").astype(str)

    eq_month = eq.groupby("month", as_index=False).agg(
        equity_first=("equity", "first"),
        equity_last=("equity", "last"),
    )
    eq_month["monthly_return"] = eq_month["equity_last"] / eq_month["equity_first"] - 1.0
    eq_month["monthly_return_pct"] = eq_month["monthly_return"].apply(pct_str)

    def trade_agg(g: pd.DataFrame) -> pd.Series:
        gp = g.loc[g["pnl"] > 0, "pnl"].sum()
        gl = g.loc[g["pnl"] < 0, "pnl"].sum()
        tc = len(g)
        wc = int((g["pnl"] > 0).sum())
        wr = safe_div(wc, tc, np.nan)

        if gl < 0:
            pf = gp / abs(gl)
        elif gp > 0 and gl == 0:
            pf = np.inf
        else:
            pf = np.nan

        return pd.Series(
            {
                "trade_count": tc,
                "win_count": wc,
                "gross_profit": round_money(gp),
                "gross_loss": round_money(gl),
                "profit_factor": round(pf, 4) if pd.notna(pf) and np.isfinite(pf) else pf,
                "win_rate": wr,
                "win_rate_pct": pct_str(wr),
            }
        )

    # ALL
    if trades_df.empty:
        out = eq_month.copy()
        out["trade_count"] = 0
        out["win_count"] = 0
        out["gross_profit"] = 0.0
        out["gross_loss"] = 0.0
        out["profit_factor"] = np.nan
        out["win_rate"] = np.nan
        out["win_rate_pct"] = ""
        out["setup_group"] = "ALL"
        out["group_name"] = group_name
        return out

    all_rows = []

    for tag in ["ALL", "GOOD_SETUP", "NORMAL_SETUP"]:
        if tag == "ALL":
            tr = trades_df.copy()
        else:
            tr = trades_df[trades_df["setup_type"] == tag].copy()

        if tr.empty:
            tmp = eq_month.copy()
            tmp["trade_count"] = 0
            tmp["win_count"] = 0
            tmp["gross_profit"] = 0.0
            tmp["gross_loss"] = 0.0
            tmp["profit_factor"] = np.nan
            tmp["win_rate"] = np.nan
            tmp["win_rate_pct"] = ""
            tmp["setup_group"] = tag
            tmp["group_name"] = group_name
            all_rows.append(tmp)
            continue

        tr = tr.copy()
        tr["exit_date"] = pd.to_datetime(tr["exit_date"])
        tr["month"] = tr["exit_date"].dt.to_period("M").astype(str)
        tr_month = tr.groupby("month").apply(trade_agg).reset_index()

        tmp = eq_month.merge(tr_month, on="month", how="left")
        tmp["trade_count"] = tmp["trade_count"].fillna(0).astype(int)
        tmp["win_count"] = tmp["win_count"].fillna(0).astype(int)
        tmp["gross_profit"] = tmp["gross_profit"].fillna(0.0).apply(round_money)
        tmp["gross_loss"] = tmp["gross_loss"].fillna(0.0).apply(round_money)
        tmp["setup_group"] = tag
        tmp["group_name"] = group_name
        all_rows.append(tmp)

    return pd.concat(all_rows, ignore_index=True)


def build_portfolio_equity(all_equity_df: pd.DataFrame) -> pd.DataFrame:
    if all_equity_df.empty:
        return pd.DataFrame()

    pivot = all_equity_df.pivot_table(
        index="datetime",
        columns="vt_symbol",
        values="equity",
        aggfunc="last"
    ).sort_index()

    for col in pivot.columns:
        pivot[col] = pivot[col].ffill().fillna(INITIAL_CAPITAL)

    pivot["portfolio_equity"] = pivot.sum(axis=1).round(2)
    pivot["portfolio_daily_return"] = pivot["portfolio_equity"].pct_change().fillna(0.0)
    pivot["portfolio_daily_return_pct"] = pivot["portfolio_daily_return"].apply(pct_str)

    return pivot.reset_index()


def select_low_vol_symbols(symbols_df: pd.DataFrame) -> pd.DataFrame:
    rows = []
    for _, row in symbols_df.iterrows():
        symbol = str(row["symbol"]).strip()
        exchange = parse_exchange(row["exchange"])

        try:
            df = load_daily_data(symbol, exchange, WARMUP_START, BACKTEST_END)
            if df.empty:
                print(f"[WARN] {symbol}.{exchange.value} 无数据")
                continue

            sub = df[(df["datetime"] >= pd.Timestamp(BACKTEST_START)) & (df["datetime"] <= pd.Timestamp(BACKTEST_END))]
            if len(sub) < 300:
                print(f"[WARN] {symbol}.{exchange.value} 数据不足")
                continue

            ann_vol = calc_annualized_vol(df, BACKTEST_START, BACKTEST_END)
            if pd.isna(ann_vol):
                continue

            rows.append(
                {
                    "symbol": symbol,
                    "exchange": exchange.value,
                    "vt_symbol": f"{symbol}.{exchange.value}",
                    "annualized_volatility": ann_vol,
                    "annualized_volatility_pct": pct_str(ann_vol),
                    "bars_count": len(sub),
                }
            )
        except Exception as e:
            print(f"[WARN] {symbol}.{row['exchange']} 读取失败: {e}")

    result = pd.DataFrame(rows)
    if result.empty:
        return result

    result = result.sort_values(["annualized_volatility", "bars_count"], ascending=[True, False]).reset_index(drop=True)
    return result.head(TOP_N_LOW_VOL).copy()


# =========================================================
# 主程序
# =========================================================
def main():
    output_dir = ensure_dir(OUTPUT_DIR)
    per_symbol_dir = ensure_dir(output_dir / "per_symbol")

    if not os.path.exists(SYMBOLS_CSV_PATH):
        raise FileNotFoundError(f"未找到文件: {SYMBOLS_CSV_PATH}")

    symbols_df = pd.read_csv(SYMBOLS_CSV_PATH)
    if not {"symbol", "exchange"}.issubset(symbols_df.columns):
        raise ValueError("symbols_list.csv 必须包含 symbol, exchange 两列")

    selected_df = select_low_vol_symbols(symbols_df)
    if selected_df.empty:
        raise RuntimeError("未筛出可用品种")

    selected_df.to_csv(output_dir / "selected_symbols_low_vol.csv", index=False, encoding="utf-8-sig")
    print(f"[INFO] 已输出低波动10个品种: {output_dir / 'selected_symbols_low_vol.csv'}")

    all_equity_list = []
    all_trades_list = []
    all_monthly_list = []
    all_summary_list = []

    for _, row in selected_df.iterrows():
        symbol = row["symbol"]
        exchange_str = row["exchange"]
        exchange = parse_exchange(exchange_str)

        print(f"[INFO] 开始回测: {symbol}.{exchange.value}")
        df = load_daily_data(symbol, exchange, WARMUP_START, BACKTEST_END)
        if df.empty:
            print(f"[WARN] {symbol}.{exchange.value} 无数据，跳过")
            continue

        equity_df, trades_df, summary_df = backtest_one_symbol(df, symbol, exchange.value)
        monthly_df = calc_monthly_stats(equity_df, trades_df, f"{symbol}.{exchange.value}")

        symbol_dir = ensure_dir(per_symbol_dir / f"{symbol}_{exchange.value}")
        equity_df.to_csv(symbol_dir / "daily_equity.csv", index=False, encoding="utf-8-sig")
        trades_df.to_csv(symbol_dir / "trades.csv", index=False, encoding="utf-8-sig")
        summary_df.to_csv(symbol_dir / "summary_stats.csv", index=False, encoding="utf-8-sig")
        monthly_df.to_csv(symbol_dir / "monthly_stats.csv", index=False, encoding="utf-8-sig")

        if not equity_df.empty:
            all_equity_list.append(equity_df)
        if not trades_df.empty:
            all_trades_list.append(trades_df)
        if not monthly_df.empty:
            all_monthly_list.append(monthly_df)
        if not summary_df.empty:
            summary_df["vt_symbol"] = f"{symbol}.{exchange.value}"
            all_summary_list.append(summary_df)

        print(f"[INFO] 完成回测: {symbol}.{exchange.value}")

    if not all_equity_list:
        raise RuntimeError("没有任何回测结果")

    all_equity_df = pd.concat(all_equity_list, ignore_index=True)
    all_trades_df = pd.concat(all_trades_list, ignore_index=True) if all_trades_list else pd.DataFrame()
    all_monthly_df = pd.concat(all_monthly_list, ignore_index=True) if all_monthly_list else pd.DataFrame()
    all_summary_df = pd.concat(all_summary_list, ignore_index=True) if all_summary_list else pd.DataFrame()

    all_equity_df.to_csv(output_dir / "all_symbols_daily_equity.csv", index=False, encoding="utf-8-sig")
    all_trades_df.to_csv(output_dir / "all_symbols_trades.csv", index=False, encoding="utf-8-sig")
    all_monthly_df.to_csv(output_dir / "all_symbols_monthly_stats.csv", index=False, encoding="utf-8-sig")
    all_summary_df.to_csv(output_dir / "all_symbols_summary_stats.csv", index=False, encoding="utf-8-sig")

    portfolio_df = build_portfolio_equity(all_equity_df)
    portfolio_df.to_csv(output_dir / "portfolio_daily_equity.csv", index=False, encoding="utf-8-sig")

    print("\n========== 回测完成 ==========")
    print(f"输出目录: {output_dir.resolve()}")
    print(f"筛选品种数: {len(selected_df)}")
    print(f"总交易数: {len(all_trades_df)}")

    if not portfolio_df.empty:
        total_ret = portfolio_df["portfolio_equity"].iloc[-1] / portfolio_df["portfolio_equity"].iloc[0] - 1.0
        print(f"组合总收益率: {total_ret * 100:.2f}%")

    if not all_summary_df.empty:
        print("\n[总体分层统计]")
        print(all_summary_df.to_string(index=False))


if __name__ == "__main__":
    main()
