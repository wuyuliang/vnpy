"""§07-05 portfolio allocation and order conflict resolver."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import numpy as np
import pandas as pd


AllocScheme = Literal["equal", "risk_parity", "sharpe"]


@dataclass
class AllocationPlan:
    weights: dict[str, float]
    risk_budget: dict[str, float]
    scheme: AllocScheme


def _normalize_weights(raw: dict[str, float]) -> dict[str, float]:
    total = float(sum(max(v, 0.0) for v in raw.values()))
    if total <= 0:
        n = max(len(raw), 1)
        return {k: 1.0 / n for k in raw}
    return {k: max(v, 0.0) / total for k, v in raw.items()}


def allocate_portfolio(
    strategy_pnl_panel: pd.DataFrame,
    total_risk_pct: float = 0.03,
    scheme: AllocScheme = "risk_parity",
) -> AllocationPlan:
    """Allocate strategy weights and risk budget."""
    if strategy_pnl_panel.empty:
        return AllocationPlan(weights={}, risk_budget={}, scheme=scheme)
    cols = [str(c) for c in strategy_pnl_panel.columns]

    if scheme == "equal":
        weights = {c: 1.0 / len(cols) for c in cols}
    elif scheme == "sharpe":
        raw: dict[str, float] = {}
        for c in cols:
            s = strategy_pnl_panel[c].astype(float)
            std = float(s.std(ddof=0))
            sharpe = float(s.mean()) / (std + 1e-9)
            raw[c] = max(0.0, sharpe)
        weights = _normalize_weights(raw)
    else:
        # risk parity ~= inverse volatility
        raw = {}
        for c in cols:
            std = float(strategy_pnl_panel[c].astype(float).std(ddof=0))
            raw[c] = 1.0 / max(std, 1e-9)
        weights = _normalize_weights(raw)

    total_risk = float(max(total_risk_pct, 0.0))
    risk_budget = {k: v * total_risk for k, v in weights.items()}
    return AllocationPlan(weights=weights, risk_budget=risk_budget, scheme=scheme)


def _side_sign(side: str) -> int:
    return 1 if str(side).lower() == "long" else -1


def resolve_conflict(
    new_order: dict,
    existing_positions: list[dict],
    policy: Literal["net", "block", "last_wins"] = "net",
) -> dict | None:
    """
    Resolve same-symbol signal conflicts.

    - net: convert to one net order
    - block: reject opposite-side order when position exists
    - last_wins: accept new order as-is
    """
    if policy == "last_wins":
        return dict(new_order)

    symbol = str(new_order.get("symbol", ""))
    side_new = str(new_order.get("side", "long")).lower()
    lots_new = int(new_order.get("lots", 0))
    if lots_new <= 0:
        return None

    same_symbol = [p for p in existing_positions if str(p.get("symbol", "")) == symbol]
    if not same_symbol:
        return dict(new_order)

    if policy == "block":
        for p in same_symbol:
            if str(p.get("side", "long")).lower() != side_new:
                return None
        return dict(new_order)

    net_existing = 0
    for p in same_symbol:
        net_existing += _side_sign(str(p.get("side", "long"))) * int(p.get("lots", 0))
    net_after = net_existing + _side_sign(side_new) * lots_new
    if net_after == 0:
        return None
    out = dict(new_order)
    out["side"] = "long" if net_after > 0 else "short"
    out["lots"] = abs(int(net_after))
    return out

