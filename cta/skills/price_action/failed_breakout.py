"""§02-05 failed breakout reversal setup."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from ._common import norm_interval


@dataclass
class FailedBreakoutSetup:
    valid: bool
    side: Literal["long", "short"]  # reversal side
    extreme_level: float
    range_boundary: float
    confirm_bar_idx: int


def detect_failed_breakout(
    df: pd.DataFrame,
    range_df: pd.DataFrame,
    max_confirm_bars: int = 3,
    interval: str = "day",
) -> pd.DataFrame:
    """Detect failed breakouts from range boundary violation + fast rejection."""
    _ = norm_interval(interval)
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"detect_failed_breakout 缺少列: {miss}")
    for c in ("range_upper", "range_lower"):
        if c not in range_df.columns:
            raise KeyError(f"range_df 缺少列: {c}")

    out = df.copy()
    out["fb_valid"] = False
    out["fb_side"] = ""
    out["fb_extreme_level"] = np.nan
    out["fb_range_boundary"] = np.nan
    out["fb_confirm_bar_idx"] = np.nan

    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    upper = range_df["range_upper"].astype(float).reindex(out.index)
    lower = range_df["range_lower"].astype(float).reindex(out.index)

    n = len(out)
    for i in range(n):
        u = upper.iloc[i]
        l = lower.iloc[i]
        if not np.isfinite(u) or not np.isfinite(l):
            continue

        # failed up-break -> short
        if high.iloc[i] > u:
            end = min(n - 1, i + int(max_confirm_bars))
            j_found = None
            for j in range(i + 1, end + 1):
                if close.iloc[j] < u:
                    j_found = j
                    break
            if j_found is not None:
                out.at[out.index[j_found], "fb_valid"] = True
                out.at[out.index[j_found], "fb_side"] = "short"
                out.at[out.index[j_found], "fb_extreme_level"] = float(high.iloc[i : j_found + 1].max())
                out.at[out.index[j_found], "fb_range_boundary"] = float(u)
                out.at[out.index[j_found], "fb_confirm_bar_idx"] = int(j_found)

        # failed down-break -> long
        if low.iloc[i] < l:
            end = min(n - 1, i + int(max_confirm_bars))
            j_found = None
            for j in range(i + 1, end + 1):
                if close.iloc[j] > l:
                    j_found = j
                    break
            if j_found is not None:
                out.at[out.index[j_found], "fb_valid"] = True
                out.at[out.index[j_found], "fb_side"] = "long"
                out.at[out.index[j_found], "fb_extreme_level"] = float(low.iloc[i : j_found + 1].min())
                out.at[out.index[j_found], "fb_range_boundary"] = float(l)
                out.at[out.index[j_found], "fb_confirm_bar_idx"] = int(j_found)
    return out


def failed_breakout_entry(
    setup: FailedBreakoutSetup,
    next_bar: pd.Series,
    tick_size: float,
) -> Optional[dict]:
    """Build reversal stop order."""
    if not setup.valid:
        return None
    tick = float(abs(tick_size))
    high = float(next_bar.get("high", np.nan))
    low = float(next_bar.get("low", np.nan))
    if not np.isfinite(high) or not np.isfinite(low):
        return None
    if setup.side == "short":
        return {"side": "short", "trigger": low - tick, "stop": setup.extreme_level + tick}
    return {"side": "long", "trigger": high + tick, "stop": setup.extreme_level - tick}

