"""§07-04 drawdown control."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Literal

import pandas as pd


DDLevel = Literal["normal", "mild", "moderate", "severe", "freeze"]


@dataclass
class DDState:
    dd: float
    level: DDLevel
    max_risk_multiplier: float
    freeze_new: bool


def _level_from_dd(dd: float, thresholds: tuple[float, ...]) -> DDState:
    t1, t2, t3 = thresholds
    # keep severe and freeze both available in state machine
    t4 = max(t3 + 0.04, 0.12)
    if dd < t1:
        return DDState(dd=dd, level="normal", max_risk_multiplier=1.0, freeze_new=False)
    if dd < t2:
        return DDState(dd=dd, level="mild", max_risk_multiplier=0.7, freeze_new=False)
    if dd < t3:
        return DDState(dd=dd, level="moderate", max_risk_multiplier=0.5, freeze_new=False)
    if dd < t4:
        return DDState(dd=dd, level="severe", max_risk_multiplier=0.3, freeze_new=False)
    return DDState(dd=dd, level="freeze", max_risk_multiplier=0.0, freeze_new=True)


def compute_dd_state(
    equity_curve: pd.Series,
    thresholds: tuple[float, ...] = (0.03, 0.05, 0.08),
) -> pd.DataFrame:
    """Compute drawdown state per timestamp."""
    if len(thresholds) != 3:
        raise ValueError("thresholds 需要 3 个值")
    eq = equity_curve.astype(float).copy()
    peak = eq.cummax().replace(0, pd.NA)
    dd = ((peak - eq) / peak).fillna(0.0).clip(lower=0.0)
    states = [_level_from_dd(float(x), thresholds) for x in dd.tolist()]
    out = pd.DataFrame(
        {
            "equity": eq,
            "peak": peak,
            "dd": dd,
            "level": [s.level for s in states],
            "max_risk_multiplier": [s.max_risk_multiplier for s in states],
            "freeze_new": [s.freeze_new for s in states],
        },
        index=equity_curve.index,
    )
    return out


def apply_dd_to_sizing(
    base_lots: int,
    dd_state: DDState | pd.Series,
) -> int:
    """Scale lots by drawdown risk multiplier."""
    if isinstance(dd_state, pd.Series):
        mul = float(dd_state.get("max_risk_multiplier", 1.0))
        freeze = bool(dd_state.get("freeze_new", False))
    else:
        mul = float(dd_state.max_risk_multiplier)
        freeze = bool(dd_state.freeze_new)
    if freeze:
        return 0
    return max(0, int(math.floor(int(base_lots) * max(mul, 0.0))))

