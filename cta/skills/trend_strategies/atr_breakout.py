"""§03-02 ATR channel breakout."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from ._common import atr, norm_interval, sma


@dataclass
class ATRChannelSignal:
    side: Literal["long", "short", "flat"]
    entry_price: float
    stop_price: float
    exit_ma: float


def compute_atr_channel(
    df: pd.DataFrame,
    ma_n: int = 20,
    atr_n: int = 14,
    k: float = 2.5,
    interval: str = "day",
) -> pd.DataFrame:
    """Compute MA +/- k*ATR channel."""
    _ = norm_interval(interval)
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"compute_atr_channel 缺少列: {miss}")
    out = df.copy()
    c = out["close"].astype(float)
    out["atr_ma"] = sma(c, ma_n)
    out["atr_value"] = atr(out["high"].astype(float), out["low"].astype(float), c, atr_n)
    out["atr_upper"] = out["atr_ma"] + float(k) * out["atr_value"]
    out["atr_lower"] = out["atr_ma"] - float(k) * out["atr_value"]
    return out


def decide_atr_channel_trade(
    df: pd.DataFrame,
    bar_idx: int,
    current_position: ATRChannelSignal | None,
    filters: dict,
) -> Optional[ATRChannelSignal]:
    """Generate entry/exit decision for ATR channel breakout."""
    _ = filters
    i = int(bar_idx)
    if i <= 0 or i >= len(df):
        return None
    row = df.iloc[i]
    prev = df.iloc[i - 1]
    close = float(row.get("close", np.nan))
    up = float(row.get("atr_upper", np.nan))
    lo = float(row.get("atr_lower", np.nan))
    ma = float(row.get("atr_ma", np.nan))
    pup = float(prev.get("atr_upper", np.nan))
    plo = float(prev.get("atr_lower", np.nan))
    pclose = float(prev.get("close", np.nan))
    if not np.isfinite(close):
        return None

    if current_position is None:
        if np.isfinite(up) and np.isfinite(pup) and close > up and pclose <= pup:
            return ATRChannelSignal(side="long", entry_price=close, stop_price=ma, exit_ma=ma)
        if np.isfinite(lo) and np.isfinite(plo) and close < lo and pclose >= plo:
            return ATRChannelSignal(side="short", entry_price=close, stop_price=ma, exit_ma=ma)
        return None

    if current_position.side == "long" and np.isfinite(ma) and close < ma:
        return ATRChannelSignal(side="flat", entry_price=close, stop_price=current_position.stop_price, exit_ma=ma)
    if current_position.side == "short" and np.isfinite(ma) and close > ma:
        return ATRChannelSignal(side="flat", entry_price=close, stop_price=current_position.stop_price, exit_ma=ma)
    return None

