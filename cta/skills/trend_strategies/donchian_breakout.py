"""§03-01 Donchian breakout."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from ._common import atr, norm_interval


@dataclass
class DonchianSignal:
    side: Literal["long", "short", "flat"]
    entry_price: float
    stop_price: float
    exit_price: float
    unit_count: int


def compute_donchian(
    df: pd.DataFrame,
    n_entry: int = 55,
    n_exit: int = 20,
    interval: str = "day",
) -> pd.DataFrame:
    """Add Donchian entry/exit channels."""
    _ = norm_interval(interval)
    need = {"high", "low"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"compute_donchian 缺少列: {miss}")
    out = df.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    out["don_upper_entry"] = high.shift(1).rolling(n_entry, min_periods=max(5, n_entry // 2)).max()
    out["don_lower_entry"] = low.shift(1).rolling(n_entry, min_periods=max(5, n_entry // 2)).min()
    out["don_upper_exit"] = high.shift(1).rolling(n_exit, min_periods=max(3, n_exit // 2)).max()
    out["don_lower_exit"] = low.shift(1).rolling(n_exit, min_periods=max(3, n_exit // 2)).min()
    out["don_atr20"] = atr(high, low, out["close"].astype(float), n=20)
    return out


def decide_donchian_trade(
    df_with_donchian: pd.DataFrame,
    bar_idx: int,
    current_position: DonchianSignal | None,
    filters: dict,
    tick_size: float,
) -> Optional[DonchianSignal]:
    """Generate Donchian trade signal at bar_idx."""
    _ = filters
    i = int(bar_idx)
    if i <= 0 or i >= len(df_with_donchian):
        return None
    row = df_with_donchian.iloc[i]
    close = float(row.get("close", np.nan))
    ue = float(row.get("don_upper_entry", np.nan))
    le = float(row.get("don_lower_entry", np.nan))
    ux = float(row.get("don_upper_exit", np.nan))
    lx = float(row.get("don_lower_exit", np.nan))
    a = float(row.get("don_atr20", np.nan))
    tick = abs(float(tick_size))
    if not np.isfinite(close):
        return None

    if current_position is None:
        if np.isfinite(ue) and close > ue:
            stop = close - max(2.0 * a, tick)
            return DonchianSignal(side="long", entry_price=close + tick, stop_price=stop, exit_price=lx if np.isfinite(lx) else close - tick, unit_count=1)
        if np.isfinite(le) and close < le:
            stop = close + max(2.0 * a, tick)
            return DonchianSignal(side="short", entry_price=close - tick, stop_price=stop, exit_price=ux if np.isfinite(ux) else close + tick, unit_count=1)
        return None

    if current_position.side == "long" and np.isfinite(lx) and close < lx:
        return DonchianSignal(side="flat", entry_price=close, stop_price=current_position.stop_price, exit_price=close, unit_count=current_position.unit_count)
    if current_position.side == "short" and np.isfinite(ux) and close > ux:
        return DonchianSignal(side="flat", entry_price=close, stop_price=current_position.stop_price, exit_price=close, unit_count=current_position.unit_count)
    return None

