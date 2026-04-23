"""§10-03 monitoring and alerting."""
from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Literal

import pandas as pd


Level = Literal["info", "warn", "critical"]


@dataclass
class Alert:
    ts: pd.Timestamp
    level: Level
    code: str
    msg: str
    payload: dict


def check_rules(state: dict) -> list[Alert]:
    """Evaluate runtime state and emit alerts."""
    ts = pd.Timestamp(state.get("ts", pd.Timestamp.now(tz="UTC")))
    alerts: list[Alert] = []

    dd = float(state.get("equity_drawdown", 0.0))
    if dd >= 0.05:
        alerts.append(Alert(ts=ts, level="critical", code="DD_CRITICAL", msg=f"drawdown={dd:.2%}", payload={"dd": dd}))
    elif dd >= 0.03:
        alerts.append(Alert(ts=ts, level="warn", code="DD_WARN", msg=f"drawdown={dd:.2%}", payload={"dd": dd}))

    pos_delta = abs(float(state.get("position_delta", 0.0)))
    if pos_delta >= 1:
        alerts.append(
            Alert(ts=ts, level="critical", code="POSITION_MISMATCH", msg=f"position_delta={pos_delta}", payload={"position_delta": pos_delta})
        )

    freshness = float(state.get("data_freshness_sec", 0.0))
    if freshness > 300:
        alerts.append(Alert(ts=ts, level="critical", code="DATA_STALE_CRIT", msg=f"stale={freshness}s", payload={"stale_sec": freshness}))
    elif freshness > 120:
        alerts.append(Alert(ts=ts, level="warn", code="DATA_STALE_WARN", msg=f"stale={freshness}s", payload={"stale_sec": freshness}))

    reject_rate = float(state.get("reject_rate_5m", 0.0))
    if reject_rate >= 0.02:
        alerts.append(Alert(ts=ts, level="warn", code="REJECT_WARN", msg=f"reject_rate_5m={reject_rate:.2%}", payload={"reject_rate_5m": reject_rate}))

    if bool(state.get("heartbeat_missed", False)):
        alerts.append(Alert(ts=ts, level="critical", code="HEARTBEAT_MISS", msg="heartbeat missed", payload={}))

    return alerts


def notify(alert: Alert, channels: list[str]) -> None:
    """Send alert to channels (placeholder implementation)."""
    _ = channels
    # keep side-effect light for research/testing
    _ = f"[{alert.level}] {alert.code}: {alert.msg}"


def heartbeat_loop(
    interval_sec: int,
    on_miss,
    max_loops: int = 1,
    miss_at: int | None = None,
) -> None:
    """
    Simple heartbeat loop.

    `miss_at` is for deterministic testing.
    """
    loops = 0
    while loops < max(1, int(max_loops)):
        if miss_at is not None and loops == int(miss_at):
            on_miss()
        if interval_sec > 0:
            time.sleep(interval_sec)
        loops += 1

