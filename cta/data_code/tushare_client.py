"""Shared tushare/utility helpers for futures data downloads."""
from __future__ import annotations

import logging
import re
import threading
import time
from collections import deque
from typing import List, Optional

logger = logging.getLogger(__name__)

_ALPHA_PREFIX_RE = re.compile(r"^([A-Za-z]+)")


def alpha_prefix(symbol: str) -> str:
    """Extract alpha prefix for symbol folder naming."""
    m = _ALPHA_PREFIX_RE.match(str(symbol).strip())
    return m.group(1).upper() if m else str(symbol).strip().upper()


EXCHANGE_VARIANTS: dict[str, list[str]] = {
    "SHFE": ["SHF", "SHFE"],
    "SHF": ["SHF", "SHFE"],
    "CZCE": ["CZC", "ZCE", "CZCE"],
    "CZC": ["CZC", "ZCE", "CZCE"],
    "ZCE": ["ZCE", "CZC", "CZCE"],
    "DCE": ["DCE"],
    "INE": ["INE"],
    "GFEX": ["GFE", "GFEX"],
    "GFE": ["GFE", "GFEX"],
    "CFFEX": ["CFX", "CFFEX"],
}


def tushare_exchange_variants(exchange: str) -> List[str]:
    """Return ts_code suffix candidates for an exchange."""
    key = str(exchange).strip().upper()
    if key in EXCHANGE_VARIANTS:
        return EXCHANGE_VARIANTS[key]
    return [key]


class RateLimiter:
    """Simple thread-safe per-minute rate limiter."""

    def __init__(self, max_per_min: int = 480):
        self.max = max(1, int(max_per_min))
        self._lock = threading.Lock()
        self._calls: deque[float] = deque()

    def acquire(self) -> None:
        while True:
            with self._lock:
                now = time.time()
                while self._calls and now - self._calls[0] > 60.0:
                    self._calls.popleft()
                if len(self._calls) < self.max:
                    self._calls.append(now)
                    return
                wait = 60.0 - (now - self._calls[0])
            if wait > 0:
                time.sleep(min(wait, 5.0))


def _safe_retry(func, *args, retries: int = 5, wait: float = 2.0, **kwargs):
    """Retry wrapper for transient tushare/akshare calls."""
    last_err: Optional[Exception] = None
    for i in range(retries):
        try:
            return func(*args, **kwargs)
        except Exception as e:  # noqa: BLE001
            last_err = e
            logger.warning("call %s failed (%s/%s): %s", func.__name__, i + 1, retries, e)
            time.sleep(wait)
    assert last_err is not None
    raise last_err


__all__ = [
    "alpha_prefix",
    "tushare_exchange_variants",
    "RateLimiter",
    "_safe_retry",
]
