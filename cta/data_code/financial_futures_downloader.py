"""Financial futures downloader for CFFEX continuous contracts."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import pandas as pd

from cta.data_code._tushare_utils import (
    chunked_minute_fetch,
    splice_continuous_from_mapping,
    write_parquet_partitioned,
)
from cta.data_code.futures_downloader import (
    CTA_ROOT,
    RateLimiter,
    _safe_retry,
)

logger = logging.getLogger(__name__)

_INTERVAL_ALIAS = {
    "min": "minute",
    "minute": "minute",
    "1min": "minute",
    "5min": "minute5",
    "minute5": "minute5",
    "15min": "minute15",
    "minute15": "minute15",
    "30min": "minute30",
    "minute30": "minute30",
    "60min": "minute60",
    "minute60": "minute60",
}

_API_FREQ = {
    "minute": "1min",
    "minute5": "5min",
    "minute15": "15min",
    "minute30": "30min",
    "minute60": "60min",
}


def _normalize_interval(interval: str) -> str:
    key = str(interval).strip().lower()
    if key not in _INTERVAL_ALIAS:
        raise ValueError(f"unsupported interval: {interval}")
    return _INTERVAL_ALIAS[key]


def _normalize_mapping_df(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame(columns=["trade_date", "mapping_ts_code"])
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
    out = out.dropna(subset=["trade_date", "mapping_ts_code"]).copy()
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    out["mapping_ts_code"] = out["mapping_ts_code"].astype(str)
    out = out[["trade_date", "mapping_ts_code"]].drop_duplicates().sort_values("trade_date")
    return out.reset_index(drop=True)


def _normalize_fut_daily(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
    out = out.dropna(subset=["trade_date"]).copy()
    out["trade_date"] = out["trade_date"].dt.strftime("%Y-%m-%d")
    rename = {"vol": "volume", "amount": "turnover", "oi": "open_interest"}
    out = out.rename(columns=rename)
    for col in ("open", "high", "low", "close", "volume", "turnover", "open_interest"):
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    keep = ["trade_date", "open", "high", "low", "close", "volume", "turnover", "open_interest"]
    return out[keep].copy()


def _normalize_ft_mins(df: pd.DataFrame) -> pd.DataFrame:
    if df is None or df.empty:
        return pd.DataFrame()
    out = df.copy()
    out = out.rename(
        columns={
            "trade_time": "datetime",
            "vol": "volume",
            "amount": "turnover",
            "oi": "open_interest",
        }
    )
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).copy()
    for col in ("open", "high", "low", "close", "volume", "turnover", "open_interest"):
        if col not in out.columns:
            out[col] = pd.NA
        out[col] = pd.to_numeric(out[col], errors="coerce")
    keep = ["datetime", "open", "high", "low", "close", "volume", "turnover", "open_interest"]
    return out[keep].sort_values("datetime").reset_index(drop=True)


class FinancialFuturesDownloader:
    """Downloader for IF/IH/IC/IM/T/TF/TS continuous contracts."""

    SUPPORTED_PREFIXES: tuple[str, ...] = ("IF", "IH", "IC", "IM", "T", "TF", "TS")
    EXCHANGE: str = "CFFEX"
    TS_SUFFIX: str = ".CFX"

    def __init__(
        self,
        rate_limiter: RateLimiter,
        data_root: Path = CTA_ROOT / "data" / "origin",
        pro: object | None = None,
    ) -> None:
        self.rl = rate_limiter
        self.data_root = Path(data_root)
        self.pro = pro if pro is not None else self._get_pro()

    @staticmethod
    def _get_pro() -> object:
        import os

        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TUSHARE_TOKEN is required for financial futures downloader")
        import tushare as ts  # type: ignore

        ts.set_token(token)
        return ts.pro_api()

    def fetch_mapping(self, prefix: str, start: str, end: str) -> pd.DataFrame:
        pref = str(prefix).upper()
        if pref not in self.SUPPORTED_PREFIXES:
            raise ValueError(f"unsupported financial prefix: {prefix}")
        params = {
            "ts_code": f"{pref}{self.TS_SUFFIX}",
            "start_date": pd.Timestamp(start).strftime("%Y%m%d"),
            "end_date": pd.Timestamp(end).strftime("%Y%m%d"),
        }
        self.rl.acquire()
        raw = _safe_retry(self.pro.fut_mapping, **params)
        return _normalize_mapping_df(raw)

    def fetch_continuous_day(self, prefix: str, start: str, end: str) -> pd.DataFrame:
        pref = str(prefix).upper()
        mapping = self.fetch_mapping(pref, start, end)
        if mapping.empty:
            return pd.DataFrame()

        monthly: dict[str, pd.DataFrame] = {}
        for ts_code in sorted(mapping["mapping_ts_code"].unique()):
            self.rl.acquire()
            raw = _safe_retry(
                self.pro.fut_daily,
                ts_code=str(ts_code),
                start_date=pd.Timestamp(start).strftime("%Y%m%d"),
                end_date=pd.Timestamp(end).strftime("%Y%m%d"),
            )
            monthly[str(ts_code)] = _normalize_fut_daily(raw)

        stitched = splice_continuous_from_mapping(monthly, mapping)
        if stitched.empty:
            return stitched

        out = stitched.copy()
        out["symbol"] = f"{pref}0"
        out["exchange"] = self.EXCHANGE
        out["interval"] = "d"
        out["datetime"] = pd.to_datetime(out["trade_date"], errors="coerce").dt.strftime("%Y-%m-%d 00:00:00")
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
        for col in cols:
            if col not in out.columns:
                out[col] = pd.NA
        out = out[cols].dropna(subset=["datetime"]).drop_duplicates("datetime").sort_values("datetime")
        out = out.reset_index(drop=True)

        day_dir = self.data_root / "day"
        day_dir.mkdir(parents=True, exist_ok=True)
        out_path = day_dir / f"{pref}0.csv"
        out.to_csv(out_path, index=False, encoding="utf-8-sig")
        logger.info("financial day saved: %s rows=%s", out_path, len(out))
        return out

    def fetch_continuous_minute(self, prefix: str, freq: str, start: str, end: str) -> None:
        pref = str(prefix).upper()
        interval = _normalize_interval(freq)
        api_freq = _API_FREQ[interval]
        mapping = self.fetch_mapping(pref, start, end)
        if mapping.empty:
            logger.warning("financial mapping empty: prefix=%s", pref)
            return

        parts: list[pd.DataFrame] = []
        for row in mapping.itertuples(index=False):
            trade_date = str(getattr(row, "trade_date"))
            ts_code = str(getattr(row, "mapping_ts_code"))
            raw = chunked_minute_fetch(
                pro=self.pro,
                ts_code=ts_code,
                freq=api_freq,
                start=trade_date,
                end=trade_date,
                rate_limiter=self.rl,
                chunk_days=1,
                safe_retry=_safe_retry,
            )
            day_df = _normalize_ft_mins(raw)
            if day_df.empty:
                continue
            day_df["symbol"] = f"{pref}0"
            day_df["exchange"] = self.EXCHANGE
            day_df["interval"] = interval
            parts.append(day_df)

        if not parts:
            logger.warning("financial minute empty: prefix=%s interval=%s", pref, interval)
            return

        merged = pd.concat(parts, ignore_index=True).sort_values("datetime").reset_index(drop=True)
        write_parquet_partitioned(
            df=merged,
            root_dir=self.data_root,
            prefix=pref,
            interval=interval,
        )

    def fetch_all(self, prefix: str, intervals: Iterable[str], start: str, end: str) -> None:
        pref = str(prefix).upper()
        for raw_interval in intervals:
            token = str(raw_interval).strip().lower()
            if token == "day":
                self.fetch_continuous_day(pref, start, end)
                continue
            self.fetch_continuous_minute(pref, token, start, end)


__all__ = [
    "FinancialFuturesDownloader",
]
