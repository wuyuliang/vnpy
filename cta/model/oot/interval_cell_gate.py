"""Static interval/cell gates for OOT candidate evaluation."""
from __future__ import annotations

import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.model.oot.block_reasons import BR_BLOCKED_INTERVAL_CELL_GATE
from cta.portfolio_logic.config import normalize_portfolio_interval


BLOCK_REASON = BR_BLOCKED_INTERVAL_CELL_GATE


def build_cell_key(row: pd.Series) -> str:
    """Return ``cluster|signal_type|interval`` for a candidate row."""
    cluster = str(
        row.get("cluster_name")
        or row.get("trade_filter_cluster")
        or row.get("cluster")
        or ""
    ).strip().lower()
    if not cluster:
        cluster = str(infer_symbol_cluster(str(row.get("symbol", "")))).strip().lower()
    signal_type = str(row.get("signal_type", "")).strip().lower()
    interval = normalize_portfolio_interval(row.get("interval", ""))
    return f"{cluster}|{signal_type}|{interval}"


def apply_static_interval_cell_gate(
    df: pd.DataFrame,
    *,
    disabled_intervals: tuple[str, ...],
    disabled_cells: tuple[str, ...],
    enabled_cells: tuple[str, ...],
) -> pd.DataFrame:
    """Block disabled intervals/cells and optionally allow only listed 30min cells."""
    out = df.copy()
    if out.empty:
        return out
    disabled_interval_set = {
        normalize_portfolio_interval(x) for x in disabled_intervals if str(x).strip()
    }
    disabled_cell_set = {str(x).strip().lower() for x in disabled_cells if str(x).strip()}
    enabled_cell_set = {str(x).strip().lower() for x in enabled_cells if str(x).strip()}
    if not disabled_interval_set and not disabled_cell_set and not enabled_cell_set:
        return out
    if "execution_status" not in out.columns:
        out["execution_status"] = ""
    if "block_reason" not in out.columns:
        out["block_reason"] = ""
    intervals = out.get("interval", pd.Series([""] * len(out), index=out.index)).map(
        normalize_portfolio_interval
    )
    cell_keys = out.apply(build_cell_key, axis=1).astype(str).str.lower()
    blocked = intervals.isin(disabled_interval_set) | cell_keys.isin(disabled_cell_set)
    if enabled_cell_set:
        blocked = blocked | ((intervals == "30min") & ~cell_keys.isin(enabled_cell_set))
    out.loc[blocked, "execution_status"] = BLOCK_REASON
    out.loc[blocked, "block_reason"] = BLOCK_REASON
    return out


__all__ = ["BLOCK_REASON", "apply_static_interval_cell_gate", "build_cell_key"]
