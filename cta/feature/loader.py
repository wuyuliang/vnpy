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

# 规范化的盘中频率名（对外主推）
CANON_INTRADAY_INTERVALS: list[str] = [
    "minute", "minute5", "minute15", "minute30", "minute60",
]

# 旧 -> 新 的别名映射（对外接口同时接受旧名，内部统一到 canonical）
INTERVAL_ALIASES: dict[str, str] = {
    "1min":  "minute",
    "5min":  "minute5",
    "15min": "minute15",
    "30min": "minute30",
    "60min": "minute60",
    # canonical names map to themselves (identity)
    "day": "day", "minute": "minute",
    "minute5": "minute5", "minute15": "minute15",
    "minute30": "minute30", "minute60": "minute60",
}

# 规范 -> 旧名反向，用于目录回退扫描
CANON_TO_LEGACY: dict[str, str] = {
    "minute5":  "5min",
    "minute15": "15min",
    "minute30": "30min",
    "minute60": "60min",
}

# 已知的盘中频率（与 data/{interval}/ 目录名一一对应）
# 兼容两套命名：
#   新（download_all.py 约定）: minute / minute5 / minute15 / minute30 / minute60
#   旧                        : minute / 5min   / 15min   / 30min   / 60min
INTRADAY_INTERVALS = CANON_INTRADAY_INTERVALS + [
    v for v in CANON_TO_LEGACY.values()
]


def normalize_interval(interval: str) -> str:
    """把 '5min'/'15min'/... 规范成 'minute5'/'minute15'/...；未知名原样返回"""
    if interval is None:
        return interval
    return INTERVAL_ALIASES.get(str(interval).strip().lower(), str(interval).strip())


def resolve_interval_dir(interval: str) -> Path:
    """
    返回某 interval 的有效数据目录。
    优先级:
      1. 规范名目录（minute5/minute15/...）若存在且非空
      2. 旧名目录（5min/15min/...）若存在且非空
      3. 否则返回规范名目录（即使不存在，让调用方报错）
    """
    canon = normalize_interval(interval)
    canon_dir = DATA_DIR / canon
    if canon_dir.exists() and any(canon_dir.iterdir()):
        return canon_dir

    legacy = CANON_TO_LEGACY.get(canon)
    if legacy:
        legacy_dir = DATA_DIR / legacy
        if legacy_dir.exists() and any(legacy_dir.iterdir()):
            return legacy_dir
    return canon_dir


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


def _dir_symbol(name: str) -> str:
    """从子目录名提取品种代码：'CU0.SHF' -> 'CU0'，'CU_small' -> 'CU_SMALL'"""
    return name.split(".", 1)[0].upper()


def load_intraday_data(symbol: str, exchange: str, interval: str = "minute") -> pd.DataFrame:
    """
    加载单品种盘中数据（parquet 按日期存储），支持任意频率
    目录结构: data/{interval}/{symbol}.{exchange}/YYYY-MM-DD.parquet
    列名自动映射为统一字段

    interval 取值: minute / minute5 / minute15 / minute30 / minute60
                  也接受老别名 5min / 15min / 30min / 60min

    目录匹配策略（严格，不会跨品种）：
      1) 优先精确匹配 {symbol}.{exchange}（若 exchange 提供，还会尝试 SHF/CZC/ZCE 等常见缩写）
      2) 退回匹配 `split('.')[0] == symbol` 的目录（同一合约多个交易所缩写）
      3) 纯符号目录（无 '.'）只在目录名 == symbol 时使用
    示例：symbol='CU0' 只匹配 'CU0.SHF' / 'CU0.SHFE'，不会吸收 'CU_small'
    """
    base_dir = resolve_interval_dir(interval)
    sym_upper = symbol.upper()

    # 交易所缩写扩展（与 download 端保持一致）
    exch_aliases: dict[str, list[str]] = {
        "SHFE": ["SHF", "SHFE"],
        "INE":  ["INE"],
        "DCE":  ["DCE"],
        "CZCE": ["CZC", "ZCE", "CZCE"],
        "CFFEX": ["CFX", "CFFEX"],
        "GFEX": ["GFE", "GFEX"],
    }
    exch_key = (exchange or "").upper()
    wanted_exch = exch_aliases.get(exch_key, [exch_key] if exch_key else [])

    def _match(d: Path, target_sym: str) -> bool:
        d_sym = _dir_symbol(d.name)
        if d_sym != target_sym:
            return False
        if wanted_exch and "." in d.name:
            suffix = d.name.split(".", 1)[1].upper()
            if suffix not in wanted_exch:
                return False
        return True

    folders: list[Path] = []
    if base_dir.exists():
        # 1) 精确匹配 symbol（例 'CU0' 只接受 'CU0' 或 'CU0.SHF'）
        for d in sorted(base_dir.iterdir()):
            if d.is_dir() and _match(d, sym_upper):
                folders.append(d)
        # 2) 回退：部分频率目录用"symbol 不带末尾 0"的写法（例 minute5/RB 对应 RB0）
        #    仅当主匹配未命中时才尝试；并进一步要求 CU_SMALL 这类后缀名不被纳入
        if not folders:
            stem = sym_upper.rstrip("0")
            if stem and stem != sym_upper:
                for d in sorted(base_dir.iterdir()):
                    if d.is_dir() and _match(d, stem):
                        folders.append(d)

    if not folders:
        raise FileNotFoundError(
            f"{interval} 数据目录不存在: 未找到 symbol={symbol} exchange={exchange} "
            f"的精确目录，搜索路径: {base_dir}"
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
    base_dir = resolve_interval_dir(interval)
    if not base_dir.exists():
        return []

    # 严格按目录名的品种部分（split('.')[0]）分组，避免跨品种聚合
    # 例：'CU0.SHF' 与 'CU_small' 属于不同 symbol，不合并
    groups: dict[str, list[Path]] = {}
    for folder in sorted(base_dir.iterdir()):
        if not folder.is_dir():
            continue
        sym = _dir_symbol(folder.name)
        if not sym:
            continue
        groups.setdefault(sym, []).append(folder)

    # 从 symbols_list.csv 查找交易所
    try:
        symbols_df = load_symbols()
        sym_exchange = {
            str(s).upper(): str(e).upper()
            for s, e in zip(symbols_df["symbol"], symbols_df["exchange"])
        }
    except Exception:
        sym_exchange = {}

    result = []
    for symbol, folders in sorted(groups.items()):
        has_data = any(list(f.glob("*.parquet")) for f in folders)
        if not has_data:
            continue
        exchange = sym_exchange.get(symbol, "")
        if not exchange:
            for f in folders:
                if "." in f.name:
                    exchange = f.name.split(".", 1)[1].upper()
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
    """
    扫描 data/ 下所有已建好的频率目录（不含 day），返回规范名（minute/minute5/...）。
    同时识别旧名 5min/15min/... 会被归并到对应规范名。
    """
    if not DATA_DIR.exists():
        return []
    found: set[str] = set()
    for d in sorted(DATA_DIR.iterdir()):
        if not d.is_dir() or not any(d.iterdir()):
            continue
        if d.name in INTRADAY_INTERVALS:
            found.add(normalize_interval(d.name))
    # 按规范顺序输出
    return [i for i in CANON_INTRADAY_INTERVALS if i in found]


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
