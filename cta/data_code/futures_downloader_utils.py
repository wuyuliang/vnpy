"""Data normalization helpers for futures downloader."""
from __future__ import annotations

from typing import Dict, List

import pandas as pd


def normalize_daily_df(raw_df: pd.DataFrame, symbol: str, exchange: str) -> pd.DataFrame:
    """Convert akshare futures daily data to the unified CTA schema."""
    cols = [
        "symbol",
        "exchange",
        "interval",
        "datetime",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "open_interest",
        "turnover",
    ]
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=cols)

    df = raw_df.rename(
        columns={
            "日期": "datetime",
            "开盘价": "open",
            "最高价": "high",
            "最低价": "low",
            "收盘价": "close",
            "成交量": "volume",
            "持仓量": "open_interest",
            "动态结算价": "settlement",
        }
    ).copy()
    keep = [c for c in ["datetime", "open", "high", "low", "close", "volume", "open_interest"] if c in df.columns]
    df = df[keep].copy()
    df["datetime"] = pd.to_datetime(df["datetime"], errors="coerce")
    df = df[df["datetime"].notna()].copy()
    df["datetime"] = df["datetime"].dt.strftime("%Y-%m-%d 00:00:00")
    for col in ["open", "high", "low", "close", "volume", "open_interest"]:
        if col in df.columns:
            df[col] = pd.to_numeric(df[col], errors="coerce")
    df = df.drop_duplicates(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    df.insert(0, "symbol", symbol)
    df.insert(1, "exchange", exchange)
    df.insert(2, "interval", "d")
    df["turnover"] = 0.0
    for col in cols:
        if col not in df.columns:
            df[col] = 0.0 if col in {"volume", "open_interest"} else None
    return df[cols]


def normalize_mapping_df(df: pd.DataFrame) -> pd.DataFrame:
    """Normalize fut_mapping output to [trade_date, mapping_ts_code]."""
    if df is None or df.empty:
        return pd.DataFrame(columns=["trade_date", "mapping_ts_code"])
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], format="%Y%m%d", errors="coerce")
    out = out.dropna(subset=["trade_date", "mapping_ts_code"])
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    return out[["trade_date", "mapping_ts_code"]].drop_duplicates().sort_values("trade_date").reset_index(drop=True)


def normalize_ftmins_df(df: pd.DataFrame, contract_code: str) -> pd.DataFrame:
    """Normalize tushare ft_mins output."""
    out = df.rename(columns={"trade_time": "datetime", "vol": "volume", "oi": "open_interest", "amount": "turnover"}).copy()
    needed = ["datetime", "open", "high", "low", "close", "volume", "open_interest", "turnover"]
    for col in needed:
        if col not in out.columns:
            out[col] = pd.NA
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    for col in ["open", "high", "low", "close", "volume", "open_interest", "turnover"]:
        out[col] = pd.to_numeric(out[col], errors="coerce")
    if "ts_code" not in out.columns:
        out["ts_code"] = contract_code
    return out[needed + ["ts_code"]].copy()


def normalize_contract_filename(contract_code: str) -> str:
    """Convert contract code to filesystem-safe stem."""
    return str(contract_code).strip().upper().replace(".", "_")


def resample_minute_bars(df_1min: pd.DataFrame, freq: str) -> pd.DataFrame:
    """Aggregate 1-minute futures bars to a higher interval within each natural day."""
    if df_1min is None or df_1min.empty:
        return pd.DataFrame()
    df = df_1min.copy()
    df["datetime"] = pd.to_datetime(df["datetime"])
    df = df.sort_values("datetime").reset_index(drop=True)
    agg_full: Dict[str, str] = {
        "open": "first",
        "high": "max",
        "low": "min",
        "close": "last",
        "volume": "sum",
        "turnover": "sum",
        "open_interest": "last",
    }
    agg = {k: v for k, v in agg_full.items() if k in df.columns}
    meta_cols = [c for c in df.columns if c not in agg and c != "datetime"]
    df["__date__"] = df["datetime"].dt.normalize()
    parts: List[pd.DataFrame] = []
    for _, g in df.groupby("__date__", sort=True):
        idx = g.set_index("datetime")
        ohlc = idx[list(agg.keys())].resample(freq, label="left", closed="left").agg(agg)
        ohlc = ohlc.dropna(subset=["open"])
        if ohlc.empty:
            continue
        ohlc = ohlc.reset_index()
        for col in meta_cols:
            ohlc[col] = g[col].iloc[0]
        parts.append(ohlc)
    if not parts:
        return pd.DataFrame()
    return pd.concat(parts, ignore_index=True).sort_values("datetime").reset_index(drop=True)


__all__ = [
    "normalize_contract_filename",
    "normalize_daily_df",
    "normalize_ftmins_df",
    "normalize_mapping_df",
    "resample_minute_bars",
]
