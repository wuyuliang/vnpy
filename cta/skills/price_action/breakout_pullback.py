"""§02-03 breakout pullback continuation."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from ._common import atr, norm_interval


@dataclass
class PullbackSetup:
    valid: bool
    direction: Literal["long", "short"]
    breakout_level: float
    pullback_low: float
    bars_since_breakout: int
    confirmed: bool


def detect_breakout_pullback(
    df: pd.DataFrame,
    breakout_df: pd.DataFrame,
    max_bars_since_brk: int = 20,
    max_pullback_atr: float = 1.5,
    interval: str = "day",
) -> pd.DataFrame:
    """Detect pullback setup from a prior breakout anchor."""
    _ = norm_interval(interval)
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"detect_breakout_pullback 缺少列: {miss}")
    if "breakout_level" not in breakout_df.columns:
        raise KeyError("breakout_df 缺少 breakout_level")

    out = df.copy()
    out["bp_valid"] = False
    out["bp_direction"] = ""
    out["bp_breakout_level"] = np.nan
    out["bp_pullback_low"] = np.nan
    out["bp_bars_since_breakout"] = np.nan
    out["bp_confirmed"] = False

    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    a = atr(high, low, close, 14).replace(0.0, np.nan)

    anchor_idx = breakout_df["breakout_level"].dropna().index
    for idx in anchor_idx:
        i0 = int(idx)
        if i0 >= len(out) - 1:
            continue
        brk_level = float(breakout_df["breakout_level"].loc[idx])
        direction_raw = str(breakout_df.get("breakout_direction", pd.Series("", index=breakout_df.index)).loc[idx]).lower()
        direction = "long" if direction_raw in {"long", "1", "up"} else "short"
        end = min(len(out) - 1, i0 + int(max_bars_since_brk))
        sl = slice(i0 + 1, end + 1)

        if direction == "long":
            pb_extreme = float(low.iloc[sl].min()) if i0 + 1 <= end else np.nan
            dist_atr = (brk_level - pb_extreme) / max(float(a.iloc[i0]), 1e-9) if np.isfinite(pb_extreme) else np.nan
            valid = np.isfinite(dist_atr) and (0.0 <= dist_atr <= max_pullback_atr)
            # confirmation: close back above level
            conf_pos = None
            if valid:
                above = np.where(close.iloc[sl].to_numpy() > brk_level)[0]
                conf_pos = int(above[0]) if len(above) else None
        else:
            pb_extreme = float(high.iloc[sl].max()) if i0 + 1 <= end else np.nan
            dist_atr = (pb_extreme - brk_level) / max(float(a.iloc[i0]), 1e-9) if np.isfinite(pb_extreme) else np.nan
            valid = np.isfinite(dist_atr) and (0.0 <= dist_atr <= max_pullback_atr)
            conf_pos = None
            if valid:
                below = np.where(close.iloc[sl].to_numpy() < brk_level)[0]
                conf_pos = int(below[0]) if len(below) else None

        if valid and conf_pos is not None:
            j = i0 + 1 + conf_pos
            out.at[out.index[j], "bp_valid"] = True
            out.at[out.index[j], "bp_direction"] = direction
            out.at[out.index[j], "bp_breakout_level"] = brk_level
            out.at[out.index[j], "bp_pullback_low"] = pb_extreme
            out.at[out.index[j], "bp_bars_since_breakout"] = j - i0
            out.at[out.index[j], "bp_confirmed"] = True
    return out


def pullback_entry_trigger(
    setup: PullbackSetup,
    next_bar: pd.Series,
    tick_size: float,
) -> Optional[dict]:
    """Create entry order for confirmed pullback setup."""
    if (not setup.valid) or (not setup.confirmed):
        return None
    tick = float(abs(tick_size))
    high = float(next_bar.get("high", np.nan))
    low = float(next_bar.get("low", np.nan))
    if not np.isfinite(high) or not np.isfinite(low):
        return None

    if setup.direction == "long":
        return {"side": "long", "trigger": high + tick, "stop": setup.pullback_low - tick}
    return {"side": "short", "trigger": low - tick, "stop": setup.pullback_low + tick}

