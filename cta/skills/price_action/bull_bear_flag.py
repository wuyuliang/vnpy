"""§02-02 bull flag / bear flag."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from ._common import atr, norm_interval


@dataclass
class FlagSetup:
    valid: bool
    kind: Literal["bull", "bear"]
    flag_low: float
    flag_high: float
    leg_length_atr: float
    pullback_ratio: float
    structure_tag: str


def detect_flag(
    df: pd.DataFrame,
    lookback_leg: int = 30,
    lookback_pullback: int = 20,
    pullback_min: float = 0.3,
    pullback_max: float = 0.55,
    interval: str = "day",
) -> pd.DataFrame:
    """
    Detect simple bull/bear flag setup per bar.

    Output columns:
    flag_valid/flag_kind/flag_low/flag_high/flag_leg_length_atr/flag_pullback_ratio/flag_structure_tag
    """
    _ = norm_interval(interval)
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"detect_flag 缺少列: {miss}")

    out = df.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)
    a = atr(high, low, close, 14).replace(0.0, np.nan)

    leg_high = high.shift(lookback_pullback).rolling(lookback_leg, min_periods=max(5, lookback_leg // 2)).max()
    leg_low = low.shift(lookback_pullback).rolling(lookback_leg, min_periods=max(5, lookback_leg // 2)).min()
    leg_len = (leg_high - leg_low).clip(lower=0.0)
    leg_len_atr = leg_len / a

    pb_high = high.rolling(lookback_pullback, min_periods=max(3, lookback_pullback // 2)).max()
    pb_low = low.rolling(lookback_pullback, min_periods=max(3, lookback_pullback // 2)).min()
    pullback = (pb_high - pb_low).clip(lower=0.0)
    pullback_ratio = pullback / leg_len.replace(0.0, np.nan)

    trend_up = close > close.shift(lookback_leg // 2)
    trend_dn = close < close.shift(lookback_leg // 2)
    pb_ok = (pullback_ratio >= pullback_min) & (pullback_ratio <= pullback_max)

    kind = pd.Series("", index=out.index, dtype=object)
    kind[trend_up] = "bull"
    kind[trend_dn] = "bear"
    valid = pb_ok & kind.isin({"bull", "bear"}) & (leg_len_atr > 1.0)

    structure_tag = pd.Series("", index=out.index, dtype=object)
    structure_tag[(kind == "bull") & valid] = "H1"
    structure_tag[(kind == "bear") & valid] = "L1"

    out["flag_valid"] = valid.astype(bool)
    out["flag_kind"] = kind
    out["flag_low"] = pb_low
    out["flag_high"] = pb_high
    out["flag_leg_length_atr"] = leg_len_atr.fillna(0.0)
    out["flag_pullback_ratio"] = pullback_ratio.fillna(0.0)
    out["flag_structure_tag"] = structure_tag
    return out


def flag_breakout_trigger(
    setup: FlagSetup,
    next_bar: pd.Series,
    tick_size: float,
) -> Optional[dict]:
    """Return stop order for bull/bear flag breakout."""
    if not setup.valid:
        return None
    tick = float(abs(tick_size))
    high = float(next_bar.get("high", np.nan))
    low = float(next_bar.get("low", np.nan))
    if not np.isfinite(high) or not np.isfinite(low):
        return None

    if setup.kind == "bull":
        return {"side": "long", "trigger": max(setup.flag_high + tick, high), "stop": min(setup.flag_low - tick, low - tick)}
    return {"side": "short", "trigger": min(setup.flag_low - tick, low), "stop": max(setup.flag_high + tick, high + tick)}

