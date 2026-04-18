"""
特征读取工具：从按日分片的 parquet 回读已计算好的特征

对应落盘布局（由 cta.feature.run_all_features 产生）:
    cta/data/feature/{canon_interval}/{SYMBOL}/{YYYY-MM-DD}.parquet
    cta/data/feature/{canon_interval}/_all_symbols.parquet  (截面合并)

canon_interval: day / minute / minute5 / minute15 / minute30 / minute60

用法
----
>>> from cta.feature.feature_loader import load_symbol_features
>>> # 读 CU0 整段日线特征
>>> df = load_symbol_features("CU0", interval="day")
>>> # 读某段时间
>>> df = load_symbol_features("CU0", interval="minute5",
...                           start_date="2024-01-01", end_date="2024-03-31")
>>> # 列出该频率下全部可用品种
>>> from cta.feature.feature_loader import list_feature_symbols
>>> list_feature_symbols("day")
>>> # 列出某品种已有的日期
>>> from cta.feature.feature_loader import list_symbol_dates
>>> list_symbol_dates("CU0", "day")

>>> # 按精确时间点取最新已收盘 bar 的特征（默认回退 200ms，避免穿越）
>>> from cta.feature.feature_loader import load_symbol_feature_at
>>> feat = load_symbol_feature_at("RB0", "2025-02-19 14:30:00",
...                               interval="minute")
>>> feat["close"], feat["rsi_14"]
"""
from __future__ import annotations

from datetime import datetime
from pathlib import Path
from typing import List, Optional, Union

import pandas as pd

from cta.feature.loader import normalize_interval

CTA_ROOT = Path(__file__).resolve().parent.parent
FEATURE_DIR = CTA_ROOT / "data" / "feature"

# 类型别名：支持 str / datetime / pd.Timestamp 作为时间戳输入
TimestampLike = Union[str, datetime, pd.Timestamp]


def _symbol_dir(symbol: str, interval: str) -> Path:
    canon = normalize_interval(interval)
    return FEATURE_DIR / canon / symbol


def _raise_missing_symbol_dir(symbol: str, interval: str, sd: Path) -> None:
    """构造信息量更大的报错：告诉用户该品种在其它频率是否有数据，以及如何生成。"""
    canon = normalize_interval(interval)
    # 该品种在其它频率下有数据吗？
    available_in: List[str] = []
    interval_root = FEATURE_DIR
    if interval_root.exists():
        for d in interval_root.iterdir():
            if not d.is_dir():
                continue
            candidate = d / symbol
            if candidate.exists() and any(candidate.glob("*.parquet")):
                available_in.append(d.name)
    # 当前频率下都有哪些品种？
    existing = []
    canon_root = FEATURE_DIR / canon
    if canon_root.exists():
        existing = sorted(
            p.name for p in canon_root.iterdir()
            if p.is_dir() and any(p.glob("*.parquet"))
        )
    msg = [f"特征目录不存在: {sd}"]
    msg.append(f"  该品种 {symbol!r} 在下列频率已有特征: "
               f"{available_in or '(无)'}")
    msg.append(f"  当前频率 {canon!r} 已有特征的品种: "
               f"{existing[:10]}{'...' if len(existing) > 10 else ''} "
               f"(共 {len(existing)})")
    msg.append("")
    msg.append("  生成该特征：")
    msg.append(f"    python3 -m cta.feature.run_all_features "
               f"--interval {canon} --symbols {symbol}")
    raise FileNotFoundError("\n".join(msg))


def list_feature_intervals() -> List[str]:
    """已落盘特征的 interval 列表（规范名）"""
    if not FEATURE_DIR.exists():
        return []
    out: List[str] = []
    for d in sorted(FEATURE_DIR.iterdir()):
        if d.is_dir() and d.name in {
            "day", "minute", "minute5", "minute15", "minute30", "minute60",
        }:
            out.append(d.name)
    return out


def list_feature_symbols(interval: str) -> List[str]:
    """在某 interval 下列出所有已有按日特征的品种"""
    canon = normalize_interval(interval)
    root = FEATURE_DIR / canon
    if not root.exists():
        return []
    return sorted(
        d.name for d in root.iterdir()
        if d.is_dir() and not d.name.startswith("_")
        and any(d.glob("*.parquet"))
    )


def list_symbol_dates(symbol: str, interval: str) -> List[str]:
    """某品种在该 interval 下已有哪些日期的 parquet (YYYY-MM-DD, 升序)"""
    sd = _symbol_dir(symbol, interval)
    if not sd.exists():
        return []
    return sorted(f.stem for f in sd.glob("*.parquet"))


