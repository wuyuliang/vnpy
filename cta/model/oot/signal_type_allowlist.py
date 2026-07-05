"""Signal-type allowlist gate for OOT evaluation."""
from __future__ import annotations

import pandas as pd

from cta.model.oot.block_reasons import BR_BLOCKED_SIGNAL_TYPE_ALLOWLIST


BLOCK_REASON = BR_BLOCKED_SIGNAL_TYPE_ALLOWLIST


def apply_signal_type_allowlist(
    df: pd.DataFrame,
    *,
    allowlist: tuple[str, ...],
    enabled: bool,
) -> pd.DataFrame:
    """Mark rows outside the configured signal-type allowlist as blocked."""
    out = df.copy()
    if out.empty or "signal_type" not in out.columns or not bool(enabled):
        return out
    allowed = {str(x).strip().lower() for x in allowlist if str(x).strip()}
    if not allowed:
        return out
    st = out["signal_type"].astype(str).str.strip().str.lower()
    blocked = ~st.isin(allowed)
    if "execution_status" not in out.columns:
        out["execution_status"] = ""
    if "block_reason" not in out.columns:
        out["block_reason"] = ""
    out.loc[blocked, "execution_status"] = BLOCK_REASON
    out.loc[blocked, "block_reason"] = BLOCK_REASON
    return out


__all__ = ["BLOCK_REASON", "apply_signal_type_allowlist"]
