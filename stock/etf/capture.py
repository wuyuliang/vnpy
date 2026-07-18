from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from .config import StrategyConfig
from .portfolio import calculate_order_quantity

CAPTURE_COLUMNS = [
    "symbol",
    "start_signal_date",
    "start_execution_date",
    "end_signal_date",
    "end_execution_date",
    "is_complete",
    "start_price",
    "end_price",
    "episode_return",
    "start_equity",
    "start_atr5",
    "hypothetical_quantity",
    "hypothetical_episode_profit",
    "actual_start_quantity",
    "actual_end_quantity",
    "actual_episode_pnl",
    "capture_ratio",
    "qualifies",
]


def _commission(notional: float, config: StrategyConfig) -> float:
    if notional <= 0:
        return 0.0
    return max(notional * config.commission_rate, config.min_commission)


def _equity_at_signal(
    equity_curve: pd.DataFrame,
    signal_date: pd.Timestamp,
    config: StrategyConfig,
) -> float:
    if equity_curve.empty or not {"datetime", "equity"}.issubset(equity_curve):
        return config.initial_capital
    available = equity_curve.loc[equity_curve["datetime"] <= signal_date]
    if available.empty:
        return config.initial_capital
    return float(available.iloc[-1]["equity"])


def _actual_episode_pnl(
    trades: pd.DataFrame,
    symbol: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
    start_price: float,
    end_price: float,
) -> tuple[float, int, int]:
    if trades.empty or "symbol" not in trades:
        return 0.0, 0, 0
    symbol_trades = trades.loc[trades["symbol"].astype(str) == symbol].copy()
    if symbol_trades.empty:
        return 0.0, 0, 0
    before = symbol_trades.loc[symbol_trades["datetime"] < start_date]
    interval = symbol_trades.loc[
        (symbol_trades["datetime"] >= start_date)
        & (symbol_trades["datetime"] <= end_date)
    ]

    def signed_quantity(frame: pd.DataFrame) -> int:
        if frame.empty:
            return 0
        signs = frame["side"].map({"buy": 1, "sell": -1}).fillna(0)
        return int((frame["quantity"] * signs).sum())

    start_quantity = signed_quantity(before)
    end_quantity = start_quantity + signed_quantity(interval)
    buy_rows = interval.loc[interval["side"] == "buy"]
    sell_rows = interval.loc[interval["side"] == "sell"]
    buy_cash = float(
        (buy_rows["quantity"] * buy_rows["fill_price"]).sum()
        + buy_rows.get("commission", pd.Series(0.0, index=buy_rows.index)).sum()
    )
    sell_cash = float(
        (sell_rows["quantity"] * sell_rows["fill_price"]).sum()
        - sell_rows.get("commission", pd.Series(0.0, index=sell_rows.index)).sum()
    )
    pnl = end_quantity * end_price - start_quantity * start_price + sell_cash - buy_cash
    return pnl, start_quantity, end_quantity


