"""§05-01 波动率压缩 -> 扩张 状态切换检测."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


VolTransition = Literal["compression", "expansion", "normal"]


@dataclass
class VolTransitionEvent:
    """One transition event emitted by the state machine."""

    timestamp: pd.Timestamp
    new_state: VolTransition
    old_state: VolTransition
    confidence: float

    def __post_init__(self) -> None:
        if self.new_state not in {"compression", "expansion", "normal"}:
            raise ValueError(f"new_state 非法: {self.new_state}")
        if self.old_state not in {"compression", "expansion", "normal"}:
            raise ValueError(f"old_state 非法: {self.old_state}")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence 应在 [0,1]，got {self.confidence}")


def _norm_interval(interval: str) -> str:
    key = (interval or "day").strip().lower()
    mapping = {
        "day": "day",
        "minute60": "minute60",
        "60min": "minute60",
        "minute30": "minute30",
        "30min": "minute30",
        "minute15": "minute15",
        "15min": "minute15",
        "minute5": "minute5",
        "5min": "minute5",
        "minute": "minute",
        "1min": "minute",
    }
    return mapping.get(key, key)


def _default_pct_window(interval: str) -> int:
    # 兼容六周期，不强行按自然时间对齐，保持 bar-based 策略一致性。
    return {
        "day": 252,
        "minute60": 500,
        "minute30": 800,
        "minute15": 1200,
        "minute5": 1800,
        "minute": 2500,
    }.get(interval, 252)


def _true_range(high: pd.Series, low: pd.Series, close: pd.Series) -> pd.Series:
    prev_close = close.shift(1)
    return pd.concat(
        [
            high - low,
            (high - prev_close).abs(),
            (low - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def _atr(high: pd.Series, low: pd.Series, close: pd.Series, window: int) -> pd.Series:
    tr = _true_range(high, low, close)
    return tr.ewm(alpha=1.0 / window, adjust=False, min_periods=window).mean()


def _rolling_pct_rank(series: pd.Series, window: int) -> pd.Series:
    min_periods = max(20, window // 4)

    def _rank(values: np.ndarray) -> float:
        valid = values[~np.isnan(values)]
        if len(valid) < 2:
            return np.nan
        last = values[-1]
        if np.isnan(last):
            return np.nan
        return float(np.mean(valid <= last))

    return series.rolling(window=window, min_periods=min_periods).apply(_rank, raw=True)


def _run_length(mask: pd.Series) -> pd.Series:
    val = mask.fillna(False).astype(int)
    grp = (val.diff().fillna(val) != 0).cumsum()
    return val.groupby(grp).cumsum()


def detect_vol_transition(
    df: pd.DataFrame,
    interval: str = "day",
    pct_window: int | None = None,
    persistence_bars: int = 5,
    compression_min_bars: int = 30,
    breakout_window: int = 20,
    atr_fast_window: int = 14,
    atr_slow_window: int = 60,
) -> pd.DataFrame:
    """
    识别 compression/expansion/normal 三态，并输出切换事件标记。

    返回列
    ------
    - vol_transition_state
    - vol_transition_candidate
    - vol_transition_event
    - vol_transition_confidence
    - vol_transition_age
    - atr_fast/atr_slow/atr_ratio/atr_pct/bb_width_pct
    """
    need = {"high", "low", "close"}
    miss = need - set(df.columns)
    if miss:
        raise KeyError(f"detect_vol_transition 缺少列: {miss}")
    if persistence_bars <= 0 or compression_min_bars <= 0 or breakout_window <= 1:
        raise ValueError("persistence_bars/compression_min_bars/breakout_window 非法")

    out = df.copy()
    close = out["close"].astype(float)
    high = out["high"].astype(float)
    low = out["low"].astype(float)
    itv = _norm_interval(interval)
    pct_w = int(pct_window or _default_pct_window(itv))

    atr_fast = _atr(high, low, close, atr_fast_window)
    atr_slow = _atr(high, low, close, atr_slow_window)
    atr_ratio = atr_fast / atr_slow.where(atr_slow > 0)
    atr_pct = _rolling_pct_rank(atr_fast, pct_w)

    ma20 = close.rolling(20, min_periods=10).mean()
    std20 = close.rolling(20, min_periods=10).std()
    bb_width = (2.0 * std20 * 2.0) / ma20.where(ma20 > 0)
    bb_width_pct = _rolling_pct_rank(bb_width, pct_w)

    compression_raw = (atr_pct < 0.2) & (atr_ratio < 0.7) & (bb_width_pct < 0.2)
    compression_age = _run_length(compression_raw)
    compression = compression_age >= compression_min_bars

    prev_high = high.shift(1).rolling(breakout_window, min_periods=5).max()
    prev_low = low.shift(1).rolling(breakout_window, min_periods=5).min()
    breakout_up = close > prev_high
    breakout_down = close < prev_low
    breakout_any = (breakout_up | breakout_down).fillna(False)
    breakout_recent = breakout_any.rolling(3, min_periods=1).max().astype(bool)
    expansion_raw = (atr_ratio > 1.3) & (breakout_recent | (atr_pct > 0.8))

    candidate = pd.Series("normal", index=out.index, dtype=object)
    candidate.loc[compression.fillna(False)] = "compression"
    candidate.loc[expansion_raw.fillna(False)] = "expansion"

    comp_conf = (
        ((0.2 - atr_pct) / 0.2).clip(0, 1).fillna(0) * 0.35
        + ((0.7 - atr_ratio) / 0.4).clip(0, 1).fillna(0) * 0.35
        + ((0.2 - bb_width_pct) / 0.2).clip(0, 1).fillna(0) * 0.20
        + (compression_age / compression_min_bars).clip(0, 1).fillna(0) * 0.10
    )
    breakout_mag = (
        ((close - prev_high).abs() / atr_fast.where(atr_fast > 0)).clip(0, 3) / 3
    ).fillna(0)
    exp_conf = (
        ((atr_ratio - 1.3) / 0.8).clip(0, 1).fillna(0) * 0.5
        + breakout_mag * 0.4
        + (compression_age.shift(1) / compression_min_bars).clip(0, 1).fillna(0) * 0.1
    )
    normal_conf = (1.0 - np.maximum(comp_conf, exp_conf)).clip(0, 1)
    conf_map = {
        "compression": comp_conf,
        "expansion": exp_conf,
        "normal": normal_conf,
    }

    states: list[str] = []
    events: list[int] = []
    confs: list[float] = []

    current: VolTransition = "normal"
    pending: VolTransition = current
    pending_cnt = 0
    for i, cand in enumerate(candidate.tolist()):
        cand_state: VolTransition = cand if cand in conf_map else "normal"
        if cand_state == current:
            pending = current
            pending_cnt = 0
            event = 0
        else:
            if cand_state == pending:
                pending_cnt += 1
            else:
                pending = cand_state
                pending_cnt = 1
            if pending_cnt >= persistence_bars:
                current = cand_state
                pending_cnt = 0
                pending = current
                event = 1
            else:
                event = 0
        states.append(current)
        events.append(event)
        confs.append(float(conf_map[current].iloc[i]))

    state_series = pd.Series(states, index=out.index, dtype=object)
    # 每次切换重置 age：使用 label 变化点切段后 cumsum
    grp = (state_series != state_series.shift(1)).cumsum()
    age = pd.Series(1, index=out.index).groupby(grp).cumsum()

    out["atr_fast"] = atr_fast
    out["atr_slow"] = atr_slow
    out["atr_ratio"] = atr_ratio
    out["atr_pct"] = atr_pct
    out["bb_width_pct"] = bb_width_pct
    out["vol_transition_candidate"] = candidate
    out["vol_transition_state"] = state_series
    out["vol_transition_confidence"] = pd.Series(confs, index=out.index).clip(0, 1)
    out["vol_transition_event"] = pd.Series(events, index=out.index, dtype=int)
    out["vol_transition_age"] = age.astype(int)
    return out


def rollback_if_false_switch(
    state_series: pd.Series,
    max_rollback_bars: int = 10,
    min_hold_bars: int = 5,
) -> pd.Series:
    """
    回滚短时假切换。

    规则：若某段状态长度 < min_hold_bars 且 <= max_rollback_bars，
    且其前后状态相同，则回滚为前后状态。
    """
    if max_rollback_bars <= 0 or min_hold_bars <= 0:
        raise ValueError("max_rollback_bars/min_hold_bars 应 > 0")
    s = state_series.astype(str).copy()
    values = s.tolist()
    n = len(values)
    if n < 3:
        return s

    i = 0
    while i < n:
        j = i
        while j + 1 < n and values[j + 1] == values[i]:
            j += 1
        seg_len = j - i + 1
        if (
            seg_len < min_hold_bars
            and seg_len <= max_rollback_bars
            and i > 0
            and j < n - 1
            and values[i - 1] == values[j + 1]
        ):
            fill = values[i - 1]
            for k in range(i, j + 1):
                values[k] = fill
        i = j + 1

    return pd.Series(values, index=s.index, dtype=object)
