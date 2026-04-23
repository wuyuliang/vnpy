"""§10-02 order execution utilities."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import pandas as pd


@dataclass
class ExecConfig:
    limit_retry_seconds: int = 30
    max_retries: int = 5
    market_fallback: bool = True
    reconcile_interval_sec: int = 300


def submit_order(order, gateway, cfg: ExecConfig) -> str:
    """Submit order with bounded retries."""
    last_exc: Exception | None = None
    for _ in range(max(1, int(cfg.max_retries))):
        try:
            return str(gateway.send_order(dict(order)))
        except Exception as exc:  # pragma: no cover - retry path
            last_exc = exc
    if last_exc is not None:
        raise last_exc
    raise RuntimeError("submit_order 未成功")


def on_order_event(evt, state):
    """Update execution state from broker event."""
    t = str(evt.get("type", "")).lower()
    if t == "trade":
        state["filled"] = int(state.get("filled", 0)) + int(evt.get("lots", 0))
    elif t == "reject":
        state["rejected"] = int(state.get("rejected", 0)) + 1
    state["last_event"] = dict(evt)
    state["last_ts"] = pd.Timestamp.now(tz="UTC")
    return state


def reconcile(
    local_positions: dict,
    broker_positions: dict,
) -> list[dict]:
    """Return position differences by symbol."""
    rows: list[dict[str, Any]] = []
    symbols = set(local_positions.keys()) | set(broker_positions.keys())
    for sym in sorted(symbols):
        lv = int(local_positions.get(sym, 0))
        bv = int(broker_positions.get(sym, 0))
        if lv != bv:
            rows.append({"symbol": sym, "local": lv, "broker": bv, "delta": bv - lv})
    return rows


def kill_switch(reason: str) -> dict:
    """Return kill-switch state snapshot."""
    return {
        "enabled": True,
        "reason": str(reason),
        "ts": pd.Timestamp.now(tz="UTC"),
        "action": "cancel_all_and_block_new_orders",
    }

