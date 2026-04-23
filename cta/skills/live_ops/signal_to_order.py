"""§10-01 signal to order."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


Side = Literal["long", "short", "flat"]


@dataclass
class Signal:
    ts: pd.Timestamp
    symbol: str
    side: Side
    lots: int
    reason: str
    stop_price: float | None = None
    take_profit: float | None = None


@dataclass
class Order:
    ts: pd.Timestamp
    contract: str
    side: Side
    order_type: Literal["market", "limit", "stop"]
    price: float | None
    lots: int
    open_close: Literal["open", "close", "close_today", "close_yesterday"]
    ref_signal_id: str


def _signal_id(sig: Signal) -> str:
    return f"{sig.symbol}_{sig.ts.strftime('%Y%m%d%H%M%S')}_{sig.reason}"


def orderize(
    sig: Signal,
    existing_positions: dict,
    meta: dict,
    active_contract: str,
) -> list[Order]:
    """Convert one strategy signal into broker-ready orders."""
    if sig.lots <= 0:
        return []
    ref = _signal_id(sig)
    exchange_rule = str(meta.get("exchange_rule", "")).lower()

    # Open signal
    if sig.side in {"long", "short"}:
        orders = [
            Order(
                ts=sig.ts,
                contract=active_contract,
                side=sig.side,
                order_type="market",
                price=None,
                lots=int(sig.lots),
                open_close="open",
                ref_signal_id=ref,
            )
        ]
        # attach protective stop
        if sig.stop_price is not None:
            stop_side: Side = "short" if sig.side == "long" else "long"
            orders.append(
                Order(
                    ts=sig.ts,
                    contract=active_contract,
                    side=stop_side,
                    order_type="stop",
                    price=float(sig.stop_price),
                    lots=int(sig.lots),
                    open_close="close",
                    ref_signal_id=ref,
                )
            )
        return orders

    # Flat signal -> close existing position
    pos = dict(existing_positions.get(sig.symbol, {}))
    if not pos:
        return []
    today_lots = int(pos.get("today_lots", 0))
    yesterday_lots = int(pos.get("yesterday_lots", pos.get("lots", 0) - today_lots))
    side = "short" if str(pos.get("side", "long")).lower() == "long" else "long"

    if exchange_rule == "shfe":
        if today_lots > 0:
            return [
                Order(
                    ts=sig.ts,
                    contract=active_contract,
                    side=side,  # type: ignore[arg-type]
                    order_type="market",
                    price=None,
                    lots=min(sig.lots, today_lots),
                    open_close="close_today",
                    ref_signal_id=ref,
                )
            ]
        return [
            Order(
                ts=sig.ts,
                contract=active_contract,
                side=side,  # type: ignore[arg-type]
                order_type="market",
                price=None,
                lots=min(sig.lots, max(0, yesterday_lots)),
                open_close="close_yesterday",
                ref_signal_id=ref,
            )
        ]
    return [
        Order(
            ts=sig.ts,
            contract=active_contract,
            side=side,  # type: ignore[arg-type]
            order_type="market",
            price=None,
            lots=sig.lots,
            open_close="close",
            ref_signal_id=ref,
        )
    ]

