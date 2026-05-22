"""OOT portfolio constraints helpers."""
from __future__ import annotations

from dataclasses import dataclass

import pandas as pd


@dataclass(frozen=True)
class ConstraintCaps:
    cap_daily: float
    cap_leverage: float
    cap_cash: float
    cap_week: float
    cap_symbol: float
    cap_cluster: float


def cap_notional(desired_notional: float, caps: ConstraintCaps) -> tuple[float, str]:
    """Cap desired notional by portfolio constraints and return block reason."""
    dn = max(float(desired_notional), 0.0)
    clipped = float(
        min(
            dn,
            float(caps.cap_daily),
            float(caps.cap_leverage),
            float(caps.cap_cash),
            float(caps.cap_week),
            float(caps.cap_symbol),
            float(caps.cap_cluster),
        )
    )
    if clipped > 0:
        return clipped, ""
    if float(caps.cap_symbol) <= 0:
        return 0.0, "blocked_symbol_cap"
    if float(caps.cap_cluster) <= 0:
        return 0.0, "blocked_cluster_cap"
    if float(caps.cap_week) <= 0:
        return 0.0, "blocked_weekly_budget"
    if float(caps.cap_daily) <= 0:
        return 0.0, "blocked_daily_position"
    if float(caps.cap_cash) <= 0:
        return 0.0, "blocked_margin_cash"
    if float(caps.cap_leverage) <= 0:
        return 0.0, "blocked_leverage"
    return 0.0, "blocked_portfolio_constraint"


def apply_constraints(df: pd.DataFrame) -> pd.DataFrame:
    """Apply row-wise notional capping on a DataFrame."""
    out = df.copy()
    if out.empty:
        out["capped_notional"] = pd.Series(dtype=float)
        out["constraint_reason"] = pd.Series(dtype=object)
        return out
    desired = pd.to_numeric(out.get("desired_notional", 0.0), errors="coerce").fillna(0.0)
    caps_daily = pd.to_numeric(out.get("cap_daily", 0.0), errors="coerce").fillna(0.0)
    caps_lev = pd.to_numeric(out.get("cap_leverage", 0.0), errors="coerce").fillna(0.0)
    caps_cash = pd.to_numeric(out.get("cap_cash", 0.0), errors="coerce").fillna(0.0)
    caps_week = pd.to_numeric(out.get("cap_week", 0.0), errors="coerce").fillna(0.0)
    caps_symbol = pd.to_numeric(out.get("cap_symbol", 0.0), errors="coerce").fillna(0.0)
    caps_cluster = pd.to_numeric(out.get("cap_cluster", 0.0), errors="coerce").fillna(0.0)
    capped: list[float] = []
    reasons: list[str] = []
    for dn, cd, cl, cc, cw, cs, ccl in zip(
        desired.tolist(),
        caps_daily.tolist(),
        caps_lev.tolist(),
        caps_cash.tolist(),
        caps_week.tolist(),
        caps_symbol.tolist(),
        caps_cluster.tolist(),
    ):
        v, r = cap_notional(
            float(dn),
            ConstraintCaps(
                cap_daily=float(cd),
                cap_leverage=float(cl),
                cap_cash=float(cc),
                cap_week=float(cw),
                cap_symbol=float(cs),
                cap_cluster=float(ccl),
            ),
        )
        capped.append(float(v))
        reasons.append(str(r))
    out["capped_notional"] = capped
    out["constraint_reason"] = reasons
    return out


__all__ = ["ConstraintCaps", "cap_notional", "apply_constraints"]
