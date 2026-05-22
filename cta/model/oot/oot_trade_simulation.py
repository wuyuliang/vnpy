"""OOT trade simulation helpers."""
from __future__ import annotations

import pandas as pd


def simulate_trades(
    df: pd.DataFrame,
    *,
    initial_capital: float = 1_000_000.0,
    return_column: str = "trade_return_pct",
    status_column: str = "execution_status",
) -> pd.DataFrame:
    """Simple equity walk-forward on executed trades.

    This helper is intentionally lightweight and deterministic for unit tests and
    report post-processing scripts.
    """
    out = df.copy()
    if out.empty:
        out["equity_before"] = pd.Series(dtype=float)
        out["equity_after"] = pd.Series(dtype=float)
        out["net_pnl"] = pd.Series(dtype=float)
        return out
    cap = float(initial_capital)
    out["equity_before"] = float("nan")
    out["equity_after"] = float("nan")
    out["net_pnl"] = 0.0
    ret = pd.to_numeric(out.get(return_column, 0.0), errors="coerce").fillna(0.0)
    status = out.get(status_column, pd.Series([""] * len(out), index=out.index)).astype(str)
    for idx in out.index:
        out.at[idx, "equity_before"] = float(cap)
        if str(status.loc[idx]) == "executed":
            pnl = float(cap * float(ret.loc[idx]))
            cap = float(cap + pnl)
            out.at[idx, "net_pnl"] = pnl
        else:
            out.at[idx, "net_pnl"] = 0.0
        out.at[idx, "equity_after"] = float(cap)
    return out


__all__ = ["simulate_trades"]