def load_symbol_features(
    symbol: str,
    interval: str = "day",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
    columns: Optional[List[str]] = None,
) -> pd.DataFrame:
    """
    读回某品种某 interval 的特征（按日 parquet 拼接）

    Parameters
    ----------
    symbol : 品种代码（例如 'CU0'）
    interval : 频率，接受规范名或旧名
    start_date, end_date : 可选，闭区间，格式 YYYY-MM-DD
    columns : 可选，只读取指定列（必须包含 datetime 才能排序）

    Returns
    -------
    DataFrame，按 datetime 升序
    """
    sd = _symbol_dir(symbol, interval)
    if not sd.exists():
        _raise_missing_symbol_dir(symbol, interval, sd)

    files = []
    for f in sorted(sd.glob("*.parquet")):
        date = f.stem
        if start_date and date < start_date:
            continue
        if end_date and date > end_date:
            continue
        files.append(f)

    if not files:
        raise FileNotFoundError(
            f"在 {sd} 下找不到匹配的按日 parquet "
            f"(range=[{start_date}, {end_date}])"
        )

    dfs = [pd.read_parquet(f, columns=columns) for f in files]
    df = pd.concat(dfs, ignore_index=True)
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        df.sort_values("datetime", inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df


def load_symbol_feature_at(
    symbol: str,
    timestamp: TimestampLike,
    interval: str = "minute",
    lookback: Union[str, pd.Timedelta] = "200ms",
    columns: Optional[List[str]] = None,
    max_days_back: int = 7,
) -> Optional[pd.Series]:
    """
    取某品种在 `timestamp` 时刻可用的**最新一根已收盘 bar 的特征**（避免穿越）

    语义
    ----
    effective_ts = pd.Timestamp(timestamp) - pd.Timedelta(lookback)
    返回 datetime <= effective_ts 的最新一行特征。

    `lookback` 默认 200ms，是为了：
      - 一根 bar 在 datetime=T 处记录（end-stamp 约定）说明它覆盖到 T 时刻
      - 在 T 时刻查询时，如果直接用 datetime <= T，会纳入刚收盘（或未收盘）的那根
      - 退 200ms 后 effective_ts < T，保证拿到的是 T 之前**完全确定**的那根 bar
        → 避免任何前视偏差

    Parameters
    ----------
    symbol : 品种代码，如 'CU0'
    timestamp : 查询时刻（str / datetime / pd.Timestamp）
    interval : 频率（day / minute / minute5 / ... 接受旧名）
    lookback : 安全回撤（pandas Timedelta 能解析的字符串或对象）
               默认 '200ms'；要更保守可传 '1s'、'5s'、'1min'
    columns : 只读取指定列（必须含 'datetime' 以保证能过滤）
    max_days_back : 若 `effective_ts` 当日无数据，向前回溯的天数上限
                    （跨交易日边界场景，例如 effective_ts 落在盘前）

    Returns
    -------
    pd.Series 或 None
        返回一行（单个 bar 的所有列）；若回溯 `max_days_back` 天仍无数据则返回 None

    Examples
    --------
    >>> from cta.feature.feature_loader import load_symbol_feature_at
    >>> feat = load_symbol_feature_at("RB0", "2025-02-19 14:30:00",
    ...                               interval="minute")
    >>> feat["close"], feat["rsi_14"]
    (3345.0, 62.4)

    >>> # 日线：建议 lookback 足够大（例如 '1D'）保证拿到前一交易日
    >>> feat = load_symbol_feature_at("CU0", "2024-02-19",
    ...                               interval="day", lookback="1D")
    """
    sd = _symbol_dir(symbol, interval)
    if not sd.exists():
        _raise_missing_symbol_dir(symbol, interval, sd)

    ts = pd.Timestamp(timestamp)
    lb = pd.Timedelta(lookback) if not isinstance(lookback, pd.Timedelta) else lookback
    if lb < pd.Timedelta(0):
        raise ValueError(f"lookback 不能为负: {lookback}")
    effective_ts = ts - lb

    # 必须读 'datetime' 才能过滤；若调用方只要子集，我们补上 datetime 读、返回时再裁
    read_cols = None
    if columns is not None:
        if "datetime" not in columns:
            read_cols = ["datetime"] + list(columns)
        else:
            read_cols = list(columns)

    # 候选日期：所有 <= effective_ts 日期的按日 parquet，按日期倒序回溯
    target_date = effective_ts.strftime("%Y-%m-%d")
    all_dates = sorted(f.stem for f in sd.glob("*.parquet"))
    candidates = [d for d in all_dates if d <= target_date]
    if not candidates:
        return None

    # 从最新向前扫，最多回溯 max_days_back 天
    for date in reversed(candidates[-max_days_back:]):
        f = sd / f"{date}.parquet"
        try:
            df = pd.read_parquet(f, columns=read_cols)
        except Exception:
            continue
        if df.empty or "datetime" not in df.columns:
            continue
        dt_col = pd.to_datetime(df["datetime"])
        mask = dt_col <= effective_ts
        if not mask.any():
            continue
        # 取 <= effective_ts 中 datetime 最大的一行
        idx = dt_col[mask].idxmax()
        row = df.loc[idx].copy()
        # 回填 datetime 列为 Timestamp 以便使用方直接用
        row["datetime"] = dt_col.loc[idx]
        # 若调用方要求 columns 且不含 datetime，返回时移除 datetime
        if columns is not None and "datetime" not in columns:
            row = row.drop(labels=["datetime"])
        return row
    return None


def load_cross_section(
    interval: str = "day",
    start_date: Optional[str] = None,
    end_date: Optional[str] = None,
) -> pd.DataFrame:
    """
    读取截面特征合并文件 _all_symbols.parquet。
    注意：该文件由 `--cross-section` 生成，内容为多品种拼接 + 截面特征。
    """
    canon = normalize_interval(interval)
    path = FEATURE_DIR / canon / "_all_symbols.parquet"
    if not path.exists():
        raise FileNotFoundError(
            f"截面特征文件不存在: {path}；请先运行 "
            f"`python3 -m cta.feature.run_all_features --cross-section "
            f"--interval {canon}`"
        )
    df = pd.read_parquet(path)
    if "datetime" in df.columns:
        df["datetime"] = pd.to_datetime(df["datetime"])
        if start_date or end_date:
            # 用 date-string 比较，避免 end_date=YYYY-MM-DD 因 midnight 比较漏掉当天
            dstr = df["datetime"].dt.strftime("%Y-%m-%d")
            if start_date:
                df = df[dstr >= start_date]
                dstr = dstr[dstr >= start_date]
            if end_date:
                df = df[dstr <= end_date]
        sort_cols = ["symbol", "datetime"] if "symbol" in df.columns else ["datetime"]
        df.sort_values(sort_cols, inplace=True)
        df.reset_index(drop=True, inplace=True)
    return df


if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(description="按日特征读取工具")
    parser.add_argument("--interval", default="day")
    parser.add_argument("--symbol", default=None)
    parser.add_argument("--start-date", default=None)
    parser.add_argument("--end-date", default=None)
    parser.add_argument("--at", default=None,
                        help="精确时间点查询（YYYY-MM-DD HH:MM:SS）—— 取该时刻之前最新一根 bar")
    parser.add_argument("--lookback", default="200ms",
                        help="--at 模式下的回撤量（默认 200ms）")
    parser.add_argument("--list-symbols", action="store_true")
    parser.add_argument("--list-dates", action="store_true")
    args = parser.parse_args()

    if args.list_symbols:
        syms = list_feature_symbols(args.interval)
        print(f"[{args.interval}] {len(syms)} symbols:")
        for s in syms:
            print(f"  {s}")
    elif args.list_dates and args.symbol:
        dates = list_symbol_dates(args.symbol, args.interval)
        print(f"[{args.interval}/{args.symbol}] {len(dates)} days:")
        for d in dates[:10]:
            print(f"  {d}")
        if len(dates) > 10:
            print(f"  ... ({len(dates)-10} more)")
    elif args.at and args.symbol:
        row = load_symbol_feature_at(
            args.symbol, args.at, interval=args.interval,
            lookback=args.lookback,
        )
        if row is None:
            print(f"[{args.interval}/{args.symbol}] 在 {args.at} "
                  f"(回撤 {args.lookback}) 之前找不到可用特征")
        else:
            print(f"[{args.interval}/{args.symbol}] @ {row['datetime']} "
                  f"(query={args.at}, lookback={args.lookback})")
            # 节选展示几列，避免输出过长
            show = [c for c in ("close","volume","rsi_14","atr_14","macd_hist")
                    if c in row.index]
            for c in show:
                print(f"  {c:12s} = {row[c]}")
            print(f"  (共 {len(row)} 列)")
    elif args.symbol:
        df = load_symbol_features(
            args.symbol, args.interval,
            start_date=args.start_date, end_date=args.end_date,
        )
        print(df.head())
        print(f"\n{len(df)} rows, {len(df.columns)} cols")
    else:
        parser.error("--symbol or --list-symbols required")
