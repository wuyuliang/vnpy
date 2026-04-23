"""§02-04 H1/H2/L1/L2 structure detection."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from ._common import norm_interval


@dataclass
class HLSignal:
    kind: Literal["H1", "H2", "L1", "L2"]
    bar_idx: int
    reference_high: float
    reference_low: float


def detect_hl_signals(
    df: pd.DataFrame,
    interval: str = "day",
) -> pd.DataFrame:
    """
    Detect simplified H1/H2/L1/L2 events from local break attempts.

    Output columns:
    hl_kind/hl_reference_high/hl_reference_low
    """
    _ = norm_interval(interval)
    need = {"high", "low"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"detect_hl_signals 缺少列: {miss}")

    out = df.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)

    prev_high = high.shift(1)
    prev_low = low.shift(1)
    h1 = high > prev_high
    l1 = low < prev_low
    # second attempt: after an H1/L1 appeared in recent bars
    h2 = h1 & h1.shift(1).rolling(5, min_periods=1).max().fillna(False).astype(bool)
    l2 = l1 & l1.shift(1).rolling(5, min_periods=1).max().fillna(False).astype(bool)

    kind = pd.Series("", index=out.index, dtype=object)
    kind[h1] = "H1"
    kind[h2] = "H2"
    kind[l1] = "L1"
    kind[l2] = "L2"

    out["hl_kind"] = kind
    out["hl_reference_high"] = prev_high
    out["hl_reference_low"] = prev_low
    return out


def resolve_hl_entry(
    signal: HLSignal,
    next_bar: pd.Series,
    tick_size: float,
) -> Optional[dict]:
    """Resolve order by HL signal type."""
    tick = float(abs(tick_size))
    high = float(next_bar.get("high", np.nan))
    low = float(next_bar.get("low", np.nan))
    if not np.isfinite(high) or not np.isfinite(low):
        return None
    if signal.kind in {"H1", "H2"}:
        return {"side": "long", "trigger": high + tick, "stop": signal.reference_low - tick}
    return {"side": "short", "trigger": low - tick, "stop": signal.reference_high + tick}

