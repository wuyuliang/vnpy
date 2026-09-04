"""Strict completed-bar ingestion contract for cycle_v1 research and replay."""
from __future__ import annotations

import numpy as np
import pandas as pd


REQUIRED_BAR_COLUMNS = (
    "source_calendar_date",
    "exchange_trade_date",
    "session_id",
    "bar_start",
    "bar_end",
    "feature_asof",
    "feature_sequence",
    "root_symbol",
    "vt_symbol",
    "contract_code",
    "exchange",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "open_interest",
    "pre_settlement",
    "limit_up",
    "limit_down",
    "source_path",
    "source_row",
)


def validate_bar_contract(frame: pd.DataFrame) -> None:
    missing = [name for name in REQUIRED_BAR_COLUMNS if name not in frame]
    if missing:
        raise ValueError(f"missing minimum bar columns: {','.join(missing)}")
    if frame.empty:
        raise ValueError("minimum bar contract requires at least one row")
    for name in ("bar_start", "bar_end", "feature_asof"):
        values = pd.to_datetime(frame[name], errors="raise")
        if values.dt.tz is None:
            raise ValueError(f"{name} must be timezone-aware")
        if str(values.dt.tz) != "Asia/Shanghai":
            raise ValueError(f"{name} must use Asia/Shanghai timezone")
    starts = pd.to_datetime(frame["bar_start"])
    ends = pd.to_datetime(frame["bar_end"])
    asof = pd.to_datetime(frame["feature_asof"])
    if frame.duplicated(["contract_code", "bar_end"]).any():
        raise ValueError("contract_code and bar_end must be unique")
    if not ((starts < ends) & (ends <= asof)).all():
        raise ValueError("event order requires bar_start < bar_end <= feature_asof")
    for _, positions in frame.groupby("contract_code", sort=False).groups.items():
        contract_ends = ends.loc[positions]
        if not contract_ends.is_monotonic_increasing:
            raise ValueError("bar_end must increase within each contract")

    sequence = pd.to_numeric(frame["feature_sequence"], errors="coerce")
    source_row = pd.to_numeric(frame["source_row"], errors="coerce")
    if (
        sequence.isna().any()
        or (sequence < 0).any()
        or not np.equal(sequence, np.floor(sequence)).all()
    ):
        raise ValueError("feature_sequence must be a nonnegative integer")
    if (
        source_row.isna().any()
        or (source_row < 0).any()
        or not np.equal(source_row, np.floor(source_row)).all()
    ):
        raise ValueError("source_row must be a nonnegative integer")

    numeric_columns = (
        "open", "high", "low", "close", "volume", "turnover", "open_interest",
        "pre_settlement", "limit_up", "limit_down",
    )
    numeric = frame.loc[:, numeric_columns].apply(pd.to_numeric, errors="coerce")
    if not np.isfinite(numeric.to_numpy(float)).all():
        raise ValueError("bar prices and activity must be finite")
    if not (
        (numeric["high"] >= numeric[["open", "close"]].max(axis=1))
        & (numeric["low"] <= numeric[["open", "close"]].min(axis=1))
        & (numeric["high"] >= numeric["low"])
    ).all():
        raise ValueError("OHLC values are inconsistent")
    if (numeric[["volume", "turnover", "open_interest"]] < 0).any().any():
        raise ValueError("volume, turnover, and open_interest must be nonnegative")
    if not (
        (numeric["limit_down"] < numeric["pre_settlement"])
        & (numeric["pre_settlement"] < numeric["limit_up"])
    ).all():
        raise ValueError("daily limits must enclose pre_settlement")

    required_text = (
        "session_id", "root_symbol", "vt_symbol", "contract_code", "exchange", "source_path"
    )
    if any(frame[name].isna().any() or frame[name].astype(str).str.strip().eq("").any()
           for name in required_text):
        raise ValueError("bar identity and source fields must be nonempty")
    if frame[["source_calendar_date", "exchange_trade_date"]].isna().any().any():
        raise ValueError("calendar dates must be present")


__all__ = ["REQUIRED_BAR_COLUMNS", "validate_bar_contract"]
