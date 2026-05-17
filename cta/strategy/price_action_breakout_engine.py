"""Backtest engine for price action breakout strategy."""
from __future__ import annotations

from dataclasses import asdict
from typing import Dict, List, Optional, Tuple

import numpy as np
import pandas as pd

from cta.strategy.price_action_breakout_indicators import (
    ATR_WINDOW,
    BACKTEST_END,
    BACKTEST_START,
    BREAKEVEN_R,
    BREAKOUT_WINDOW,
    EMA_SLOW_WINDOW,
    EMA_WINDOW,
    INITIAL_CAPITAL,
    MONTHLY_STOP_LOSS_PCT,
    TRAIL_STOP_ATR,
    VOL_MA_WINDOW,
    WARMUP_START,
    add_indicators,
    get_contract_meta,
    pct_str,
    round_money,
    safe_div,
)
from cta.strategy.price_action_breakout_rules import TradeRecord, calc_position_size, classify_setup, gen_exit_signal


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

        if month_key not in monthly_start_equity_map:
            monthly_start_equity_map[month_key] = capital if not equity_rows else equity_rows[-1]["equity"]
            month_stop_trading_map[month_key] = False

        current_month_start_equity = monthly_start_equity_map[month_key]
        current_equity_pre = capital if not equity_rows else equity_rows[-1]["equity"]
        current_month_return_pre = safe_div(current_equity_pre - current_month_start_equity, current_month_start_equity, 0.0)
        if current_month_return_pre <= MONTHLY_STOP_LOSS_PCT:
            month_stop_trading_map[month_key] = True

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
    if trades_df.empty:
        trades_df = pd.DataFrame(
            columns=[
                "setup_type",
                "pnl",
                "return_pct",
                "entry_price",
                "exit_price",
                "turnover_entry",
                "turnover_exit",
                "initial_stop",
                "final_stop",
                "highest_since_entry",
                "risk_per_unit",
                "risk_budget",
                "r_multiple",
            ]
        )
    if not equity_df.empty:
        equity_df["daily_return"] = equity_df["equity"].pct_change().fillna(0.0)
        equity_df["daily_return_pct"] = equity_df["daily_return"].apply(pct_str)

    summary_rows = []
    for tag in ["ALL", "GOOD_SETUP", "NORMAL_SETUP"]:
        tr = trades_df.copy() if tag == "ALL" else trades_df[trades_df["setup_type"] == tag].copy()
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
            "entry_price",
            "exit_price",
            "turnover_entry",
            "turnover_exit",
            "pnl",
            "initial_stop",
            "final_stop",
            "highest_since_entry",
            "risk_per_unit",
            "risk_budget",
            "r_multiple",
        ]
        for col in money_cols:
            if col in trades_df.columns:
                trades_df[col] = trades_df[col].apply(round_money)
        trades_df["return_pct_str"] = trades_df["return_pct"].apply(pct_str)

    return equity_df, trades_df, summary_df


__all__ = ["backtest_one_symbol"]
