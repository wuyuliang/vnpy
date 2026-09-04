"""Durable on-disk cache for vendor metadata fetches.

The bundle cache in :mod:`vendor_metadata_builder` is keyed by the exact
``(symbols, start, end)`` of one run, so changing a backtest window rebuilds the
whole bundle and re-hits the vendor for data that was already downloaded. This
module caches at the *fetch* level instead: one file per vendor call, keyed by
the call's arguments. A longer or shifted window then only downloads the days it
has never seen.

Only immutable history is cached. A vendor table for a trade date that has not
closed yet, or a contract listing that changes as contracts are added, must not
be frozen on disk, so:

* date-keyed historical tables are cached only when the date is strictly before
  today;
* ``fetch_calendar`` is cached only when its window ends before today;
* ``fetch_contracts`` is cached under the current date, so it refreshes daily.

Every served call still produces an audit record: a cache hit is recorded with
``source="cache"`` so a run's audit trail never silently loses a fetch.
"""
from __future__ import annotations

from datetime import date
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import pandas as pd


_SCHEMA_VERSION = "1"


def _slug(value: str) -> str:
    text = "".join(char if char.isalnum() or char in "-_." else "_" for char in str(value))
    return text[:64] or "_"


class CachingMetadataSourceClient:
    """Wrap a :class:`MetadataSourceClient` with a durable per-call disk cache."""

    def __init__(self, inner: Any, root: str | Path) -> None:
        self._inner = inner
        self._root = Path(root)
        self._records: list[dict[str, object]] = []
        self._hits = 0
        self._misses = 0
        self._bypassed = 0

    # -- audit ---------------------------------------------------------------
    @property
    def audit_records(self) -> tuple[dict[str, object], ...]:
        return tuple(self._records) + tuple(getattr(self._inner, "audit_records", ()))

    @property
    def cache_stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "bypassed": self._bypassed,
        }

    # -- plumbing ------------------------------------------------------------
    def _path(self, method: str, key: dict[str, Any]) -> Path:
        payload = json.dumps(
            {"schema": _SCHEMA_VERSION, **key}, sort_keys=True, separators=(",", ":")
        )
        digest = hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]
        label = "_".join(_slug(key[name]) for name in sorted(key))
        return self._root / method / f"{label}__{digest}.parquet"

    def _read(self, path: Path) -> pd.DataFrame | None:
        if not path.is_file():
            return None
        try:
            return pd.read_parquet(path)
        except (OSError, ValueError):
            return None

    def _write(self, path: Path, frame: pd.DataFrame) -> None:
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(
                dir=path.parent, prefix=".tmp_", suffix=".parquet"
            )
            os.close(handle)
            temporary_path = Path(temporary)
            frame.to_parquet(temporary_path, index=False)
            os.replace(temporary_path, path)
        except (OSError, ValueError, ImportError):
            # 缓存是加速手段，写失败不能影响回测本身
            return

    def _serve(
        self,
        method: str,
        key: dict[str, Any],
        cacheable: bool,
        fetch: Any,
    ) -> pd.DataFrame:
        if not cacheable:
            self._bypassed += 1
            return fetch()
        path = self._path(method, key)
        cached = self._read(path)
        if cached is not None:
            self._hits += 1
            self._records.append(
                {"source": "cache", "method": method, "key": dict(key), "rows": len(cached)}
            )
            return cached.copy()
        frame = fetch()
        self._misses += 1
        if isinstance(frame, pd.DataFrame):
            self._write(path, frame)
        return frame

    # -- MetadataSourceClient ------------------------------------------------
    def fetch_calendar(self, exchange: str, start: date, end: date) -> pd.DataFrame:
        return self._serve(
            "fetch_calendar",
            {"exchange": exchange, "start": start.isoformat(), "end": end.isoformat()},
            end < date.today(),
            lambda: self._inner.fetch_calendar(exchange, start, end),
        )

    def fetch_contracts(self, exchange: str) -> pd.DataFrame:
        today = date.today()
        return self._serve(
            "fetch_contracts",
            {"exchange": exchange, "asof": today.isoformat()},
            True,
            lambda: self._inner.fetch_contracts(exchange),
        )

    def fetch_settlements(self, trade_date: date) -> pd.DataFrame:
        return self._serve(
            "fetch_settlements",
            {"trade_date": trade_date.isoformat()},
            trade_date < date.today(),
            lambda: self._inner.fetch_settlements(trade_date),
        )

    def fetch_daily_quote(self, contract_code: str, trade_date: date) -> pd.DataFrame:
        return self._serve(
            "fetch_daily_quote",
            {"contract_code": contract_code, "trade_date": trade_date.isoformat()},
            trade_date < date.today(),
            lambda: self._inner.fetch_daily_quote(contract_code, trade_date),
        )

    def fetch_vendor_parameters(self, source_date: date) -> pd.DataFrame:
        return self._serve(
            "fetch_vendor_parameters",
            {"source_date": source_date.isoformat()},
            source_date < date.today(),
            lambda: self._inner.fetch_vendor_parameters(source_date),
        )

    def fetch_trading_rules(self, source_date: date) -> pd.DataFrame:
        return self._serve(
            "fetch_trading_rules",
            {"source_date": source_date.isoformat()},
            source_date < date.today(),
            lambda: self._inner.fetch_trading_rules(source_date),
        )

    def fetch_czce_settlement_parameters(self, source_date: date) -> pd.DataFrame:
        return self._serve(
            "fetch_czce_settlement_parameters",
            {"source_date": source_date.isoformat()},
            source_date < date.today(),
            lambda: self._inner.fetch_czce_settlement_parameters(source_date),
        )

    def fetch_shfe_settlement_parameters(self, source_date: date) -> pd.DataFrame:
        return self._serve(
            "fetch_shfe_settlement_parameters",
            {"source_date": source_date.isoformat()},
            source_date < date.today(),
            lambda: self._inner.fetch_shfe_settlement_parameters(source_date),
        )

    def fetch_ine_settlement_parameters(self, source_date: date) -> pd.DataFrame:
        return self._serve(
            "fetch_ine_settlement_parameters",
            {"source_date": source_date.isoformat()},
            source_date < date.today(),
            lambda: self._inner.fetch_ine_settlement_parameters(source_date),
        )


__all__ = ["CachingMetadataSourceClient"]
