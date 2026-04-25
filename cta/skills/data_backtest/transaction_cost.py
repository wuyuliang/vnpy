"""§08-03 transaction cost model."""
from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass
class CostComponents:
    commission: float
    slippage: float
    impact: float
    total: float


def estimate_cost(
    symbol: str,
    price: float,
    lots: int,
    side: str,
    multiplier: float,
    commission_rate: float,
    tick_size: float,
    slippage_ticks: float = 1.5,
    adv: float | None = None,
) -> CostComponents:
    """Estimate commission/slippage/impact costs."""
    _ = symbol, side  # placeholder for symbol/side-specific extensions
    px = max(float(price), 0.0)
    lts = max(int(lots), 0)
    mult = max(float(multiplier), 0.0)
    notional = px * lts * mult
    commission = notional * max(float(commission_rate), 0.0)
    slippage = lts * mult * max(float(tick_size), 0.0) * max(float(slippage_ticks), 0.0)
    if adv is None or adv <= 0:
        impact = 0.0
    else:
        # simple square-root impact proxy
        impact = 0.0001 * px * math.sqrt(max(notional / float(adv), 0.0)) * lts
    total = commission + slippage + impact
    return CostComponents(commission=commission, slippage=slippage, impact=impact, total=total)


def apply_cost_to_pnl(
    trade_log: pd.DataFrame,
    cost_fn: Callable[..., CostComponents],
) -> pd.DataFrame:
    """
    Append `cost` and `net_pnl` columns to trade log.

    如果 trade_log 同时含 ``entry_price`` 与 ``exit_price``，则按完整交易
    (entry + exit) 各扣一腿成本；否则按单腿 ``price`` 估算。
    """
    out = trade_log.copy()
    costs: list[float] = []
    has_entry = "entry_price" in out.columns
    has_exit = "exit_price" in out.columns
    for _, row in out.iterrows():
        common = dict(
            symbol=str(row.get("symbol", "")),
            lots=int(row.get("lots", 0)),
            side=str(row.get("side", "long")),
            multiplier=float(row.get("multiplier", 1.0)),
            commission_rate=float(row.get("commission_rate", 0.0)),
            tick_size=float(row.get("tick_size", 1.0)),
            slippage_ticks=float(row.get("slippage_ticks", 1.5)),
            adv=float(row["adv"]) if "adv" in row and pd.notna(row["adv"]) else None,
        )
        if has_entry and has_exit:
            # 双腿：entry 用 entry_price，exit 用 exit_price
            entry_px = float(row["entry_price"])
            exit_px = float(row["exit_price"])
            comp_in = cost_fn(price=entry_px, **common)
            comp_out = cost_fn(price=exit_px, **common)
            costs.append(float(comp_in.total) + float(comp_out.total))
        else:
            # 单腿：兼容老 schema
            price = float(row.get("price", row.get("entry_price", 0.0)))
            comp = cost_fn(price=price, **common)
            costs.append(float(comp.total))
    out["cost"] = costs
    out["net_pnl"] = out.get(
        "gross_pnl", pd.Series([0.0] * len(out), index=out.index)
    ).astype(float) - out["cost"]
    return out

