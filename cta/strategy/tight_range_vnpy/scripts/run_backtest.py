from __future__ import annotations

import argparse
from datetime import datetime
from pathlib import Path

from vnpy.trader.constant import Exchange, Interval
from vnpy_ctastrategy.backtesting import BacktestingEngine

from tight_range_vnpy.strategy import TightRangeBreakoutStrategy


def main() -> None:
    parser = argparse.ArgumentParser(description="运行 TightRangeBreakoutStrategy 的 vn.py 回测")
    parser.add_argument("--feature-db", required=True, help="离线特征 SQLite 路径")
    parser.add_argument("--vt-symbol", required=True, help="例如 rb99.SHFE")
    parser.add_argument("--start", required=True, help="开始日期，如 2019-01-01")
    parser.add_argument("--end", required=True, help="结束日期，如 2024-12-31")
    parser.add_argument("--rate", type=float, default=0.0002)
    parser.add_argument("--slippage", type=float, default=1.0)
    parser.add_argument("--size", type=float, default=10)
    parser.add_argument("--pricetick", type=float, default=1.0)
    parser.add_argument("--capital", type=float, default=1_000_000)
    args = parser.parse_args()

    symbol, exchange_str = args.vt_symbol.split(".")
    exchange = getattr(Exchange, exchange_str)

    engine = BacktestingEngine()
    engine.set_parameters(
        vt_symbol=args.vt_symbol,
        interval=Interval.DAILY,
        start=datetime.fromisoformat(args.start),
        end=datetime.fromisoformat(args.end),
        rate=args.rate,
        slippage=args.slippage,
        size=args.size,
        pricetick=args.pricetick,
        capital=args.capital,
    )

    engine.add_strategy(
        TightRangeBreakoutStrategy,
        {
            "feature_db_path": args.feature_db,
            "entry_score": 68,
            "min_expected_rr": 1.2,
            "use_fixed_size": 1,
            "fixed_size": 1,
            "trailing_atr_multiplier": 2.0,
            "stop_atr_multiplier": 1.0,
        },
    )

    engine.load_data()
    engine.run_backtesting()
    df = engine.calculate_result()
    engine.calculate_statistics()
    if df is not None:
        out_path = Path(args.feature_db).with_name(f"backtest_{symbol}_{exchange_str}.csv")
        df.to_csv(out_path)
        print(f"[OK] backtest result saved: {out_path}")
    engine.show_chart()


if __name__ == "__main__":
    main()
