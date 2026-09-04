"""Best-effort disk cache for deterministic minute-bar aggregation."""
from __future__ import annotations

from collections.abc import Callable, Sequence
from datetime import time
import hashlib
import json
import os
from pathlib import Path
import tempfile
from typing import Any

import pandas as pd


AGGREGATION_CACHE_VERSION = 1
DEFAULT_AGGREGATION_CACHE_ROOT = Path("cta/data/origin/aggregated_cache")

_CONTENT_COLUMNS = (
    "contract_code",
    "bar_end",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "open_interest",
    "exchange_trade_date",
)
_OPTIONAL_CONTENT_COLUMNS = ("turnover", "feature_sequence")


class AggregationCache:
    """Cache aggregation results without making cache health a run dependency."""

    def __init__(
        self,
        root: str | Path | None,
        *,
        version: int = AGGREGATION_CACHE_VERSION,
    ) -> None:
        self.root = Path(root) if root else None
        self.version = int(version)
        self._hits = 0
        self._misses = 0
        self._errors = 0

    @property
    def stats(self) -> dict[str, int]:
        return {
            "hits": self._hits,
            "misses": self._misses,
            "errors": self._errors,
        }

    def get_or_compute(
        self,
        frame: pd.DataFrame,
        *,
        aggregation: str,
        minutes: int | None,
        sessions: Sequence[Any],
        compute: Callable[[], pd.DataFrame],
    ) -> pd.DataFrame:
        if self.root is None:
            return compute()
        try:
            path = self._path(
                frame,
                aggregation=aggregation,
                minutes=minutes,
                sessions=sessions,
            )
        except Exception:
            self._errors += 1
            return compute()

        cached = self._read(path)
        if cached is not None:
            self._hits += 1
            return cached

        self._misses += 1
        result = compute()
        self._write(path, result)
        return result

    def _path(
        self,
        frame: pd.DataFrame,
        *,
        aggregation: str,
        minutes: int | None,
        sessions: Sequence[Any],
    ) -> Path:
        payload = {
            "version": self.version,
            "aggregation": aggregation,
            "minutes": minutes,
            "sessions": _session_payload(sessions),
            "content_sha256": _frame_content_sha256(frame),
        }
        encoded = json.dumps(
            payload,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        key = hashlib.sha256(encoded).hexdigest()
        assert self.root is not None
        return self.root / key[:2] / f"{key}.parquet"

    def _read(self, path: Path) -> pd.DataFrame | None:
        if not path.is_file():
            return None
        try:
            return pd.read_parquet(path)
        except Exception:
            self._errors += 1
            return None

    def _write(self, path: Path, frame: pd.DataFrame) -> None:
        temporary_path: Path | None = None
        try:
            path.parent.mkdir(parents=True, exist_ok=True)
            handle, temporary = tempfile.mkstemp(
                dir=path.parent,
                prefix=".tmp_",
                suffix=".parquet",
            )
            os.close(handle)
            temporary_path = Path(temporary)
            frame.to_parquet(temporary_path, index=False)
            os.replace(temporary_path, path)
            temporary_path = None
        except Exception:
            self._errors += 1
        finally:
            if temporary_path is not None:
                try:
                    temporary_path.unlink(missing_ok=True)
                except OSError:
                    pass


def cached_aggregate_completed_bars(
    cache: AggregationCache,
    frame: pd.DataFrame,
    *,
    minutes: int,
    sessions: Sequence[Any],
    compute: Callable[..., pd.DataFrame],
) -> pd.DataFrame:
    """Return cached intraday aggregation or compute it directly on a miss."""
    return cache.get_or_compute(
        frame,
        aggregation="intraday",
        minutes=minutes,
        sessions=sessions,
        compute=lambda: compute(frame, minutes=minutes, sessions=sessions),
    )


def cached_aggregate_completed_daily_bars(
    cache: AggregationCache,
    frame: pd.DataFrame,
    *,
    sessions: Sequence[Any],
    compute: Callable[..., pd.DataFrame],
) -> pd.DataFrame:
    """Return cached daily aggregation or compute it directly on a miss."""
    return cache.get_or_compute(
        frame,
        aggregation="daily",
        minutes=None,
        sessions=sessions,
        compute=lambda: compute(frame, sessions=sessions),
    )


def _frame_content_sha256(frame: pd.DataFrame) -> str:
    columns = [column for column in _CONTENT_COLUMNS if column in frame]
    columns.extend(
        column for column in _OPTIONAL_CONTENT_COLUMNS if column in frame
    )
    normalized = frame.loc[:, columns].copy()
    if "contract_code" in normalized:
        normalized["contract_code"] = normalized["contract_code"].astype(str)
    if "bar_end" in normalized:
        normalized["bar_end"] = pd.to_datetime(
            normalized["bar_end"], utc=True, errors="coerce"
        ).astype("int64")
    if "exchange_trade_date" in normalized:
        normalized["exchange_trade_date"] = pd.to_datetime(
            normalized["exchange_trade_date"], errors="coerce"
        ).dt.strftime("%Y-%m-%d")
    numeric_columns = [
        column
        for column in (*_CONTENT_COLUMNS[2:-1], *_OPTIONAL_CONTENT_COLUMNS)
        if column in normalized
    ]
    for column in numeric_columns:
        normalized[column] = pd.to_numeric(
            normalized[column], errors="coerce"
        ).astype("float64")
    normalized = normalized.sort_values(
        [column for column in ("contract_code", "bar_end") if column in normalized],
        kind="stable",
    ).reset_index(drop=True)
    hashed = pd.util.hash_pandas_object(
        normalized,
        index=False,
        categorize=False,
    )
    digest = hashlib.sha256()
    digest.update("\x1f".join(columns).encode("utf-8"))
    digest.update(hashed.to_numpy().tobytes())
    return digest.hexdigest()


def _session_payload(sessions: Sequence[Any]) -> list[dict[str, object]]:
    return [
        {
            "session_id": str(session.session_id),
            "is_night": bool(session.is_night),
            "segments": [
                {
                    "segment_id": str(segment.segment_id),
                    "start": _time_text(segment.start),
                    "end": _time_text(segment.end),
                    "bucket_anchor": _time_text(segment.bucket_anchor),
                }
                for segment in session.segments
            ],
        }
        for session in sessions
    ]


def _time_text(value: time) -> str:
    return value.isoformat(timespec="microseconds")


__all__ = [
    "AGGREGATION_CACHE_VERSION",
    "DEFAULT_AGGREGATION_CACHE_ROOT",
    "AggregationCache",
    "cached_aggregate_completed_bars",
    "cached_aggregate_completed_daily_bars",
]
