"""Audit row constructors shared by replay components."""
from __future__ import annotations

import math
from typing import Any

import pandas as pd


def _rejection(
    root_symbol: str,
    candidate: dict[str, Any],
    reason: str,
    *,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "root_symbol": root_symbol,
        "feature_asof": candidate["signal_time"],
        "reason_code": reason,
        "candidate_id": candidate["candidate_id"],
        "risk_budget": math.nan,
        "loss_per_lot": math.nan,
        "detail": detail,
    }


def _portfolio_risk_event(
    timestamp: pd.Timestamp,
    reason: str,
    *,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "root_symbol": "ALL",
        "feature_asof": timestamp,
        "reason_code": reason,
        "candidate_id": "",
        "risk_budget": math.nan,
        "loss_per_lot": math.nan,
        "detail": detail,
    }
