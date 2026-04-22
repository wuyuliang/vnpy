from __future__ import annotations

import argparse
from pathlib import Path
from typing import Dict

import pandas as pd
from zoneinfo import ZoneInfo

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.database import get_database
from vnpy.trader.object import BarData


def parse_csv(csv_path: Path) -> pd.DataFrame:
    df = pd.read_csv(csv_path)
    df.columns = [str(c).strip().lower() for c in df.columns]
    dt_col = "datetime" if "datetime" in df.columns else "date"
    if dt_col not in df.columns:
        raise ValueError(f"{csv_path} 缺少 datetime/date 列")
    for col in ["open", "high", "low", "close", "volume"]:
        if col not in df.columns:
            raise ValueError(f"{csv_path} 缺少 {col} 列")
    df[dt_col] = pd.to_datetime(df[dt_col])
    df = df.sort_values(dt_col).reset_index(drop=True)
    df = df.rename(columns={dt_col: "datetime"})
    if "symbol" not in df.columns:
        df["symbol"] = csv_path.stem.upper()
    if "exchange" not in df.columns:
        raise ValueError(f"{csv_path} 缺少 exchange 列，建议 csv 中提供，例如 SHFE/DCE/CZCE/INE/GFEX/CFFEX")
    return df


def main() -> None:
    parser = argparse.ArgumentParser(description="把日线 CSV 导入 vn.py 数据库")
    parser.add_argument("--csv-dir", required=True)
    parser.add_argument("--tz", default="Asia/Shanghai")
    args = parser.parse_args()

    db = get_database()
    tz = ZoneInfo(args.tz)
    csv_dir = Path(args.csv_dir)

    total_bars = 0
    total_files = 0
    for csv_file in sorted(csv_dir.glob("*.csv")):
        df = parse_csv(csv_file)
        bars = []
        for row in df.itertuples(index=False):
            exchange = getattr(Exchange, str(row.exchange).upper())
            dt = pd.Timestamp(row.datetime).to_pydatetime().replace(tzinfo=tz)
            bars.append(
                BarData(
                    symbol=str(row.symbol).upper(),
                    exchange=exchange,
                    datetime=dt,
                    interval=Interval.DAILY,
                    volume=float(row.volume),
                    open_price=float(row.open),
                    high_price=float(row.high),
                    low_price=float(row.low),
                    close_price=float(row.close),
                    gateway_name="DB",
                )
            )
        db.save_bar_data(bars)
        total_bars += len(bars)
        total_files += 1
        print(f"[OK] imported {csv_file.name} bars={len(bars)}")

    print(f"[DONE] files={total_files} total_bars={total_bars}")


if __name__ == "__main__":
    main()
