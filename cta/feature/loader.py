"""
数据加载工具：统一加载日线/分钟线数据

日线数据字段: symbol, exchange, interval, datetime, open, high, low, close, volume, open_interest, turnover
分钟数据字段: ts_code, trade_time, open, close, high, low, vol, amount, oi, ...
    -> 统一映射为: datetime, open, high, low, close, volume, open_interest, turnover, symbol, exchange
"""
from pathlib import Path

import pandas as pd

# 项目根路径
CTA_ROOT = Path(__file__).resolve().parent.parent
DATA_DIR = CTA_ROOT / "data"
SYMBOLS_CSV = DATA_DIR / "day" / "symbols_list.csv"

# 分钟数据列名映射 (原始 -> 统一)
MINUTE_COL_MAP = {
    "trade_time": "datetime",
    "vol": "volume",
    "oi": "open_interest",
    "amount": "turnover",
}


def load_symbols() -> pd.DataFrame:
    """加载品种列表，返回 DataFrame(symbol, exchange, name)"""
    return pd.read_csv(SYMBOLS_CSV, encoding="utf-8-sig")


def load_day_data(symbol: str) -> pd.DataFrame:
    """加载单品种日线数据"""
    path = DATA_DIR / "day" / f"{symbol}.csv"
    if not path.exists():
        raise FileNotFoundError(f"日线数据不存在: {path}")
    df = pd.read_csv(path, encoding="utf-8-sig", parse_dates=["datetime"])
    df.sort_values("datetime", inplace=True)
    df.reset_index(drop=True, inplace=True)
    return df


def load_minute_data(symbol: str, exchange: str) -> pd.DataFrame:
    """
    加载单品种分钟线数据（parquet 按日期存储）
    目录结构: data/minute/{symbol}.{exchange}/YYYY-MM-DD.parquet
    列名自动映射为统一字段
    """
    # 尝试多种目录命名: CU0.SHF, CU0.SHFE
    candidates = [
        DATA_DIR / "minute" / f"{symbol}.{exchange}",
        DATA_DIR / "minute" / f"{symbol}.{exchange[:3]}",
    ]
    folder = None
    for c in candidates:
        if c.exists():
            folder = c
            break
    if folder is None:
        raise FileNotFoundError(
            f"分钟数据目录不存在，已尝试: {[str(c) for c in candidates]}"
        )

    files = sorted(folder.glob("*.parquet"))
    if not files:
        raise FileNotFoundError(f"分钟数据目录为空: {folder}")
    dfs = [pd.read_parquet(f) for f in files]
    df = pd.concat(dfs, ignore_index=True)

    # 列名映射
    df.rename(columns=MINUTE_COL_MAP, inplace=True)

    # 补充 symbol / exchange 列（如果不存在）
    if "symbol" not in df.columns:
        df["symbol"] = symbol
    if "exchange" not in df.columns:
        df["exchange"] = exchange

    # 确保 datetime 类型
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        df.sort_values("datetime", inplace=True)
        df.reset_index(drop=True, inplace=True)

    # 确保 open_interest 列存在
    if "open_interest" not in df.columns:
        df["open_interest"] = 0

    return df


def list_minute_symbols() -> list[dict]:
    """
    扫描 data/minute/ 目录，返回可用品种列表
    返回: [{"symbol": "CU0", "exchange": "SHF", "folder": Path}, ...]
    """
    minute_dir = DATA_DIR / "minute"
    if not minute_dir.exists():
        return []
    result = []
    for folder in sorted(minute_dir.iterdir()):
        if folder.is_dir() and "." in folder.name:
            parts = folder.name.split(".", 1)
            parquet_files = list(folder.glob("*.parquet"))
            if parquet_files:
                result.append({
                    "symbol": parts[0],
                    "exchange": parts[1],
                    "folder": folder,
                })
    return result


def load_all_day_data() -> pd.DataFrame:
    """加载所有品种日线数据，合并为一个 DataFrame"""
    symbols_df = load_symbols()
    dfs = []
    for _, row in symbols_df.iterrows():
        try:
            df = load_day_data(row["symbol"])
            dfs.append(df)
        except FileNotFoundError:
            continue
    if not dfs:
        raise RuntimeError("没有加载到任何日线数据")
    return pd.concat(dfs, ignore_index=True)
