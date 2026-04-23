"""§08-01 continuous contract builder."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

import pandas as pd


AdjMethod = Literal["back", "ratio", "none"]


@dataclass
class ContinuousSeries:
    df: pd.DataFrame
    roll_events: pd.DataFrame


def detect_rollover(
    all_contracts: pd.DataFrame,
    rule: Literal["oi_max", "vol_max"] = "oi_max",
    min_days_in_contract: int = 5,
) -> pd.DataFrame:
    """Detect rollover events from active contract switches."""
    need = {"date", "contract"}
    miss = need - set(all_contracts.columns)
    if miss:
        raise KeyError(f"detect_rollover 缺少列: {miss}")
    score_col = "oi" if rule == "oi_max" else "volume"
    if score_col not in all_contracts.columns:
        raise KeyError(f"detect_rollover 缺少列: {score_col}")

    df = all_contracts.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", score_col], ascending=[True, False])
    active = df.groupby("date", as_index=False).first()[["date", "contract"]]

    events: list[dict] = []
    cur_contract = None
    cur_start = None
    for _, row in active.iterrows():
        d = pd.Timestamp(row["date"])
        c = str(row["contract"])
        if cur_contract is None:
            cur_contract = c
            cur_start = d
            continue
        if c != cur_contract:
            days_held = (d - pd.Timestamp(cur_start)).days if cur_start is not None else 999
            if days_held >= int(min_days_in_contract):
                # compute visible gap on switch day
                old_row = df[(df["date"] == d) & (df["contract"] == cur_contract)]
                new_row = df[(df["date"] == d) & (df["contract"] == c)]
                old_close = float(old_row["close"].iloc[0]) if len(old_row) else float("nan")
                new_open = float(new_row["open"].iloc[0]) if len(new_row) else float("nan")
                gap = old_close - new_open
                events.append(
                    {
                        "date": d,
                        "from_contract": cur_contract,
                        "to_contract": c,
                        "gap": gap,
                        "method": "back",
                    }
                )
                cur_contract = c
                cur_start = d
    return pd.DataFrame(events)


def build_continuous(
    daily_all_contracts: pd.DataFrame,
    symbol_root: str,
    method: AdjMethod = "back",
) -> ContinuousSeries:
    """
    Build continuous bars by active contract and optional adjustment.

    Input columns: date, contract, open, high, low, close, volume, oi.
    """
    need = {"date", "contract", "open", "high", "low", "close", "volume", "oi"}
    miss = need - set(daily_all_contracts.columns)
    if miss:
        raise KeyError(f"build_continuous 缺少列: {miss}")
    if method not in {"back", "ratio", "none"}:
        raise ValueError(f"method 非法: {method}")

    df = daily_all_contracts.copy()
    df["date"] = pd.to_datetime(df["date"])
    df = df.sort_values(["date", "oi"], ascending=[True, False])
    active = df.groupby("date", as_index=False).first()
    active = active.sort_values("date").reset_index(drop=True)

    roll_events = detect_rollover(df, rule="oi_max", min_days_in_contract=1)
    roll_map = {pd.Timestamp(r["date"]): (str(r["from_contract"]), str(r["to_contract"])) for _, r in roll_events.iterrows()}

    out = active[["date", "open", "high", "low", "close", "volume", "contract"]].copy()
    out = out.rename(columns={"contract": "contract_code"})
    out["symbol_root"] = symbol_root
    out["adj_factor"] = 1.0

    if method == "none":
        return ContinuousSeries(df=out, roll_events=roll_events)

    add_shift = 0.0
    ratio_shift = 1.0
    for i in range(1, len(out)):
        d = pd.Timestamp(out["date"].iloc[i])
        if d in roll_map:
            from_c, to_c = roll_map[d]
            old_row = df[(df["date"] == d) & (df["contract"] == from_c)]
            new_row = df[(df["date"] == d) & (df["contract"] == to_c)]
            if len(old_row) and len(new_row):
                old_close = float(old_row["close"].iloc[0])
                new_open = float(new_row["open"].iloc[0])
                if method == "back":
                    add_shift += old_close - new_open
                elif method == "ratio" and new_open != 0:
                    ratio_shift *= old_close / new_open

        if method == "back":
            for col in ("open", "high", "low", "close"):
                out.at[i, col] = float(out.at[i, col]) + add_shift
            out.at[i, "adj_factor"] = add_shift
        elif method == "ratio":
            for col in ("open", "high", "low", "close"):
                out.at[i, col] = float(out.at[i, col]) * ratio_shift
            out.at[i, "adj_factor"] = ratio_shift

    return ContinuousSeries(df=out, roll_events=roll_events)

