"""Tushare-based stock day downloader for the stock sample pipeline."""
from __future__ import annotations

import logging
import os
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import pandas as pd

logger = logging.getLogger(__name__)


STOCK_DAILY_COLUMNS: list[str] = [
    "symbol",
    "exchange",
    "interval",
    "datetime",
    "open",
    "high",
    "low",
    "close",
    "pct_chg",
    "volume",
    "open_interest",
    "turnover",
]


class RateLimiter:
    """Simple thread-safe rate limiter."""

    def __init__(self, per_minute: int) -> None:
        self.per_minute = max(1, int(per_minute))
        self.min_interval = 60.0 / float(self.per_minute)
        self._lock = threading.Lock()
        self._last_call = 0.0

    def acquire(self) -> None:
        """Sleep when needed to keep call frequency bounded."""
        with self._lock:
            now = time.monotonic()
            elapsed = now - self._last_call
            if elapsed < self.min_interval:
                time.sleep(self.min_interval - elapsed)
            self._last_call = time.monotonic()


def _safe_retry(func: Callable[..., Any], *args: Any, retries: int = 3, wait: float = 1.0, **kwargs: Any) -> Any:
    """Retry transient remote calls a small number of times."""
    last_error: Exception | None = None
    for attempt in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as exc:  # noqa: BLE001
            last_error = exc
            if attempt == retries - 1:
                break
            time.sleep(wait)
    assert last_error is not None
    raise last_error


def _numeric_column_or_zero(frame: pd.DataFrame, column: str) -> pd.Series:
    if column not in frame.columns:
        return pd.Series([0.0] * len(frame), index=frame.index)
    return pd.to_numeric(frame[column], errors="coerce").fillna(0.0)


def is_st_stock_name(name: object) -> bool:
    """Return True for common ST stock name prefixes."""
    text = str(name or "").strip().upper().replace("＊", "*")
    return text.startswith(("ST", "*ST", "S*ST"))


def stock_price_limit_threshold_pct(symbol: object) -> float:
    """Return the approximate daily limit threshold for the stock's board."""
    code = str(symbol or "").strip().upper().split(".", 1)[0]
    if code.startswith(("300", "301", "688", "689")):
        return 19.8
    return 9.8


def normalize_tushare_daily_df(raw_df: pd.DataFrame, ts_code: str, exchange: str) -> pd.DataFrame:
    """Normalize tushare daily output to the stock schema."""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=STOCK_DAILY_COLUMNS)

    frame = raw_df.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
    frame = frame.dropna(subset=["trade_date"]).copy()
    frame = frame.sort_values("trade_date").drop_duplicates("trade_date", keep="last").reset_index(drop=True)

    frame["symbol"] = str(ts_code).upper()
    frame["exchange"] = str(exchange).upper()
    frame["interval"] = "d"
    frame["datetime"] = frame["trade_date"].dt.strftime("%Y-%m-%d 00:00:00")
    frame["volume"] = pd.to_numeric(frame.get("vol"), errors="coerce").fillna(0.0)
    frame["turnover"] = pd.to_numeric(frame.get("amount"), errors="coerce").fillna(0.0)
    frame["pct_chg"] = _numeric_column_or_zero(frame, "pct_chg")
    frame["open_interest"] = 0.0

    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")

    frame = frame.dropna(subset=["open", "high", "low", "close"]).copy()
    return frame.loc[:, STOCK_DAILY_COLUMNS].reset_index(drop=True)


def normalize_tushare_daily_batch_df(
    raw_df: pd.DataFrame,
    exchange_by_symbol: dict[str, str],
) -> pd.DataFrame:
    """Normalize a trade-date batch from tushare daily output."""
    if raw_df is None or raw_df.empty:
        return pd.DataFrame(columns=STOCK_DAILY_COLUMNS)

    frame = raw_df.copy()
    frame["ts_code"] = frame["ts_code"].astype(str).str.upper()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], format="%Y%m%d", errors="coerce")
    frame = frame.dropna(subset=["trade_date", "ts_code"]).copy()
    frame = frame[frame["ts_code"].isin(exchange_by_symbol)].copy()
    if frame.empty:
        return pd.DataFrame(columns=STOCK_DAILY_COLUMNS)

    frame["symbol"] = frame["ts_code"]
    frame["exchange"] = frame["ts_code"].map(exchange_by_symbol)
    frame["interval"] = "d"
    frame["datetime"] = frame["trade_date"].dt.strftime("%Y-%m-%d 00:00:00")
    frame["volume"] = pd.to_numeric(frame.get("vol"), errors="coerce").fillna(0.0)
    frame["turnover"] = pd.to_numeric(frame.get("amount"), errors="coerce").fillna(0.0)
    frame["pct_chg"] = _numeric_column_or_zero(frame, "pct_chg")
    frame["open_interest"] = 0.0
    for column in ["open", "high", "low", "close"]:
        frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
    frame = frame.dropna(subset=["open", "high", "low", "close"]).copy()
    frame = frame.sort_values(["symbol", "datetime"]).drop_duplicates(["symbol", "datetime"], keep="last")
    return frame.loc[:, STOCK_DAILY_COLUMNS].reset_index(drop=True)


