"""OOT profit-quality diagnostics.

These helpers measure whether headline returns depend on a few outsized trades
or on capital that could not realistically be deployed at scale.
"""
from __future__ import annotations

import math
from typing import Any

import numpy as np
import pandas as pd


def _executed_trades(trades: pd.DataFrame) -> pd.DataFrame:
    if trades.empty:
        return trades.copy()
    if "execution_status" in trades.columns:
        mask = trades["execution_status"].astype(str).str.lower().eq("executed")
        if bool(mask.any()):
            return trades.loc[mask].copy()
    if "net_pnl" in trades.columns:
        return trades.loc[pd.to_numeric(trades["net_pnl"], errors="coerce").notna()].copy()
    return trades.iloc[0:0].copy()


def _annualized(total_return_pct: float, start_ts: pd.Timestamp | None, end_ts: pd.Timestamp | None) -> float:
    if start_ts is None or end_ts is None or not math.isfinite(float(total_return_pct)):
        return float("nan")
    days = max(1, int((end_ts.normalize() - start_ts.normalize()).days))
    base = 1.0 + float(total_return_pct)
    if base <= 0.0:
        return float("nan")
    return float(base ** (365.0 / float(days)) - 1.0)


def _gini(values: pd.Series) -> float:
    arr = pd.to_numeric(values, errors="coerce").fillna(0.0).abs().to_numpy(dtype=float)
    arr = arr[np.isfinite(arr)]
    if len(arr) == 0:
        return float("nan")
    total = float(arr.sum())
    if total <= 0.0:
        return 0.0
    arr.sort()
    n = len(arr)
    weights = np.arange(1, n + 1, dtype=float)
    return float((2.0 * np.sum(weights * arr) / (n * total)) - (n + 1.0) / n)


def compute_concentration_diagnostics(
    trades: pd.DataFrame,
    *,
    initial_capital: float,
    top_ns: tuple[int, ...] = (1, 5, 20),
) -> dict[str, Any]:
    """Return fat-tail and concentration diagnostics for executed OOT trades."""
    ex = _executed_trades(trades)
    pnl = pd.to_numeric(ex.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    total = float(pnl.sum())
    capital = float(initial_capital)
    start = pd.to_datetime(ex.get("datetime", pd.Series(dtype=object)), errors="coerce").min()
    end = pd.to_datetime(ex.get("datetime", pd.Series(dtype=object)), errors="coerce").max()
    start_ts = None if pd.isna(start) else pd.Timestamp(start)
    end_ts = None if pd.isna(end) else pd.Timestamp(end)

    ordered = pnl.sort_values(ascending=False).reset_index(drop=True)
    out: dict[str, Any] = {
        "trade_count": int(len(ex)),
        "total_net_pnl": total,
        "pnl_gini": _gini(pnl),
    }
    for n in top_ns:
        remaining = float(ordered.iloc[int(n):].sum()) if len(ordered) > int(n) else 0.0
        out[f"net_excl_top{int(n)}_pct"] = remaining / capital if capital > 0 else float("nan")
        if int(n) == 20:
            out["annualized_excl_top20"] = _annualized(
                remaining / capital if capital > 0 else float("nan"),
                start_ts,
                end_ts,
            )

    denom = abs(total)
    out["top1_trade_pnl_pct"] = float(ordered.iloc[0] / denom) if len(ordered) and denom > 1e-12 else float("nan")
    if "symbol" in ex.columns and denom > 1e-12:
        by_symbol = (
            pnl.groupby(ex["symbol"].astype(str), dropna=False)
            .sum()
            .sort_values(ascending=False)
        )
        out["top5_symbol_pnl_pct"] = float(by_symbol.head(5).sum() / denom)
    else:
        out["top5_symbol_pnl_pct"] = float("nan")
    out["concentration_warning"] = bool(
        (np.isfinite(out["top1_trade_pnl_pct"]) and float(out["top1_trade_pnl_pct"]) > 0.05)
        or (np.isfinite(out["top5_symbol_pnl_pct"]) and float(out["top5_symbol_pnl_pct"]) > 0.50)
    )
    return out


def compute_deployable_capital_metrics(
    trades: pd.DataFrame,
    *,
    risk_capital_multiplier: float = 2.0,
) -> dict[str, float]:
    """Compute returns against peak deployed margin rather than nominal capital."""
    ex = _executed_trades(trades)
    pnl = pd.to_numeric(ex.get("net_pnl", pd.Series(dtype=float)), errors="coerce").fillna(0.0)
    entry_margin = pd.to_numeric(ex.get("entry_margin", pd.Series(0.0, index=ex.index)), errors="coerce").fillna(0.0)
    margin_before = pd.to_numeric(
        ex.get("margin_used_before_entry", pd.Series(0.0, index=ex.index)),
        errors="coerce",
    ).fillna(0.0)
    peak_margin = float((margin_before + entry_margin).max()) if len(ex) else 0.0
    total = float(pnl.sum())
    mult = max(float(risk_capital_multiplier), 1e-12)
    return {
        "peak_margin_used": peak_margin,
        "return_on_peak_margin": total / peak_margin if peak_margin > 0 else float("nan"),
        "return_on_risk_capital": total / (peak_margin * mult) if peak_margin > 0 else float("nan"),
        "max_position_notional_after_trade": float(
            pd.to_numeric(
                ex.get("position_notional_after_trade", pd.Series(0.0, index=ex.index)),
                errors="coerce",
            ).fillna(0.0).max()
        )
        if len(ex)
        else 0.0,
    }


__all__ = ["compute_concentration_diagnostics", "compute_deployable_capital_metrics"]
