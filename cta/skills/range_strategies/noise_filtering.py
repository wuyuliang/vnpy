"""§04-04 noise filtering combinators."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import time
from typing import Callable, Iterable

import pandas as pd


@dataclass
class FilterResult:
    name: str
    passed: bool
    reason: str | None = None


Filter = Callable[[pd.DataFrame, int], FilterResult]


def combine_filters(
    df: pd.DataFrame,
    bar_idx: int,
    filters: Iterable[Filter],
    min_pass: int = 2,
) -> bool:
    """Pass if at least min_pass filters return True."""
    passed = 0
    total = 0
    for f in filters:
        total += 1
        res = f(df, bar_idx)
        if res.passed:
            passed += 1
    if total == 0:
        return True
    return passed >= int(min_pass)


def make_volume_filter(ratio: float = 1.2, ma_n: int = 20) -> Filter:
    """Build volume confirmation filter."""
    r = float(ratio)
    n = int(ma_n)

    def _filter(df: pd.DataFrame, bar_idx: int) -> FilterResult:
        if "volume" not in df.columns:
            return FilterResult(name="volume", passed=False, reason="missing_volume")
        i = int(bar_idx)
        if i < 0 or i >= len(df):
            return FilterResult(name="volume", passed=False, reason="idx_out_of_range")
        vol = df["volume"].astype(float)
        ma = vol.rolling(n, min_periods=max(3, n // 2)).mean()
        ok = float(vol.iloc[i]) >= r * float(ma.iloc[i]) if pd.notna(ma.iloc[i]) else False
        return FilterResult(name="volume", passed=bool(ok), reason=None if ok else "below_threshold")

    return _filter


def make_time_window_filter(windows: list[tuple[str, str]]) -> Filter:
    """Build trading-session window filter."""
    parsed: list[tuple[time, time]] = []
    for st, ed in windows:
        s_h, s_m = [int(x) for x in st.split(":")]
        e_h, e_m = [int(x) for x in ed.split(":")]
        parsed.append((time(s_h, s_m), time(e_h, e_m)))

    def _filter(df: pd.DataFrame, bar_idx: int) -> FilterResult:
        if "datetime" not in df.columns:
            return FilterResult(name="time_window", passed=False, reason="missing_datetime")
        i = int(bar_idx)
        if i < 0 or i >= len(df):
            return FilterResult(name="time_window", passed=False, reason="idx_out_of_range")
        dt = pd.Timestamp(df["datetime"].iloc[i]).time()
        ok = any((st <= dt <= ed) for st, ed in parsed)
        return FilterResult(name="time_window", passed=ok, reason=None if ok else "outside_window")

    return _filter

