"""Spot index downloader (reference-only, day frequency)."""
from __future__ import annotations

import csv
import logging
from pathlib import Path

import pandas as pd

from cta.data_code.futures_downloader import CTA_ROOT, RateLimiter, _safe_retry

logger = logging.getLogger(__name__)


class IndexDownloader:
    """Download reference index daily bars to ``cta/data/origin_index``."""

    DEFAULT_SYMBOLS: tuple[tuple[str, str], ...] = (
        ("000001.SH", "上证指数"),
        ("000852.SH", "中证1000"),
        ("000300.SH", "沪深300"),
    )

    def __init__(
        self,
        rate_limiter: RateLimiter,
        data_root: Path = CTA_ROOT / "data" / "origin_index",
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
            raise RuntimeError("TUSHARE_TOKEN is required for index downloader")
        import tushare as ts  # type: ignore

        ts.set_token(token)
        return ts.pro_api()

    @classmethod
    def load_reference_symbols(
        cls,
        csv_path: Path = CTA_ROOT / "feature" / "index_reference_symbols.csv",
    ) -> list[tuple[str, str]]:
        path = Path(csv_path)
        if not path.exists():
            return list(cls.DEFAULT_SYMBOLS)
        out: list[tuple[str, str]] = []
        with path.open("r", encoding="utf-8-sig", newline="") as fh:
            reader = csv.DictReader(fh)
            for row in reader:
                ts_code = str(row.get("ts_code", "")).strip().upper()
                name = str(row.get("name", "")).strip()
                if not ts_code:
                    continue
                out.append((ts_code, name))
        return out or list(cls.DEFAULT_SYMBOLS)

    def fetch_index_day(self, ts_code: str, start: str, end: str) -> pd.DataFrame:
        params = {
            "ts_code": str(ts_code).upper(),
            "start_date": pd.Timestamp(start).strftime("%Y%m%d"),
            "end_date": pd.Timestamp(end).strftime("%Y%m%d"),
        }
        self.rl.acquire()
        raw = _safe_retry(self.pro.index_daily, **params)
        if raw is None or raw.empty:
            return pd.DataFrame()

        out = raw.copy()
        out["trade_date"] = pd.to_datetime(out["trade_date"], errors="coerce")
        out = out.dropna(subset=["trade_date"]).copy()
        out = out.sort_values("trade_date").reset_index(drop=True)

        rename = {"vol": "volume", "amount": "turnover"}
        out = out.rename(columns=rename)
        out["symbol"] = str(ts_code).upper()
        out["exchange"] = "SSE"
        out["interval"] = "d"
        out["datetime"] = out["trade_date"].dt.strftime("%Y-%m-%d 00:00:00")
        out["open_interest"] = 0.0

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
            "pct_chg",
        ]
        for col in cols:
            if col not in out.columns:
                out[col] = pd.NA
        out = out[cols].copy()

        day_dir = self.data_root / "day"
        day_dir.mkdir(parents=True, exist_ok=True)
        out_path = day_dir / f"{str(ts_code).replace('.', '_').upper()}.csv"
        out.to_csv(out_path, index=False, encoding="utf-8-sig")
        logger.info("index day saved: %s rows=%s", out_path, len(out))
        return out

    def fetch_all(
        self,
        symbols: list[tuple[str, str]] | None = None,
        *,
        start: str,
        end: str,
    ) -> None:
        items = symbols if symbols is not None else self.load_reference_symbols()
        for ts_code, _ in items:
            self.fetch_index_day(ts_code, start, end)


__all__ = [
    "IndexDownloader",
]
