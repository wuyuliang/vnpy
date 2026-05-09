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
    # 涨跌停过滤：next_bar 相对 cur_bar.close 涨跌幅 ≥ 该阈值且 high==low（一字板）时
    # 视为触及涨跌停，禁止该方向开仓；None 关闭。
    limit_move_pct: float | None = None
    # 流动性约束：单笔可成交手数上限 = floor(next_bar.volume * liquidity_ratio)；
    # None 关闭。仅当 next_bar 含 volume 列时生效。
    liquidity_ratio: float | None = None


def _hit_price_limit(
    cur_bar: pd.Series,
    next_bar: pd.Series,
    side: str,
    limit_move_pct: float | None,
) -> bool:
    """Detect one-sided price limit (一字板) blocking an opening order."""
    if limit_move_pct is None or limit_move_pct <= 0:
        return False
    try:
        prev_close = float(cur_bar["close"])
        nxt_open = float(next_bar["open"])
        nxt_high = float(next_bar["high"])
        nxt_low = float(next_bar["low"])
    except (KeyError, TypeError, ValueError):
        return False
    if prev_close <= 0:
        return False
    move = (nxt_open - prev_close) / prev_close
    one_sided = nxt_high == nxt_low
    if not one_sided:
        return False
    if side == "long" and move >= float(limit_move_pct):
        return True
    if side == "short" and move <= -float(limit_move_pct):
        return True
    return False


def _liquidity_cap(next_bar: pd.Series, liquidity_ratio: float | None) -> int | None:
    """Return max tradable lots given liquidity_ratio; None means unlimited."""
    if liquidity_ratio is None or liquidity_ratio <= 0:
        return None
    if "volume" not in next_bar.index:
        return None
    try:
        vol = float(next_bar["volume"])
    except (TypeError, ValueError):
        return None
    if vol != vol or vol <= 0:  # NaN or non-positive
        return 0
    return int(vol * float(liquidity_ratio))


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

    # 涨跌停过滤：仅对开仓方向（long/short）生效；flat 平仓不受限。
    if side in {"long", "short"} and _hit_price_limit(cur_bar, next_bar, side, cfg.limit_move_pct):
        return None

    # 流动性截断：上限为 0 直接拒绝。
    cap = _liquidity_cap(next_bar, cfg.liquidity_ratio)
    if cap is not None:
        if cap <= 0:
            return None
        lots = min(lots, cap)
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
                    common = dict(
                        symbol=str(sig.get("symbol", "")),
                        lots=abs(position),
                        side=pos_side,
                        multiplier=float(sig.get("multiplier", 1.0)),
                        commission_rate=float(sig.get("commission_rate", 0.0)),
                        tick_size=float(sig.get("tick_size", 1.0)),
                        slippage_ticks=cfg.slippage_ticks,
                    )
                    # 双腿成本：entry 用 entry_price，exit 用 px
                    comp_entry = cfg.cost_fn(price=float(entry_price), **common)
                    comp_exit = cfg.cost_fn(price=px, **common)
                    cost = float(getattr(comp_entry, "total", 0.0)) + float(
                        getattr(comp_exit, "total", 0.0)
                    )
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

