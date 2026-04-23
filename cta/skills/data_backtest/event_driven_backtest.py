"""§08-04 event-driven backtest."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Callable

import pandas as pd


@dataclass
class EngineConfig:
    fill_rule: str = "next_open"
    stop_fill: str = "worst"
    cost_fn: Callable | None = None
    slippage_ticks: float = 1.5


def simulate_fill(
    order: dict,
    cur_bar: pd.Series,
    next_bar: pd.Series,
    cfg: EngineConfig,
) -> dict | None:
    """Simulate one order fill on next bar."""
    side = str(order.get("side", "long")).lower()
    order_type = str(order.get("order_type", "market")).lower()
    lots = int(order.get("lots", 0))
    if lots <= 0:
        return None

    if order_type == "market":
        price = float(next_bar["open"]) if cfg.fill_rule == "next_open" else float(cur_bar["close"])
        return {"side": side, "lots": lots, "price": price, "order_type": order_type}

    if order_type == "limit":
        limit_px = float(order.get("price", cur_bar["close"]))
        if float(next_bar["low"]) <= limit_px <= float(next_bar["high"]):
            return {"side": side, "lots": lots, "price": limit_px, "order_type": order_type}
        return None

    if order_type == "stop":
        stop_px = float(order.get("price", cur_bar["close"]))
        if side == "long":
            triggered = float(next_bar["high"]) >= stop_px
            fill_px = max(stop_px, float(next_bar["open"])) if cfg.stop_fill == "worst" else stop_px
        else:
            triggered = float(next_bar["low"]) <= stop_px
            fill_px = min(stop_px, float(next_bar["open"])) if cfg.stop_fill == "worst" else stop_px
        if triggered:
            return {"side": side, "lots": lots, "price": fill_px, "order_type": order_type}
    return None


def run_backtest(
    bars: pd.DataFrame,
    strategy,
    cfg: EngineConfig,
) -> dict:
    """
    Run event-driven backtest over bars.

    Strategy interface:
    on_bar(i, bar, position) -> list[order-like dict]
    """
    need = {"open", "high", "low", "close"}
    miss = need - set(bars.columns)
    if miss:
        raise KeyError(f"run_backtest 缺少列: {miss}")
    if len(bars) < 2:
        return {"trade_log": pd.DataFrame(), "equity_curve": pd.Series(dtype=float), "positions": [], "stats": {}}

    df = bars.reset_index(drop=True).copy()
    position = 0
    entry_price = 0.0
    entry_i = -1
    equity = [0.0]
    trade_rows: list[dict] = []

    for i in range(len(df) - 1):
        bar = df.iloc[i]
        nxt = df.iloc[i + 1]
        signals = strategy.on_bar(i, bar, position)
        closed_pnl = 0.0
        for sig in signals:
            fill = simulate_fill(sig, bar, nxt, cfg)
            if fill is None:
                continue
            side = str(fill["side"]).lower()
            lots = int(fill["lots"])
            px = float(fill["price"])
            if side in {"long", "short"} and position == 0:
                position = lots if side == "long" else -lots
                entry_price = px
                entry_i = i + 1
            elif side == "flat" and position != 0:
                if position > 0:
                    gross = (px - entry_price) * abs(position)
                    pos_side = "long"
                else:
                    gross = (entry_price - px) * abs(position)
                    pos_side = "short"
                cost = 0.0
                if cfg.cost_fn is not None:
                    comp = cfg.cost_fn(
                        symbol=str(sig.get("symbol", "")),
                        price=px,
                        lots=abs(position),
                        side=pos_side,
                        multiplier=float(sig.get("multiplier", 1.0)),
                        commission_rate=float(sig.get("commission_rate", 0.0)),
                        tick_size=float(sig.get("tick_size", 1.0)),
                        slippage_ticks=cfg.slippage_ticks,
                    )
                    cost = float(getattr(comp, "total", 0.0))
                net = gross - cost
                closed_pnl += net
                trade_rows.append(
                    {
                        "entry_i": entry_i,
                        "exit_i": i + 1,
                        "side": pos_side,
                        "lots": abs(position),
                        "entry_price": entry_price,
                        "exit_price": px,
                        "gross_pnl": gross,
                        "cost": cost,
                        "net_pnl": net,
                    }
                )
                position = 0
                entry_price = 0.0
                entry_i = -1
        equity.append(equity[-1] + closed_pnl)

    eq = pd.Series(equity, name="equity")
    trade_log = pd.DataFrame(trade_rows)
    stats = {"total_pnl": float(eq.iloc[-1]), "trade_count": int(len(trade_log))}
    return {"trade_log": trade_log, "equity_curve": eq, "positions": [{"position": position}], "stats": stats}

