"""§02-06 micro channel / trend channel."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, Optional

import numpy as np
import pandas as pd

from ._common import norm_interval, optional_value


@dataclass
class ChannelState:
    micro_count: int
    micro_direction: Literal["up", "down", "none"]
    trend_top: float | None
    trend_bot: float | None
    touches_top: int
    touches_bot: int


def _run_count(mask: pd.Series) -> pd.Series:
    v = mask.fillna(False).astype(int)
    grp = (v.diff().fillna(v) != 0).cumsum()
    return v.groupby(grp).cumsum()


def compute_channel_state(
    df: pd.DataFrame,
    swing_n: int = 3,
    touch_lookback: int = 50,
    interval: str = "day",
) -> pd.DataFrame:
    """Compute micro-channel counts and simple trend-channel boundaries."""
    _ = norm_interval(interval)
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"compute_channel_state 缺少列: {miss}")

    out = df.copy()
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    close = out["close"].astype(float)

    higher_low = low > low.shift(1)
    lower_high = high < high.shift(1)
    up_count = _run_count(higher_low)
    dn_count = _run_count(lower_high)
    micro_count = pd.concat([up_count, dn_count], axis=1).max(axis=1).fillna(0).astype(int)
    micro_dir = pd.Series("none", index=out.index, dtype=object)
    micro_dir[up_count > dn_count] = "up"
    micro_dir[dn_count > up_count] = "down"

    trend_top = high.rolling(max(swing_n * 4, 8), min_periods=max(3, swing_n)).max()
    trend_bot = low.rolling(max(swing_n * 4, 8), min_periods=max(3, swing_n)).min()
    width = (trend_top - trend_bot).replace(0.0, np.nan)
    dist_top = (trend_top - close) / width
    dist_bot = (close - trend_bot) / width
    touch_top = (dist_top <= 0.08).astype(int).rolling(touch_lookback, min_periods=1).sum().astype(int)
    touch_bot = (dist_bot <= 0.08).astype(int).rolling(touch_lookback, min_periods=1).sum().astype(int)

    out["ch_micro_count"] = micro_count
    out["ch_micro_direction"] = micro_dir
    out["ch_trend_top"] = trend_top
    out["ch_trend_bot"] = trend_bot
    out["ch_touches_top"] = touch_top
    out["ch_touches_bot"] = touch_bot
    return out


def channel_based_trailing(
    position_side: Literal["long", "short"],
    state: ChannelState,
    last_high: float,
    last_low: float,
) -> Optional[float]:
    """
    Return trailing stop from channel state.

    long: use last_low if micro up; short symmetric.
    """
    lh = float(last_high)
    ll = float(last_low)
    if not np.isfinite(lh) or not np.isfinite(ll):
        return None

    if position_side == "long":
        if state.micro_direction == "up":
            return optional_value(ll)
        if state.trend_bot is not None:
            return optional_value(float(state.trend_bot))
        return optional_value(ll)

    if state.micro_direction == "down":
        return optional_value(lh)
    if state.trend_top is not None:
        return optional_value(float(state.trend_top))
    return optional_value(lh)