def _is_mainland_a_share(row: pd.Series) -> bool:
    ts_code = str(row.get("ts_code", "")).upper()
    symbol = str(row.get("symbol", "")).strip()
    exchange = str(row.get("exchange", "")).upper()
    if exchange not in {"SSE", "SZSE"}:
        return False
    if not (ts_code.endswith(".SH") or ts_code.endswith(".SZ")):
        return False
    if symbol.startswith(("900", "200")):
        return False
    if is_st_stock_name(row.get("name", "")):
        return False
    return True


class StockDownloader:
    """Download stock daily bars into ``stock/data/origin/day``."""

    def __init__(
        self,
        *,
        rate_limiter: RateLimiter | None = None,
        data_root: Path | None = None,
        pro: object | None = None,
    ) -> None:
        self.rate_limiter = rate_limiter or RateLimiter(450)
        self.data_root = Path(data_root) if data_root is not None else Path(__file__).resolve().parents[1] / "data" / "origin"
        self._pro = pro

    def _get_pro(self) -> object:
        if self._pro is not None:
            return self._pro

        token = os.getenv("TUSHARE_TOKEN", "").strip()
        if not token:
            raise RuntimeError("TUSHARE_TOKEN is required for stock downloader")

        import tushare as ts  # type: ignore

        self._pro = ts.pro_api(token)
        return self._pro

    def fetch_stock_day(
        self,
        ts_code: str,
        exchange: str,
        start: str,
        end: str,
        *,
        merge_existing: bool = False,
    ) -> pd.DataFrame:
        """Download one stock's day bars and write csv."""
        params = {
            "ts_code": str(ts_code).upper(),
            "start_date": pd.Timestamp(start).strftime("%Y%m%d"),
            "end_date": pd.Timestamp(end).strftime("%Y%m%d"),
        }
        self.rate_limiter.acquire()
        raw = _safe_retry(self._get_pro().daily, **params)
        normalized = normalize_tushare_daily_df(raw, params["ts_code"], exchange)

        out_dir = self.data_root / "day"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{params['ts_code'].replace('.', '_')}.csv"
        if merge_existing:
            self.write_symbol_day_files(normalized, merge_existing=True)
        else:
            normalized.to_csv(out_path, index=False, encoding="utf-8-sig")
        logger.info("stock day saved: %s rows=%s", out_path, len(normalized))
        return normalized

    def list_all_a_share_symbols(self) -> list[dict[str, str]]:
        """Return currently listed Shanghai and Shenzhen A-share symbols."""
        self.rate_limiter.acquire()
        raw = _safe_retry(
            self._get_pro().stock_basic,
            exchange="",
            list_status="L",
            fields="ts_code,symbol,name,exchange,market,list_status",
        )
        if raw is None or raw.empty:
            return []

        frame = raw.copy()
        frame["ts_code"] = frame["ts_code"].astype(str).str.upper()
        frame["exchange"] = frame["exchange"].astype(str).str.upper()
        frame = frame[frame.apply(_is_mainland_a_share, axis=1)].copy()
        frame["exchange_order"] = frame["exchange"].map({"SSE": 0, "SZSE": 1}).fillna(99)
        frame = frame.sort_values(["exchange_order", "ts_code"]).reset_index(drop=True)
        return [
            {
                "ts_code": str(row.ts_code),
                "exchange": str(row.exchange),
                "name": str(row.name),
            }
            for row in frame.itertuples(index=False)
        ]

    def fetch_trade_date_day(
        self,
        trade_date: str,
        exchange_by_symbol: dict[str, str],
    ) -> pd.DataFrame:
        """Download all stock day bars for one trade date."""
        self.rate_limiter.acquire()
        raw = _safe_retry(
            self._get_pro().daily,
            trade_date=pd.Timestamp(trade_date).strftime("%Y%m%d"),
        )
        return normalize_tushare_daily_batch_df(raw, exchange_by_symbol)

    def fetch_daily_basic(self, trade_date: str) -> pd.DataFrame:
        """Download daily basic fields used for market-cap enrichment."""
        columns = ["ts_code", "trade_date", "close", "total_mv", "circ_mv"]
        self.rate_limiter.acquire()
        raw = _safe_retry(
            self._get_pro().daily_basic,
            trade_date=pd.Timestamp(trade_date).strftime("%Y%m%d"),
            fields="ts_code,trade_date,close,total_mv,circ_mv",
        )
        if raw is None or raw.empty:
            return pd.DataFrame(columns=columns)
        frame = raw.copy()
        frame["ts_code"] = frame["ts_code"].astype(str).str.upper()
        for column in ["close", "total_mv", "circ_mv"]:
            frame[column] = pd.to_numeric(frame.get(column), errors="coerce")
        frame["trade_date"] = frame["trade_date"].astype(str)
        return frame.loc[:, columns].reset_index(drop=True)

    def list_open_trade_dates(self, start: str, end: str) -> list[str]:
        """Return open SSE trading dates as YYYY-MM-DD strings."""
        self.rate_limiter.acquire()
        raw = _safe_retry(
            self._get_pro().trade_cal,
            exchange="SSE",
            start_date=pd.Timestamp(start).strftime("%Y%m%d"),
            end_date=pd.Timestamp(end).strftime("%Y%m%d"),
            is_open="1",
        )
        if raw is None or raw.empty:
            return [
                str(day.date())
                for day in pd.date_range(start=start, end=end, freq="B")
            ]
        dates = pd.to_datetime(raw["cal_date"], format="%Y%m%d", errors="coerce").dropna()
        return [str(day.date()) for day in dates.sort_values()]

    def fetch_all_a_share_days(
        self,
        *,
        start: str,
        end: str,
        symbols: list[dict[str, str]] | None = None,
        merge_existing: bool = False,
    ) -> pd.DataFrame:
        """Download all A-share day bars by trade date and write per-symbol csv files."""
        universe = symbols if symbols is not None else self.list_all_a_share_symbols()
        exchange_by_symbol = {
            str(item["ts_code"]).upper(): str(item["exchange"]).upper()
            for item in universe
        }
        frames: list[pd.DataFrame] = []
        for trade_date in self.list_open_trade_dates(start, end):
            batch = self.fetch_trade_date_day(trade_date, exchange_by_symbol)
            if batch.empty:
                continue
            frames.append(batch)
            logger.info("stock daily batch fetched: %s rows=%s", trade_date, len(batch))

        if frames:
            merged = pd.concat(frames, ignore_index=True)
        else:
            merged = pd.DataFrame(columns=STOCK_DAILY_COLUMNS)
        self.write_symbol_day_files(merged, merge_existing=merge_existing)
        return merged

    def write_symbol_day_files(self, frame: pd.DataFrame, *, merge_existing: bool = False) -> list[Path]:
        """Write normalized day bars to one csv per symbol."""
        out_dir = self.data_root / "day"
        out_dir.mkdir(parents=True, exist_ok=True)
        if frame.empty:
            return []

        prepared = frame.copy()
        if "pct_chg" not in prepared.columns:
            prepared["pct_chg"] = 0.0
        normalized = prepared.loc[:, STOCK_DAILY_COLUMNS].copy()
        normalized["datetime"] = pd.to_datetime(normalized["datetime"], errors="coerce")
        normalized = normalized.dropna(subset=["datetime", "symbol"]).copy()
        normalized = normalized.sort_values(["symbol", "datetime"]).drop_duplicates(["symbol", "datetime"], keep="last")
        normalized["datetime"] = normalized["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")

        paths: list[Path] = []
        for symbol, group in normalized.groupby("symbol", sort=True):
            ts_code = str(symbol).upper()
            path = out_dir / f"{ts_code.replace('.', '_')}.csv"
            output_group = group.loc[:, STOCK_DAILY_COLUMNS].copy()
            if merge_existing and path.exists():
                existing = pd.read_csv(path)
                output_group = pd.concat([existing, output_group], ignore_index=True)
                output_group["datetime"] = pd.to_datetime(output_group["datetime"], errors="coerce")
                output_group = output_group.dropna(subset=["datetime", "symbol"]).copy()
                output_group = output_group.sort_values("datetime").drop_duplicates("datetime", keep="last")
                output_group["datetime"] = output_group["datetime"].dt.strftime("%Y-%m-%d %H:%M:%S")
                for column in STOCK_DAILY_COLUMNS:
                    if column not in output_group.columns:
                        output_group[column] = 0.0 if column in {"pct_chg", "volume", "open_interest", "turnover"} else ""
                output_group = output_group.loc[:, STOCK_DAILY_COLUMNS]
            output_group.to_csv(path, index=False, encoding="utf-8-sig")
            paths.append(path)
        logger.info("stock day files written: symbols=%s", len(paths))
        return paths

    def fetch_many(self, symbols: list[dict[str, str]], start: str, end: str, *, merge_existing: bool = False) -> list[Path]:
        """Download a batch of stock day csv files."""
        written: list[Path] = []
        for item in symbols:
            ts_code = str(item["ts_code"]).upper()
            exchange = str(item["exchange"]).upper()
            self.fetch_stock_day(ts_code, exchange, start, end, merge_existing=merge_existing)
            written.append(self.data_root / "day" / f"{ts_code.replace('.', '_')}.csv")
        return written


__all__ = [
    "RateLimiter",
    "STOCK_DAILY_COLUMNS",
    "StockDownloader",
    "_safe_retry",
    "is_st_stock_name",
    "normalize_tushare_daily_df",
    "normalize_tushare_daily_batch_df",
    "stock_price_limit_threshold_pct",
]
