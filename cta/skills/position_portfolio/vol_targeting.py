"""§07-02 vol targeting sizing."""
from __future__ import annotations

import math
from dataclasses import dataclass


@dataclass
class VolTargetResult:
    target_lots: int
    target_daily_std: float
    per_lot_daily_std: float


def vol_target_size(
    equity: float,
    atr: float,
    contract_multiplier: float,
    target_vol: float = 0.1,
    trading_days: int = 252,
) -> VolTargetResult:
    """
    Convert annual target volatility to lot count with ATR proxy.
    """
    target_daily_std = max(float(equity), 0.0) * max(float(target_vol), 0.0) / math.sqrt(max(int(trading_days), 1))
    per_lot_daily_std = max(float(atr), 0.0) * max(float(contract_multiplier), 0.0)
    if per_lot_daily_std <= 0:
        target_lots = 0
    else:
        target_lots = int(math.floor(target_daily_std / per_lot_daily_std))
    return VolTargetResult(
        target_lots=max(0, target_lots),
        target_daily_std=target_daily_std,
        per_lot_daily_std=per_lot_daily_std,
    )


def combine_sizing(
    risk_pct_lots: int,
    vol_target_lots: int,
    max_lots: int,
) -> int:
    """Take the conservative lot count among risk and vol targets."""
    lots = min(int(risk_pct_lots), int(vol_target_lots), int(max_lots))
    return max(0, lots)

