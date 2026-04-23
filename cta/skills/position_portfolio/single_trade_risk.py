"""§07-01 single trade risk sizing."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass

from cta.config.futures_meta import FUTURES_META


@dataclass
class SizingResult:
    lot_size: int
    per_lot_risk: float
    total_risk_amount: float
    skip: bool
    skip_reason: str | None


def resolve_multiplier(symbol: str) -> float:
    """
    Resolve contract multiplier from cta/config/futures_meta.py.

    Falls back to root-symbol mapping when full symbol key is absent.
    """
    sym = str(symbol).strip()
    if not sym:
        return 10.0

    for k, meta in FUTURES_META.items():
        if k.lower() == sym.lower():
            return float(meta.get("size", 10.0))

    root = re.sub(r"[^a-z]", "", sym.lower())
    if not root:
        return 10.0

    for k, meta in FUTURES_META.items():
        root_k = re.sub(r"[^a-z]", "", k.lower())
        if root and root_k.startswith(root):
            return float(meta.get("size", 10.0))

    return 10.0


def size_by_risk_pct(
    entry: float,
    stop: float,
    equity: float,
    contract_multiplier: float,
    risk_pct: float = 0.001,
    max_lots: int = 50,
) -> SizingResult:
    """
    Position size by fixed risk percentage.

    Formula:
    risk_amount = equity * risk_pct
    per_lot_risk = abs(entry-stop) * multiplier
    lot_size = floor(risk_amount / per_lot_risk)
    """
    risk_amount = max(float(equity), 0.0) * max(float(risk_pct), 0.0)
    stop_dist = abs(float(entry) - float(stop))
    per_lot_risk = stop_dist * max(float(contract_multiplier), 0.0)
    if per_lot_risk <= 0:
        return SizingResult(
            lot_size=0,
            per_lot_risk=per_lot_risk,
            total_risk_amount=risk_amount,
            skip=True,
            skip_reason="invalid_per_lot_risk",
        )
    raw_lots = int(math.floor(risk_amount / per_lot_risk))
    if raw_lots < 1:
        return SizingResult(
            lot_size=0,
            per_lot_risk=per_lot_risk,
            total_risk_amount=risk_amount,
            skip=True,
            skip_reason="lot_lt_1",
        )
    if raw_lots > int(max_lots):
        return SizingResult(
            lot_size=0,
            per_lot_risk=per_lot_risk,
            total_risk_amount=risk_amount,
            skip=True,
            skip_reason="lot_gt_max",
        )
    return SizingResult(
        lot_size=raw_lots,
        per_lot_risk=per_lot_risk,
        total_risk_amount=raw_lots * per_lot_risk,
        skip=False,
        skip_reason=None,
    )

