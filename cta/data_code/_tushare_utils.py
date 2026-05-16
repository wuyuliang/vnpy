"""Reusable Tushare data helpers for CTA downloaders."""
from __future__ import annotations

import logging
from datetime import timedelta
from pathlib import Path
from typing import Callable, Mapping, MutableMapping

import pandas as pd

logger = logging.getLogger(__name__)


def _to_trade_date_str(series: pd.Series) -> pd.Series:
    dt = pd.to_datetime(series, errors="coerce")
    return dt.dt.strftime("%Y-%m-%d")


def splice_continuous_from_mapping(
    monthly_dfs: Mapping[str, pd.DataFrame],
    mapping_df: pd.DataFrame,
) -> pd.DataFrame:
    """Build continuous series by applying daily mapping to monthly contract bars."""
    if mapping_df is None or mapping_df.empty:
        return pd.DataFrame()
    if "trade_date" not in mapping_df.columns or "mapping_ts_code" not in mapping_df.columns:
        raise KeyError("mapping_df must contain trade_date and mapping_ts_code")

    parts: list[pd.DataFrame] = []
    for ts_code, raw in monthly_dfs.items():
        if raw is None or raw.empty:
            continue
        df = raw.copy()
        if "trade_date" not in df.columns:
            continue
        df["trade_date"] = _to_trade_date_str(df["trade_date"])
        df = df.dropna(subset=["trade_date"]).copy()
        df["mapping_ts_code"] = str(ts_code)
        parts.append(df)
    if not parts:
        return pd.DataFrame()

    all_monthly = pd.concat(parts, ignore_index=True)
    mapping = mapping_df.copy()
    mapping["trade_date"] = _to_trade_date_str(mapping["trade_date"])
    mapping = mapping.dropna(subset=["trade_date", "mapping_ts_code"]).copy()
    mapping["mapping_ts_code"] = mapping["mapping_ts_code"].astype(str)

    merged = mapping.merge(
        all_monthly,
        on=["trade_date", "mapping_ts_code"],
        how="left",
    )
    merged = merged.sort_values("trade_date").reset_index(drop=True)
    return merged


def chunked_minute_fetch(
    pro: object,
    ts_code: str,
    freq: str,
    start: str,
    end: str,
    rate_limiter: object | None = None,
    chunk_days: int = 7,
    safe_retry: Callable | None = None,
) -> pd.DataFrame:
    """Fetch minute bars in chunks via ``pro.ft_mins``."""
    start_ts = pd.Timestamp(start).normalize()
    end_ts = pd.Timestamp(end).normalize()
    if start_ts > end_ts:
        return pd.DataFrame()

    rows: list[pd.DataFrame] = []
    cur = start_ts
    delta = timedelta(days=max(1, int(chunk_days)))
    while cur <= end_ts:
        right = min(cur + delta - timedelta(days=1), end_ts)
        params = {
            "ts_code": str(ts_code),
            "freq": str(freq),
            "start_date": f"{cur.strftime('%Y-%m-%d')} 00:00:00",
            "end_date": f"{right.strftime('%Y-%m-%d')} 23:59:59",
        }
        try:
            if rate_limiter is not None and hasattr(rate_limiter, "acquire"):
                rate_limiter.acquire()
            if safe_retry is not None:
                df = safe_retry(pro.ft_mins, **params)
            else:
                df = pro.ft_mins(**params)
        except Exception as exc:  # noqa: BLE001
            logger.warning("ft_mins failed for %s [%s~%s]: %s", ts_code, cur.date(), right.date(), exc)
            cur = right + timedelta(days=1)
            continue
        if df is not None and not df.empty:
            rows.append(df)
        cur = right + timedelta(days=1)

    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, ignore_index=True)
    time_col = "trade_time" if "trade_time" in out.columns else "datetime"
    if time_col in out.columns:
        out[time_col] = pd.to_datetime(out[time_col], errors="coerce")
        out = out.dropna(subset=[time_col]).sort_values(time_col).reset_index(drop=True)
    return out


def write_parquet_partitioned(
    df: pd.DataFrame,
    root_dir: Path,
    prefix: str,
    interval: str,
) -> int:
    """Write bars into ``root_dir/interval/prefix/YYYY-MM-DD.parquet``."""
    if df is None or df.empty:
        return 0
    out = df.copy()
    if "datetime" not in out.columns:
        raise KeyError("df missing datetime column for partition write")

    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).copy()
    if out.empty:
        return 0
    out["_trade_date"] = out["datetime"].dt.strftime("%Y-%m-%d")

    base = Path(root_dir) / str(interval) / str(prefix).upper()
    base.mkdir(parents=True, exist_ok=True)
    count = 0
    for trade_date, part in out.groupby("_trade_date", sort=True):
        path = base / f"{trade_date}.parquet"
        part.drop(columns=["_trade_date"]).to_parquet(path, index=False)
        count += 1
    return count


__all__ = [
    "splice_continuous_from_mapping",
    "chunked_minute_fetch",
    "write_parquet_partitioned",
]
