"""Position lifetime aggregation for OOT trade details."""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd


def _build_position_lifetime_table(trade_df: pd.DataFrame) -> pd.DataFrame:
    """Build one-row-per-position lifetime stats from trade details."""
    empty_cols = [
        "pos_id",
        "symbol",
        "exchange",
        "direction",
        "first_entry",
        "last_exit",
        "layer_count",
        "max_active_layers",
        "peak_notional",
    ]
    if trade_df.empty or "pos_id" not in trade_df.columns:
        return pd.DataFrame(columns=empty_cols)

    df = trade_df.copy()
    if "execution_status" in df.columns:
        df = df.loc[df["execution_status"].astype(str) == "executed"].copy()
    df = df.loc[df["pos_id"].astype(str).str.strip() != ""].copy()
    if df.empty:
        return pd.DataFrame(columns=empty_cols)

    df["entry_datetime"] = pd.to_datetime(df.get("entry_datetime", pd.NaT), errors="coerce")
    df["exit_datetime"] = pd.to_datetime(df.get("exit_datetime", pd.NaT), errors="coerce")
    df["layer_id"] = pd.to_numeric(df.get("layer_id", np.nan), errors="coerce")
    df["position_notional"] = pd.to_numeric(df.get("position_notional", np.nan), errors="coerce")

    def _active_stats(grp: pd.DataFrame) -> tuple[int, float]:
        events: list[tuple[pd.Timestamp, int, int, float]] = []
        for _, row in grp.iterrows():
            entry_ts = pd.to_datetime(row.get("entry_datetime"), errors="coerce")
            exit_ts = pd.to_datetime(row.get("exit_datetime"), errors="coerce")
            notional = pd.to_numeric(pd.Series([row.get("position_notional", np.nan)]), errors="coerce").iloc[0]
            if pd.isna(entry_ts) or pd.isna(exit_ts) or not np.isfinite(float(notional)):
                continue
            n = max(0.0, float(notional))
            events.append((pd.Timestamp(entry_ts), 1, 1, n))
            events.append((pd.Timestamp(exit_ts), 0, -1, -n))
        active_layers = 0
        active_notional = 0.0
        max_layers = 0
        peak_notional = 0.0
        for _, _, delta_layers, delta_notional in sorted(events, key=lambda x: (x[0], x[1])):
            active_layers = max(0, active_layers + int(delta_layers))
            active_notional = max(0.0, active_notional + float(delta_notional))
            max_layers = max(max_layers, active_layers)
            peak_notional = max(peak_notional, active_notional)
        return int(max_layers), float(peak_notional)

    parts: list[dict[str, Any]] = []
    for pos_id, grp in df.groupby("pos_id", dropna=False):
        grp = grp.sort_values("entry_datetime")
        max_active_layers, peak_notional = _active_stats(grp)
        parts.append(
            {
                "pos_id": str(pos_id),
                "symbol": str(grp.get("symbol", pd.Series([""])).iloc[0]),
                "exchange": str(grp.get("exchange", pd.Series([""])).iloc[0]),
                "direction": str(grp.get("side", pd.Series([""])).iloc[0]),
                "first_entry": pd.to_datetime(grp["entry_datetime"], errors="coerce").min(),
                "last_exit": pd.to_datetime(grp["exit_datetime"], errors="coerce").max(),
                "layer_count": int(pd.to_numeric(grp["layer_id"], errors="coerce").nunique(dropna=True)),
                "max_active_layers": int(max_active_layers),
                "peak_notional": float(peak_notional),
            }
        )
    return pd.DataFrame(parts)


__all__ = ["_build_position_lifetime_table"]