def calculate_trend_capture(
    *,
    candidates: pd.DataFrame,
    trades: pd.DataFrame,
    positions: pd.DataFrame,
    equity_curve: pd.DataFrame,
    config: StrategyConfig,
) -> pd.DataFrame:
    """Calculate executable bull-trend episode capture from the trade ledger."""
    del positions  # The transaction ledger determines boundary inventory exactly.
    if candidates.empty:
        return pd.DataFrame(columns=CAPTURE_COLUMNS)
    frame = candidates.copy()
    date_column = "datetime" if "datetime" in frame else "signal_date"
    frame[date_column] = pd.to_datetime(frame[date_column])
    equity = equity_curve.copy()
    if "datetime" in equity:
        equity["datetime"] = pd.to_datetime(equity["datetime"])
        equity = equity.sort_values("datetime")
    ledger = trades.copy()
    if "datetime" in ledger:
        ledger["datetime"] = pd.to_datetime(ledger["datetime"])

    records: list[dict[str, Any]] = []
    for symbol, symbol_frame in frame.groupby("symbol", sort=True):
        rows = symbol_frame.sort_values(date_column).reset_index(drop=True)
        cursor = 0
        while cursor < len(rows):
            entry_rank = pd.to_numeric(rows["entry_rank"], errors="coerce")
            trend_confirmed = rows["trend_confirmed"].fillna(False).astype(bool)
            start_matches = rows.index[
                (rows.index >= cursor)
                & (entry_rank <= config.entry_rank)
                & trend_confirmed
            ]
            if len(start_matches) == 0:
                break
            start_index = int(start_matches[0])
            if start_index + 1 >= len(rows):
                break
            start_row = rows.iloc[start_index]
            start_execution_row = rows.iloc[start_index + 1]
            start_signal_date = pd.Timestamp(start_row[date_column])
            start_execution_date = pd.Timestamp(start_execution_row[date_column])
            start_price = float(start_execution_row["open"])
            start_atr5 = float(start_row["atr5"])

            later = rows.iloc[start_index + 1 :]
            end_matches = later.index[
                pd.to_numeric(later["close"], errors="coerce")
                < pd.to_numeric(later["ema20"], errors="coerce")
            ]
            has_executable_end = bool(
                len(end_matches) and int(end_matches[0]) + 1 < len(rows)
            )
            if has_executable_end:
                end_index = int(end_matches[0])
                end_signal_date: pd.Timestamp | pd.NaT = pd.Timestamp(
                    rows.iloc[end_index][date_column]
                )
                end_execution_row = rows.iloc[end_index + 1]
                end_execution_date = pd.Timestamp(end_execution_row[date_column])
                end_price = float(end_execution_row["open"])
                is_complete = True
            else:
                end_index = len(rows) - 1
                end_signal_date = (
                    pd.Timestamp(rows.iloc[int(end_matches[0])][date_column])
                    if len(end_matches)
                    else pd.NaT
                )
                end_execution_date = pd.Timestamp(rows.iloc[-1][date_column])
                end_price = float(rows.iloc[-1]["close"])
                is_complete = False

            start_equity = _equity_at_signal(equity, start_signal_date, config)
            estimated_entry_fill = start_price * (1 + config.slippage_rate)
            hypothetical_quantity = calculate_order_quantity(
                start_equity,
                start_equity,
                estimated_entry_fill,
                start_atr5,
                config,
            )
            hypothetical_exit_fill = end_price * (1 - config.slippage_rate)
            entry_notional = hypothetical_quantity * estimated_entry_fill
            exit_notional = hypothetical_quantity * hypothetical_exit_fill
            hypothetical_profit = (
                exit_notional
                - entry_notional
                - _commission(entry_notional, config)
                - _commission(exit_notional, config)
            )
            actual_pnl, start_quantity, end_quantity = _actual_episode_pnl(
                ledger,
                str(symbol),
                start_execution_date,
                end_execution_date,
                start_price,
                end_price,
            )
            episode_return = end_price / start_price - 1
            capture_ratio = (
                actual_pnl / hypothetical_profit
                if hypothetical_profit > 0
                else float("nan")
            )
            qualifies = bool(
                is_complete and episode_return > 0.20 and hypothetical_profit > 0
            )
            records.append(
                {
                    "symbol": str(symbol),
                    "start_signal_date": start_signal_date,
                    "start_execution_date": start_execution_date,
                    "end_signal_date": end_signal_date,
                    "end_execution_date": end_execution_date,
                    "is_complete": is_complete,
                    "start_price": start_price,
                    "end_price": end_price,
                    "episode_return": episode_return,
                    "start_equity": start_equity,
                    "start_atr5": start_atr5,
                    "hypothetical_quantity": hypothetical_quantity,
                    "hypothetical_episode_profit": hypothetical_profit,
                    "actual_start_quantity": start_quantity,
                    "actual_end_quantity": end_quantity,
                    "actual_episode_pnl": actual_pnl,
                    "capture_ratio": capture_ratio,
                    "qualifies": qualifies,
                }
            )
            if not is_complete:
                break
            cursor = end_index + 1

    result = pd.DataFrame(records, columns=CAPTURE_COLUMNS)
    if not result.empty:
        result["capture_ratio"] = pd.to_numeric(
            result["capture_ratio"], errors="coerce"
        ).replace([np.inf, -np.inf], np.nan)
    return result
