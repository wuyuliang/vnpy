"""OOT position sizing helpers."""
from __future__ import annotations

import numpy as np
import pandas as pd


def compute_contract_position(
    *,
    desired_notional: float,
    entry_price: float,
    contract_size: float = 1.0,
    lot_size: float = 1.0,
) -> tuple[float, float]:
    """Return ``(position_qty, rounded_notional)`` using lot rounding."""
    dn = max(float(desired_notional), 0.0)
    ep = float(entry_price)
    cs = float(contract_size) if float(contract_size) > 0 else 1.0
    ls = float(lot_size) if float(lot_size) > 0 else 1.0
    if not np.isfinite(ep) or ep <= 0 or dn <= 0:
        return 0.0, 0.0
    raw_qty = dn / (ep * cs)
    qty = float(np.floor(raw_qty / ls) * ls)
    if not np.isfinite(qty) or qty <= 0:
        return 0.0, 0.0
    rounded_notional = float(qty * ep * cs)
    return qty, rounded_notional


def compute_sizes(df: pd.DataFrame) -> pd.DataFrame:
    """Vectorized convenience wrapper for sizing columns on a DataFrame."""
    out = df.copy()
    if out.empty:
        out["position_qty"] = pd.Series(dtype=float)
        out["rounded_notional"] = pd.Series(dtype=float)
        return out
    desired = pd.to_numeric(out.get("desired_notional", 0.0), errors="coerce").fillna(0.0)
    entry = pd.to_numeric(out.get("entry_price", np.nan), errors="coerce")
    contract = pd.to_numeric(out.get("contract_size", 1.0), errors="coerce").fillna(1.0)
    lot = pd.to_numeric(out.get("lot_size", 1.0), errors="coerce").fillna(1.0)
    qty_list: list[float] = []
    notional_list: list[float] = []
    for dn, ep, cs, ls in zip(desired.tolist(), entry.tolist(), contract.tolist(), lot.tolist()):
        qty, ntl = compute_contract_position(
            desired_notional=float(dn),
            entry_price=float(ep),
            contract_size=float(cs),
            lot_size=float(ls),
        )
        qty_list.append(float(qty))
        notional_list.append(float(ntl))
    out["position_qty"] = qty_list
    out["rounded_notional"] = notional_list
    return out


__all__ = ["compute_contract_position", "compute_sizes"]
