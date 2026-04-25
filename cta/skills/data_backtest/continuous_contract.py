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
    # 确保 OHLC 是 float：否则下面 loc[mask, col] = float_series 会触发 dtype 冲突
    for _col in ("open", "high", "low", "close"):
        out[_col] = out[_col].astype(float)
    out["symbol_root"] = symbol_root
    # back: 累积加法偏移（最新 bar=0）；ratio: 累积乘法因子（最新 bar=1）
    out["adj_factor"] = 0.0 if method == "back" else 1.0

    if method == "none":
        return ContinuousSeries(df=out, roll_events=roll_events)

    # 标准 back-adjust 语义：最新合约保持不变，历史 bar 被调整以平滑 roll gap。
    # 做法：收集所有 roll 事件，按【时间倒序】逐个把 date < d 的所有 bar 前移
    # （back: 加上 new_open - old_close；ratio: 乘以 new_open / old_close）。
    # 一根历史 bar 的最终调整 = 它之后发生的所有 roll 的偏移累加/累乘。
    events: list[tuple[pd.Timestamp, float, float]] = []
    for _, r in roll_events.iterrows():
        d = pd.Timestamp(r["date"])
        from_c = str(r["from_contract"])
        to_c = str(r["to_contract"])
        old_row = df[(df["date"] == d) & (df["contract"] == from_c)]
        new_row = df[(df["date"] == d) & (df["contract"] == to_c)]
        if not len(old_row) or not len(new_row):
            continue
        old_close = float(old_row["close"].iloc[0])
        new_open = float(new_row["open"].iloc[0])
        events.append((d, old_close, new_open))

    out_dates = pd.to_datetime(out["date"])
    for d, old_close, new_open in reversed(events):
        mask = out_dates < d
        if not mask.any():
            continue
        if method == "back":
            shift = new_open - old_close
            for col in ("open", "high", "low", "close"):
                out.loc[mask, col] = out.loc[mask, col].astype(float) + shift
            out.loc[mask, "adj_factor"] = out.loc[mask, "adj_factor"].astype(float) + shift
        elif method == "ratio":
            if old_close == 0:
                continue
            ratio = new_open / old_close
            for col in ("open", "high", "low", "close"):
                out.loc[mask, col] = out.loc[mask, col].astype(float) * ratio
            out.loc[mask, "adj_factor"] = out.loc[mask, "adj_factor"].astype(float) * ratio

    return ContinuousSeries(df=out, roll_events=roll_events)

