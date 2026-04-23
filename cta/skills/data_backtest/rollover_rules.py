"""§08-02 rollover rules and executor."""
from __future__ import annotations

from dataclasses import asdict, dataclass
from typing import Callable, Literal

import pandas as pd


@dataclass
class RolloverAction:
    date: pd.Timestamp
    from_contract: str
    to_contract: str
    lots: int
    rule: Literal["oi", "vol", "days_to_expiry"]
    estimated_cost: float


def decide_rollover(
    positions: list[dict],
    oi_snapshot: dict[str, int],
    days_to_expiry: dict[str, int],
    rule: Literal["oi", "ratio"] = "oi",
) -> list[RolloverAction]:
    """
    Decide rollover actions from current positions and active-contract snapshot.

    Rule:
    - oi: switch when another contract has higher OI and old dte < 15
    - ratio: switch when new_oi/(old_oi+new_oi) > 0.55 or old dte < 10
    """
    actions: list[RolloverAction] = []
    if not oi_snapshot:
        return actions
    active_new = max(oi_snapshot.items(), key=lambda x: x[1])[0]
    now = pd.Timestamp.now(tz="UTC")

    for pos in positions:
        old = str(pos.get("contract", ""))
        lots = int(pos.get("lots", 0))
        if not old or lots <= 0:
            continue
        if old == active_new:
            continue
        old_oi = float(oi_snapshot.get(old, 0))
        new_oi = float(oi_snapshot.get(active_new, 0))
        old_dte = int(days_to_expiry.get(old, 999))

        trigger = False
        if rule == "oi":
            trigger = (new_oi > old_oi) and (old_dte < 15)
        else:
            ratio = new_oi / max(old_oi + new_oi, 1.0)
            trigger = (ratio > 0.55) or (old_dte < 10)
        if not trigger:
            continue

        actions.append(
            RolloverAction(
                date=now,
                from_contract=old,
                to_contract=active_new,
                lots=lots,
                rule="oi" if rule == "oi" else "days_to_expiry",
                estimated_cost=float(lots) * 2.0,
            )
        )
    return actions


def execute_rollover(
    actions: list[RolloverAction],
    executor_fn: Callable[[RolloverAction], dict],
) -> pd.DataFrame:
    """Execute rollover actions via callback and return ledger."""
    rows: list[dict] = []
    for action in actions:
        result = executor_fn(action)
        row = asdict(action)
        row.update({str(k): v for k, v in dict(result).items()})
        rows.append(row)
    return pd.DataFrame(rows)
