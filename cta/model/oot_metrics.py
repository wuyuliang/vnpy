"""OOT metric utility functions."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.config.symbol_cluster_config import infer_symbol_roll_cost_pct


def _max_drawdown_from_return_series(ret: pd.Series) -> float:
    values = pd.to_numeric(ret, errors="coerce").fillna(0.0)
    if values.empty:
        return float("nan")
    equity = values.cumsum()
    running_max = equity.cummax()
    drawdown = equity - running_max
    return float(drawdown.min())


def _count_roll_dates_between(
    entry_ts: pd.Timestamp,
    exit_ts: pd.Timestamp,
    roll_day_of_month: int = 14,
) -> int:
    """Count how many roll-over dates the holding period covers."""
    ent = pd.to_datetime(entry_ts, errors="coerce")
    exi = pd.to_datetime(exit_ts, errors="coerce")
    if pd.isna(ent) or pd.isna(exi) or exi <= ent:
        return 0
    day = int(roll_day_of_month)
    if day < 1 or day > 28:
        day = 14
    count = 0
    cur = pd.Timestamp(year=ent.year, month=ent.month, day=day)
    if cur <= ent:
        cur = cur + pd.DateOffset(months=1)
    while cur <= exi:
        count += 1
        cur = cur + pd.DateOffset(months=1)
    return int(count)


def _calc_roll_cost(
    *,
    symbol: str,
    notional: float,
    entry_ts: pd.Timestamp,
    exit_ts: pd.Timestamp,
    cfg: OotEvaluationConfig,
) -> float:
    """Estimate roll-over cost over the holding period."""
    if not bool(getattr(cfg, "use_roll_cost", False)):
        return 0.0
    if not np.isfinite(notional) or float(notional) <= 0.0:
        return 0.0
    ent = pd.to_datetime(entry_ts, errors="coerce")
    exi = pd.to_datetime(exit_ts, errors="coerce")
    if pd.isna(ent) or pd.isna(exi) or exi <= ent:
        return 0.0
    annual_pct = infer_symbol_roll_cost_pct(
        str(symbol),
        default_pct=float(getattr(cfg, "default_roll_cost_pct_per_year", 0.05) or 0.05),
    )
    if not np.isfinite(annual_pct) or float(annual_pct) <= 0.0:
        return 0.0
    roll_day = int(getattr(cfg, "roll_cost_day_of_month", 14) or 14)
    n_rolls = _count_roll_dates_between(ent, exi, roll_day_of_month=roll_day)
    if n_rolls <= 0:
        return 0.0
    per_roll_pct = float(annual_pct) / 12.0
    return float(notional) * per_roll_pct * float(n_rolls)


__all__ = [
    "_max_drawdown_from_return_series",
    "_count_roll_dates_between",
    "_calc_roll_cost",
]

