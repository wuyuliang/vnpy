"""§04-03 false breakout reversal strategy wrapper."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from cta.skills.price_action.failed_breakout import detect_failed_breakout


@dataclass
class FBRTrade:
    side: Literal["long", "short"]
    entry: float
    stop: float
    targets: list[float]


def build_fbr_trade(
    df: pd.DataFrame,
    range_df: pd.DataFrame,
    bar_idx: int,
    tick_size: float,
    htf_range_ok: bool,
) -> Optional[FBRTrade]:
    """Build a false-breakout reversal trade candidate."""
    if not htf_range_ok:
        return None
    i = int(bar_idx)
    if i < 0 or i >= len(df):
        return None
    fb = detect_failed_breakout(df, range_df, max_confirm_bars=3)
    row = fb.iloc[i]
    if not bool(row.get("fb_valid", False)):
        return None
    side = str(row.get("fb_side", "")).lower()
    if side not in {"long", "short"}:
        return None
    tick = abs(float(tick_size))
    close = float(df["close"].iloc[i])
    extreme = float(row.get("fb_extreme_level", close))
    upper = float(range_df["range_upper"].iloc[i]) if "range_upper" in range_df.columns else close + 1.0
    lower = float(range_df["range_lower"].iloc[i]) if "range_lower" in range_df.columns else close - 1.0

    if side == "short":
        return FBRTrade(side="short", entry=close - tick, stop=extreme + tick, targets=[(upper + lower) / 2.0, lower])
    return FBRTrade(side="long", entry=close + tick, stop=extreme - tick, targets=[(upper + lower) / 2.0, upper])


def manage_fbr_exit(
    trade: FBRTrade,
    bars_since_entry: int,
    current_price: float,
) -> str:
    """Return hold/partial/full_exit/stop_out based on targets and stop."""
    px = float(current_price)
    if trade.side == "short":
        if px >= trade.stop:
            return "stop_out"
        if px <= trade.targets[-1]:
            return "full_exit"
        if px <= trade.targets[0]:
            return "partial"
    else:
        if px <= trade.stop:
            return "stop_out"
        if px >= trade.targets[-1]:
            return "full_exit"
        if px >= trade.targets[0]:
            return "partial"
    if bars_since_entry > 30:
        return "full_exit"
    return "hold"

