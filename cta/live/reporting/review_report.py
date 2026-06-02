"""Live vs OOT review report (signal/execution/risk attribution)."""
from __future__ import annotations

from pathlib import Path
from typing import Any

import pandas as pd


EXECUTED_STATUSES = {"executed", "filled", "closed", "stop_loss", "trailing_stop", "horizon_exit"}


def _normalize(df: pd.DataFrame, *, prefix: str) -> pd.DataFrame:
    if df.empty:
        return df.copy()
    out = df.copy()
    if "datetime" in out.columns:
        out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    if "execution_status" in out.columns:
        out["execution_status"] = out["execution_status"].astype(str).str.lower().str.strip()
    else:
        out["execution_status"] = ""
    if "net_pnl" in out.columns:
        out["net_pnl"] = pd.to_numeric(out["net_pnl"], errors="coerce").fillna(0.0)
    else:
        out["net_pnl"] = 0.0
    if "block_reason" not in out.columns:
        out["block_reason"] = ""
    out["block_reason"] = out["block_reason"].astype(str).fillna("")
    out[f"{prefix}_executed"] = out["execution_status"].isin(EXECUTED_STATUSES)
    out[f"{prefix}_pnl"] = out["net_pnl"].astype(float).where(out[f"{prefix}_executed"], 0.0)
    return out


def _join_keys(live: pd.DataFrame, oot: pd.DataFrame) -> list[str]:
    keys = ["datetime", "symbol", "exchange", "interval", "signal_type", "side"]
    return [k for k in keys if k in live.columns and k in oot.columns]


def write_review_report(
    *,
    live_trade_details: pd.DataFrame,
    oot_trade_details: pd.DataFrame,
    out_dir: Path | str,
    run_tag: str = "live",
) -> dict[str, str]:
    """Write live-vs-OOT attribution summary + per-row details."""
    out_root = Path(out_dir)
    out_root.mkdir(parents=True, exist_ok=True)

    live = _normalize(live_trade_details, prefix="live")
    oot = _normalize(oot_trade_details, prefix="oot")

    keys = _join_keys(live, oot)
    if not keys:
        live = live.reset_index().rename(columns={"index": "row_id"})
        oot = oot.reset_index().rename(columns={"index": "row_id"})
        keys = ["row_id"]

    cols = keys + ["live_executed", "live_pnl", "block_reason"]
    l = live[cols].copy()
    r = oot[keys + ["oot_executed", "oot_pnl"]].copy()
    merged = l.merge(r, on=keys, how="outer", indicator=True)
    merged["live_executed"] = merged["live_executed"].fillna(False).astype(bool)
    merged["oot_executed"] = merged["oot_executed"].fillna(False).astype(bool)
    merged["live_pnl"] = pd.to_numeric(merged["live_pnl"], errors="coerce").fillna(0.0)
    merged["oot_pnl"] = pd.to_numeric(merged["oot_pnl"], errors="coerce").fillna(0.0)
    merged["block_reason"] = merged["block_reason"].fillna("").astype(str)

    signal_gap = 0.0
    execution_gap = 0.0
    risk_gap = 0.0

    for _, row in merged.iterrows():
        src = str(row["_merge"])
        live_exec = bool(row["live_executed"])
        oot_exec = bool(row["oot_executed"])
        live_pnl = float(row["live_pnl"])
        oot_pnl = float(row["oot_pnl"])
        block_reason = str(row["block_reason"]).strip().lower()

        if src == "left_only":
            signal_gap += live_pnl
            continue
        if src == "right_only":
            signal_gap -= oot_pnl
            continue
        if live_exec and oot_exec:
            execution_gap += live_pnl - oot_pnl
            continue
        if live_exec and not oot_exec:
            execution_gap += live_pnl
            continue
        if (not live_exec) and oot_exec:
            if block_reason:
                risk_gap += 0.0 - oot_pnl
            else:
                execution_gap += 0.0 - oot_pnl

    live_total = float(live["live_pnl"].sum()) if not live.empty else 0.0
    oot_total = float(oot["oot_pnl"].sum()) if not oot.empty else 0.0
    delta = live_total - oot_total
    remainder = delta - (signal_gap + execution_gap + risk_gap)
    execution_gap += remainder

    summary = pd.DataFrame(
        [
            {
                "live_net_pnl": live_total,
                "oot_expected_net_pnl": oot_total,
                "delta_pnl": delta,
                "signal_gap_pnl": float(signal_gap),
                "execution_gap_pnl": float(execution_gap),
                "risk_gap_pnl": float(risk_gap),
                "join_key_count": len(keys),
                "rows_compared": int(len(merged)),
            }
        ]
    )

    tag = str(run_tag).strip() or "live"
    details_path = out_root / f"{tag}_live_vs_oot_review_details.csv"
    summary_path = out_root / f"{tag}_live_vs_oot_review_summary.csv"
    merged.to_csv(details_path, index=False, encoding="utf-8-sig")
    summary.to_csv(summary_path, index=False, encoding="utf-8-sig")
    return {"details": str(details_path), "summary": str(summary_path)}


__all__ = ["write_review_report"]

