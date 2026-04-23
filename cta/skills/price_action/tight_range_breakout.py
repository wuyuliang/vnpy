"""§02-01 tight range breakout."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

import numpy as np
import pandas as pd

from ._common import atr, infer_bias, norm_interval


@dataclass
class TightRangeSetup:
    valid: bool
    upper: float
    lower: float
    range_atr: float
    count: int
    direction_bias: int  # -1/0/1


def detect_tight_range(
    df: pd.DataFrame,
    lookback: int = 10,
    alpha: float = 1.5,
    min_count: int = 5,
    interval: str = "day",
) -> pd.DataFrame:
    """
    Detect tight-range windows and breakout-ready levels.

    Output columns:
    tr_valid/tr_upper/tr_lower/tr_range_atr/tr_count/tr_direction_bias
    """
    _ = norm_interval(interval)
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"detect_tight_range 缺少列: {miss}")
    if lookback <= 1:
        raise ValueError("lookback 应 > 1")

    out = df.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)
    atr14 = atr(high, low, close, n=14).replace(0.0, np.nan)

    upper = high.shift(1).rolling(lookback, min_periods=max(3, lookback // 2)).max()
    lower = low.shift(1).rolling(lookback, min_periods=max(3, lookback // 2)).min()
    close_upper = close.shift(1).rolling(lookback, min_periods=max(3, lookback // 2)).max()
    close_lower = close.shift(1).rolling(lookback, min_periods=max(3, lookback // 2)).min()
    range_h_hl = (upper - lower).clip(lower=0.0)
    range_h_close = (close_upper - close_lower).clip(lower=0.0) * 1.2
    range_h = pd.concat([range_h_hl, range_h_close], axis=1).min(axis=1)
    range_atr = range_h / atr14

    tight_raw = range_atr < float(alpha)
    count = tight_raw.astype(int).rolling(lookback, min_periods=1).sum().astype(int)
    valid = (tight_raw.fillna(False)) & (count >= int(min_count))
    bias = infer_bias(close, lookback=max(3, lookback // 2))

    out["tr_valid"] = valid.astype(bool)
    out["tr_upper"] = upper
    out["tr_lower"] = lower
    out["tr_range_atr"] = range_atr
    out["tr_count"] = count
    out["tr_direction_bias"] = bias
    return out


def resolve_breakout_trigger(
    setup: TightRangeSetup,
    bar: pd.Series,
    tick_size: float,
) -> Optional[dict]:
    """Resolve breakout direction and stop order from signal bar."""
    if (not setup.valid) or setup.upper <= setup.lower:
        return None
    tick = float(abs(tick_size))
    close = float(bar.get("close", np.nan))
    high = float(bar.get("high", np.nan))
    low = float(bar.get("low", np.nan))
    if not np.isfinite(close) or not np.isfinite(high) or not np.isfinite(low):
        return None

    if close > setup.upper:
        return {
            "side": "long",
            "trigger": high + tick,
            "stop": low - tick,
        }
    if close < setup.lower:
        return {
            "side": "short",
            "trigger": low - tick,
            "stop": high + tick,
        }

    if setup.direction_bias > 0:
        return {"side": "long", "trigger": setup.upper + tick, "stop": setup.lower - tick}
    if setup.direction_bias < 0:
        return {"side": "short", "trigger": setup.lower - tick, "stop": setup.upper + tick}
    return None
