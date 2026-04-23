"""§04-01 range boundary reversal."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from cta.skills.price_action._common import atr, norm_interval


@dataclass
class BoundaryReversalSetup:
    valid: bool
    side: Literal["long", "short"]
    boundary: float
    stop: float
    mid: float
    far_boundary: float


def detect_boundary_reversal(
    df: pd.DataFrame,
    range_df: pd.DataFrame,
    proximity_pct: float = 0.005,
    interval: str = "day",
) -> pd.DataFrame:
    """Detect reversal opportunities near range boundaries."""
    _ = norm_interval(interval)
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"detect_boundary_reversal 缺少列: {miss}")
    for c in ("range_upper", "range_lower"):
        if c not in range_df.columns:
            raise KeyError(f"range_df 缺少列: {c}")

    out = df.copy()
    upper = range_df["range_upper"].astype(float).reindex(out.index)
    lower = range_df["range_lower"].astype(float).reindex(out.index)
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    a = atr(high, low, close, 14).fillna(0.0)
    mid = (upper + lower) / 2.0

    near_upper = ((upper - close).abs() / close.replace(0.0, np.nan)) <= proximity_pct
    near_lower = ((close - lower).abs() / close.replace(0.0, np.nan)) <= proximity_pct

    side = pd.Series("", index=out.index, dtype=object)
    side[near_upper] = "short"
    side[near_lower] = "long"
    valid = side.isin({"long", "short"})

    stop = pd.Series(np.nan, index=out.index, dtype=float)
    stop[side == "short"] = upper[side == "short"] + 0.3 * a[side == "short"]
    stop[side == "long"] = lower[side == "long"] - 0.3 * a[side == "long"]
    boundary = pd.Series(np.nan, index=out.index, dtype=float)
    boundary[side == "short"] = upper[side == "short"]
    boundary[side == "long"] = lower[side == "long"]
    far = pd.Series(np.nan, index=out.index, dtype=float)
    far[side == "short"] = lower[side == "short"]
    far[side == "long"] = upper[side == "long"]

    out["rb_valid"] = valid.astype(bool)
    out["rb_side"] = side
    out["rb_boundary"] = boundary
    out["rb_stop"] = stop
    out["rb_mid"] = mid
    out["rb_far_boundary"] = far
    return out


def place_boundary_order(
    setup: BoundaryReversalSetup,
    tick_size: float,
) -> Optional[dict]:
    """Create range reversal limit order."""
    if not setup.valid:
        return None
    tick = abs(float(tick_size))
    if setup.side == "short":
        return {
            "side": "short",
            "limit_price": setup.boundary - tick,
            "stop": setup.stop + tick,
            "targets": [setup.mid, setup.far_boundary],
        }
    return {
        "side": "long",
        "limit_price": setup.boundary + tick,
        "stop": setup.stop - tick,
        "targets": [setup.mid, setup.far_boundary],
    }

