"""
数据加载工具：统一加载日线/分钟线数据

日线数据字段: symbol, exchange, interval, datetime, open, high, low, close, volume, open_interest, turnover
分钟数据字段: ts_code, trade_time, open, close, high, low, vol, amount, oi, ...
    -> 统一映射为: datetime, open, high, low, close, volume, open_interest, turnover, symbol, exchange
"""
import re
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

# 已知的盘中频率（与 data/{interval}/ 目录名一一对应）
# 兼容两套命名：
#   新（download_all.py 约定）: minute / minute5 / minute15 / minute30 / minute60
#   旧                        : minute / 5min   / 15min   / 30min   / 60min
INTRADAY_INTERVALS = [
    "minute",
    "minute5", "minute15", "minute30", "minute60",
    "5min", "15min", "30min", "60min",
]


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


def _get_alpha_prefix(name: str) -> str:
    """提取名称开头的字母部分: 'CU0' -> 'CU', 'CU_small' -> 'CU', 'C0.DCE' -> 'C'"""
    m = re.match(r'^([A-Za-z]+)', name)
    return m.group(1).upper() if m else ''


def load_intraday_data(symbol: str, exchange: str, interval: str = "minute") -> pd.DataFrame:
    """
    加载单品种盘中数据（parquet 按日期存储），支持任意频率
    目录结构: data/{interval}/{symbol}.{exchange}/YYYY-MM-DD.parquet
    列名自动映射为统一字段

    interval 取值: minute / 5min / 15min / 30min / 60min 或其它已建好的目录
    目录匹配: 按品种字母前缀扫描所有匹配目录
    例如 symbol='CU0' 会匹配 CU0.SHFE, CU.SHF, CU_small 等
    """
    base_dir = DATA_DIR / interval
    prefix = _get_alpha_prefix(symbol)

    # 扫描所有以该品种字母前缀开头的目录
    folders = []
    if base_dir.exists():
        for d in sorted(base_dir.iterdir()):
            if d.is_dir() and _get_alpha_prefix(d.name) == prefix:
                folders.append(d)

    if not folders:
        raise FileNotFoundError(
            f"{interval} 数据目录不存在，品种前缀: {prefix}，搜索路径: {base_dir}"
        )

    # 从所有匹配目录加载 parquet
    files = []
    for folder in folders:
        files.extend(sorted(folder.glob("*.parquet")))

    if not files:
        raise FileNotFoundError(f"{interval} 数据目录为空: {folders}")
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


def load_minute_data(symbol: str, exchange: str) -> pd.DataFrame:
    """加载分钟线数据（向后兼容，等价于 load_intraday_data(..., interval='minute')）"""
    return load_intraday_data(symbol, exchange, interval="minute")


def list_intraday_symbols(interval: str = "minute") -> list[dict]:
    """
    扫描 data/{interval}/ 目录，返回可用品种列表
    按字母前缀分组去重，同一品种多个目录只返回一条记录
    返回: [{"symbol": "CU0", "exchange": "SHFE", "folder": Path}, ...]
    """
    base_dir = DATA_DIR / interval
    if not base_dir.exists():
        return []

    # 按字母前缀分组
    groups: dict[str, list[Path]] = {}
    for folder in sorted(base_dir.iterdir()):
        if not folder.is_dir():
            continue
        prefix = _get_alpha_prefix(folder.name)
        if not prefix:
            continue
        if prefix not in groups:
            groups[prefix] = []
        groups[prefix].append(folder)

    # 从 symbols_list.csv 查找交易所
    try:
        symbols_df = load_symbols()
        sym_exchange = dict(zip(symbols_df["symbol"], symbols_df["exchange"]))
    except Exception:
        sym_exchange = {}

    result = []
    for prefix, folders in sorted(groups.items()):
        has_data = any(list(f.glob("*.parquet")) for f in folders)
        if not has_data:
            continue
        symbol = prefix + "0"
        exchange = sym_exchange.get(symbol, "")
        if not exchange:
            for f in folders:
                if "." in f.name:
                    exchange = f.name.split(".", 1)[1]
                    break
        result.append({
            "symbol": symbol,
            "exchange": exchange,
            "folder": folders[0],
        })
    return result


def list_minute_symbols() -> list[dict]:
    """扫描 data/minute/ 目录（向后兼容包装）"""
    return list_intraday_symbols(interval="minute")


def list_available_intervals() -> list[str]:
    """扫描 data/ 下所有已建好的频率目录（不含 day）"""
    available = []
    if not DATA_DIR.exists():
        return available
    for d in sorted(DATA_DIR.iterdir()):
        if d.is_dir() and d.name in INTRADAY_INTERVALS:
            available.append(d.name)
    return available


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
