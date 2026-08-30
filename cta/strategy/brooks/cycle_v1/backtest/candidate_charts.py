"""Render auditable multi-timeframe review cards for rejected candidates."""

from __future__ import annotations

from typing import Any

import pandas as pd


_CANDIDATE_COLUMNS = {
    "candidate_id",
    "symbol",
    "contract_code",
    "setup",
    "direction",
    "cycle",
    "signal_time",
    "active_time",
    "entry",
    "stop",
    "target",
}
_REJECTION_COLUMNS = {
    "candidate_id",
    "feature_asof",
    "reason_code",
    "detail",
}
_LONG_COLUMNS = {"feature_asof", "cycle", "reason"}


def _diagnose_candidates(
    candidates: pd.DataFrame,
    rejections: pd.DataFrame,
    long_frame: pd.DataFrame,
) -> pd.DataFrame:
    """Bind each candidate rejection to the latest visible large snapshot."""
    _require_columns(candidates, _CANDIDATE_COLUMNS, "candidates")
    _require_columns(rejections, _REJECTION_COLUMNS, "rejections")
    _require_columns(long_frame, _LONG_COLUMNS, "long cycle frame")

    selected_rejections = rejections.loc[
        rejections["candidate_id"].notna()
        & rejections["reason_code"].astype(str).eq("LARGE_CYCLE_UNAVAILABLE")
    ].copy()
    selected_rejections["feature_asof"] = selected_rejections[
        "feature_asof"
    ].map(_shanghai_timestamp)

    cycles = long_frame.copy()
    cycles["feature_asof"] = cycles["feature_asof"].map(_shanghai_timestamp)
    cycles = cycles.sort_values("feature_asof", kind="stable").reset_index(drop=True)

    ordered = candidates.copy()
    ordered["signal_time"] = ordered["signal_time"].map(_shanghai_timestamp)
    ordered["active_time"] = ordered["active_time"].map(_shanghai_timestamp)
    ordered = ordered.sort_values("signal_time", kind="stable").reset_index(drop=True)

    rows: list[dict[str, Any]] = []
    for sequence, candidate in enumerate(ordered.to_dict("records"), start=1):
        candidate_id = str(candidate["candidate_id"])
        rejection_rows = selected_rejections.loc[
            selected_rejections["candidate_id"].astype(str).eq(candidate_id)
        ]
        if len(rejection_rows) != 1:
            raise ValueError(
                f"candidate {candidate_id} requires one LARGE_CYCLE_UNAVAILABLE rejection"
            )
        rejection = rejection_rows.iloc[0]
        rejection_asof = pd.Timestamp(rejection["feature_asof"])
        visible = cycles.loc[cycles["feature_asof"].le(rejection_asof)]
        if visible.empty:
            raise ValueError(f"candidate {candidate_id} has no visible large snapshot")
        snapshot = visible.iloc[-1]
        large_cycle = str(snapshot["cycle"])
        if large_cycle not in {"UNAVAILABLE", "TRANSITION"}:
            raise ValueError(
                f"candidate {candidate_id} rejection conflicts with large cycle {large_cycle}"
            )
        large_reason = str(snapshot["reason"]).strip()
        if not large_reason or large_reason.lower() == "nan":
            raise ValueError(f"candidate {candidate_id} has no large-cycle reason")
        rows.append(
            {
                **candidate,
                "sequence": sequence,
                "rejection_feature_asof": rejection_asof,
                "rejection_code": str(rejection["reason_code"]),
                "rejection_detail": str(rejection["detail"]),
                "large_cycle": large_cycle,
                "large_reason": large_reason,
                "diagnostic_source": "recomputed_30min_snapshot",
            }
        )
    return pd.DataFrame(rows)


def _require_columns(frame: pd.DataFrame, required: set[str], label: str) -> None:
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"{label} are missing columns: {','.join(missing)}")


def _shanghai_timestamp(value: object) -> pd.Timestamp:
    parsed = pd.Timestamp(value)
    if pd.isna(parsed):
        raise ValueError("candidate chart timestamp is invalid")
    if parsed.tzinfo is None:
        return parsed.tz_localize("Asia/Shanghai")
    return parsed.tz_convert("Asia/Shanghai")


__all__ = []
