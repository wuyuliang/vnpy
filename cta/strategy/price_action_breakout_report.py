"""Reporting helpers for price action breakout strategy."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.strategy.price_action_breakout_indicators import (
    BACKTEST_END,
    BACKTEST_START,
    INITIAL_CAPITAL,
    TOP_N_LOW_VOL,
    WARMUP_START,
    calc_annualized_vol,
    load_daily_data,
    parse_exchange,
    pct_str,
    round_money,
    safe_div,
)


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
        tr = trades_df.copy() if tag == "ALL" else trades_df[trades_df["setup_type"] == tag].copy()
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
        aggfunc="last",
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
                continue
            sub = df[(df["datetime"] >= pd.Timestamp(BACKTEST_START)) & (df["datetime"] <= pd.Timestamp(BACKTEST_END))]
            if len(sub) < 300:
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
        except Exception:
            continue

    result = pd.DataFrame(rows)
    if result.empty:
        return result
    result = result.sort_values(["annualized_volatility", "bars_count"], ascending=[True, False]).reset_index(drop=True)
    return result.head(TOP_N_LOW_VOL).copy()


__all__ = ["calc_monthly_stats", "build_portfolio_equity", "select_low_vol_symbols"]

