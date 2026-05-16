"""Trading session templates and symbol routing."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Final

import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_cluster


def _parse_hhmm(raw: str) -> time:
    text = str(raw).strip()
    hh, mm = text.split(":")
    return time(hour=int(hh), minute=int(mm))


def _minutes_between(start: time, end: time) -> int:
    start_min = start.hour * 60 + start.minute
    end_min = end.hour * 60 + end.minute
    if end_min >= start_min:
        return end_min - start_min
    return (24 * 60 - start_min) + end_min


@dataclass(frozen=True)
class TradingSession:
    """Trading session definition for validation."""

    name: str
    sessions: tuple[tuple[str, str], ...]
    has_night_session: bool

    def total_minutes(self) -> int:
        total = 0
        for start_raw, end_raw in self.sessions:
            total += _minutes_between(_parse_hhmm(start_raw), _parse_hhmm(end_raw))
        return int(total)

    def is_within(self, dt: pd.Timestamp) -> bool:
        ts = pd.Timestamp(dt)
        t = ts.time()
        cur = t.hour * 60 + t.minute
        for start_raw, end_raw in self.sessions:
            s = _parse_hhmm(start_raw)
            e = _parse_hhmm(end_raw)
            s_min = s.hour * 60 + s.minute
            e_min = e.hour * 60 + e.minute
            if e_min >= s_min:
                if s_min <= cur < e_min:
                    return True
            else:
                # Overnight segment, e.g. 21:00 -> 02:30
                if cur >= s_min or cur < e_min:
                    return True
        return False

    def expected_bar_count(self, freq_minutes: int) -> int:
        freq = max(1, int(freq_minutes))
        return int(self.total_minutes() // freq)


COMMODITY_DAY_NIGHT: Final[TradingSession] = TradingSession(
    name="commodity_day_night",
    sessions=(("09:00", "11:30"), ("13:30", "15:00"), ("21:00", "23:00")),
    has_night_session=True,
)

COMMODITY_NIGHT_23: Final[TradingSession] = COMMODITY_DAY_NIGHT

COMMODITY_NIGHT_01: Final[TradingSession] = TradingSession(
    name="commodity_night_01",
    sessions=(("09:00", "11:30"), ("13:30", "15:00"), ("21:00", "01:00")),
    has_night_session=True,
)

COMMODITY_NIGHT_0230: Final[TradingSession] = TradingSession(
    name="commodity_night_0230",
    sessions=(("09:00", "11:30"), ("13:30", "15:00"), ("21:00", "02:30")),
    has_night_session=True,
)

INDEX_FUTURES: Final[TradingSession] = TradingSession(
    name="index_futures",
    sessions=(("09:30", "11:30"), ("13:00", "15:00")),
    has_night_session=False,
)

BOND_FUTURES: Final[TradingSession] = TradingSession(
    name="bond_futures",
    sessions=(("09:15", "11:30"), ("13:00", "15:15")),
    has_night_session=False,
)

INDEX_SPOT: Final[TradingSession] = TradingSession(
    name="index_spot",
    sessions=(("09:30", "11:30"), ("13:00", "15:00")),
    has_night_session=False,
)


CLUSTER_TO_SESSION: Final[dict[str, TradingSession]] = {
    "black": COMMODITY_NIGHT_23,
    "metal": COMMODITY_NIGHT_23,
    "chemical": COMMODITY_NIGHT_23,
    "agri": COMMODITY_NIGHT_23,
    "energy": COMMODITY_NIGHT_23,
    "precious": COMMODITY_NIGHT_23,
    "index": INDEX_FUTURES,
    "bond": BOND_FUTURES,
    "other": COMMODITY_NIGHT_23,
}

# 细化夜盘模板：优先按品种字母前缀覆盖 cluster 默认值。
_NIGHT_0230_PREFIXES: Final[set[str]] = {
    "AU", "AG", "CU", "AL", "ZN", "PB", "NI", "SN",
}
_NIGHT_01_PREFIXES: Final[set[str]] = {
    "RU", "NR", "BU", "FU", "SC", "LU", "SP", "TA", "MA", "EG", "EB", "PG",
}


def _symbol_alpha_prefix(symbol: str) -> str:
    text = str(symbol).upper().strip()
    out: list[str] = []
    for ch in text:
        if "A" <= ch <= "Z":
            out.append(ch)
        else:
            break
    return "".join(out)


def get_session_for_symbol(symbol: str) -> TradingSession:
    prefix = _symbol_alpha_prefix(symbol)
    if prefix in _NIGHT_0230_PREFIXES:
        return COMMODITY_NIGHT_0230
    if prefix in _NIGHT_01_PREFIXES:
        return COMMODITY_NIGHT_01
    cluster = infer_symbol_cluster(symbol)
    return CLUSTER_TO_SESSION.get(cluster, COMMODITY_NIGHT_23)


__all__ = [
    "TradingSession",
    "COMMODITY_DAY_NIGHT",
    "COMMODITY_NIGHT_23",
    "COMMODITY_NIGHT_01",
    "COMMODITY_NIGHT_0230",
    "INDEX_FUTURES",
    "BOND_FUTURES",
    "INDEX_SPOT",
    "CLUSTER_TO_SESSION",
    "get_session_for_symbol",
]
