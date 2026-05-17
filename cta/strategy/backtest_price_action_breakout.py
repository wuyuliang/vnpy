"""CLI entrypoint for price action breakout backtest."""
from __future__ import annotations

import pandas as pd

from cta.strategy.price_action_breakout_engine import backtest_one_symbol
from cta.strategy.price_action_breakout_indicators import (
    OUTPUT_DIR,
    SYMBOLS_CSV_PATH,
    ensure_dir,
    load_daily_data,
    parse_exchange,
)
from cta.strategy.price_action_breakout_report import build_portfolio_equity, calc_monthly_stats, select_low_vol_symbols


def main() -> None:
    output_dir = ensure_dir(OUTPUT_DIR)
    per_symbol_dir = ensure_dir(output_dir / "per_symbol")

    if not SYMBOLS_CSV_PATH:
        raise FileNotFoundError("SYMBOLS_CSV_PATH is empty")
    symbols_df = pd.read_csv(SYMBOLS_CSV_PATH)
    if not {"symbol", "exchange"}.issubset(symbols_df.columns):
        raise ValueError("symbols_list.csv 必须包含 symbol, exchange 两列")

    selected_df = select_low_vol_symbols(symbols_df)
    if selected_df.empty:
        raise RuntimeError("未筛出可用品种")

    selected_df.to_csv(output_dir / "selected_symbols_low_vol.csv", index=False, encoding="utf-8-sig")

    all_equity_list = []
    all_trades_list = []
    all_monthly_list = []
    all_summary_list = []

    for _, row in selected_df.iterrows():
        symbol = row["symbol"]
        exchange_str = row["exchange"]
        exchange = parse_exchange(exchange_str)
        df = load_daily_data(symbol, exchange, "2014-01-01", "2023-12-31")
        if df.empty:
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


__all__ = ["main", "backtest_one_symbol", "calc_monthly_stats", "build_portfolio_equity", "select_low_vol_symbols"]


if __name__ == "__main__":
    main()

