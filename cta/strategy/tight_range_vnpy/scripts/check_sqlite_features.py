from __future__ import annotations

import argparse

from tight_range_vnpy.db import SQLiteStore


def main() -> None:
    parser = argparse.ArgumentParser(description="检查离线特征与信号表")
    parser.add_argument("--db-path", required=True)
    parser.add_argument("--symbol", required=False)
    parser.add_argument("--exchange", required=False)
    args = parser.parse_args()

    store = SQLiteStore(args.db_path)
    if args.symbol and args.exchange:
        df = store.load_symbol_features(args.symbol, args.exchange)
        print(df.tail(20).to_string(index=False))
    else:
        print(store.query("SELECT symbol, exchange, COUNT(*) AS cnt, MIN(trade_date) AS start_date, MAX(trade_date) AS end_date FROM daily_features GROUP BY symbol, exchange ORDER BY symbol, exchange").to_string(index=False))
        print("\nSignals:")
        print(store.query("SELECT * FROM signal_events ORDER BY created_at DESC LIMIT 20").to_string(index=False))


if __name__ == "__main__":
    main()
