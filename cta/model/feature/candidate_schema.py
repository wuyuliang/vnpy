"""Candidate events schema normalization utilities."""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np
import pandas as pd

from cta.config.baseline_skill_suite_config import (
    LABEL_MAE_PENALTY,
    LABEL_THRESHOLD,
    OPPORTUNITY_CLASS_A_BREAK,
    OPPORTUNITY_CLASS_B_BREAK,
)
from cta.model.block_reasons import (
    BR_CAPACITY_BLOCKED,
    BR_EXECUTION_RULE_BLOCKED,
    BR_FILTERED_BY_RULE,
    BR_NEXT_BAR_NOT_TRIGGERED,
    BR_RISK_RULE_BLOCKED,
)

# baseline candidate_status -> sample_status 映射。
_SAMPLE_STATUS_MAP: dict[str, str] = {
    "filled": "executed",
    "filtered": "filtered_by_rule",
    "not_triggered": "not_triggered_market",
}

_VALID_SAMPLE_STATUS: frozenset[str] = frozenset(
    {
        "executed",
        "filtered_by_rule",
        "blocked_by_risk",
        "blocked_by_capacity",
        "blocked_by_execution",
        "not_triggered_market",
    }
)

_STATUS_DEFAULT_BLOCK_REASON: dict[str, str] = {
    "filtered_by_rule": BR_FILTERED_BY_RULE,
    "blocked_by_risk": BR_RISK_RULE_BLOCKED,
    "blocked_by_capacity": BR_CAPACITY_BLOCKED,
    "blocked_by_execution": BR_EXECUTION_RULE_BLOCKED,
    "not_triggered_market": BR_NEXT_BAR_NOT_TRIGGERED,
}

_CORE_COLS: tuple[str, ...] = (
    "candidate_id",
    "symbol",
    "exchange",
    "interval",
    "timeframe",
    "datetime",
    "candidate_trade_date",
    "signal_datetime",
    "setup_type",
    "signal_type",
    "direction",
    "side",
    "candidate_flag",
    "sample_status",
    "block_reason",
    "filtered_reason",
    "trigger",
    "entry_price_virtual",
    "stop_price_virtual",
    "target_price_virtual",
    "entry_price",
    "stop_price",
    "atr_warmed",
    "atr_pct_at_entry",
    "label_class",
    "future_mfe_atr",
    "future_mae_atr",
    "future_return_atr",
    "opportunity_score",
    "is_good_opportunity",
    "opportunity_class",
    "executed_flag",
    "linked_trade_id",
    "risk_block_flag",
    "capacity_block_flag",
    "execution_block_flag",
    "candidate_status",
    "is_executed",
    "is_filtered",
    "is_triggered",
)

_PRIMARY_KEY_COLS: tuple[str, ...] = (
    "symbol",
    "interval",
    "datetime",
    "setup_type",
    "direction",
)

_MISSING_REASON_TOKENS: dict[str, str] = {
    "nan": "",
    "<na>": "",
    "none": "",
    "null": "",
}


@dataclass(frozen=True)
class CandidateTrainingDatasetResult:
    """Persisted dataset artifacts."""

    dataset_dir: Path
    candidate_events_parquet: Path
    training_samples_parquet: Path
    summary_parquet: Path


def _to_float_series(df: pd.DataFrame, column: str, fill_value: float = np.nan) -> pd.Series:
    if column not in df.columns:
        return pd.Series([fill_value] * len(df), index=df.index, dtype=float)
    return pd.to_numeric(df[column], errors="coerce")


def _build_candidate_id(df: pd.DataFrame) -> pd.Series:
    dt = pd.to_datetime(df["datetime"], errors="coerce")
    if dt.isna().any():
        raise ValueError("candidate datetime contains NaT, cannot build primary key")
    dt_text = dt.dt.strftime("%Y%m%d%H%M%S")
    setup = df["setup_type"].astype(str).str.lower().str.replace(r"[^a-z0-9]+", "_", regex=True)
    direction = df["direction"].astype(str).str.lower().str.replace(r"[^a-z0-9]+", "_", regex=True)
    symbol = df["symbol"].astype(str).str.upper()
    interval = df["interval"].astype(str).str.lower()
    return symbol + "_" + interval + "_" + dt_text + "_" + setup + "_" + direction


def _assert_unique_primary_key(df: pd.DataFrame) -> None:
    dup_mask = df.duplicated(subset=list(_PRIMARY_KEY_COLS), keep=False)
    if dup_mask.any():
        sample = df.loc[dup_mask, list(_PRIMARY_KEY_COLS)].head(5).to_dict("records")
        raise ValueError(
            f"duplicate candidate primary key found: {int(dup_mask.sum())} rows "
            f"share the same (symbol, interval, datetime, setup_type, direction); sample={sample}"
        )


def _normalize_sample_status(df: pd.DataFrame) -> pd.Series:
    if "sample_status" in df.columns:
        raw = df["sample_status"].fillna("").astype(str).str.strip().str.lower().replace({"nan": ""})
    elif "candidate_status" in df.columns:
        raw = (
            df["candidate_status"]
            .fillna("")
            .astype(str)
            .str.strip()
            .str.lower()
            .replace({"nan": ""})
            .map(_SAMPLE_STATUS_MAP)
            .fillna("not_triggered_market")
        )
    else:
        raw = pd.Series(["executed"] * len(df), index=df.index)
    raw = raw.replace({"": "not_triggered_market"})
    return raw.where(raw.isin(_VALID_SAMPLE_STATUS), "not_triggered_market")


def _coerce_missing_reason(s: pd.Series) -> pd.Series:
    out = s.fillna("").astype(str).str.strip()
    return out.where(~out.str.lower().isin(_MISSING_REASON_TOKENS), "")


def _normalize_block_reason(df: pd.DataFrame, sample_status: pd.Series) -> pd.Series:
    if "block_reason" in df.columns:
        reason = _coerce_missing_reason(df["block_reason"])
    else:
        reason = pd.Series([""] * len(df), index=df.index, dtype=str)
    if "filtered_reason" in df.columns:
        fr = _coerce_missing_reason(df["filtered_reason"])
        reason = reason.where(reason != "", fr)

    for status, default_reason in _STATUS_DEFAULT_BLOCK_REASON.items():
        mask = (sample_status == status) & (reason == "")
        reason.loc[mask] = default_reason
    reason.loc[sample_status == "executed"] = ""
    return reason


def _opportunity_class(score: pd.Series) -> pd.Series:
    return pd.Series(
        np.select(
            [
                score >= OPPORTUNITY_CLASS_A_BREAK,
                score >= OPPORTUNITY_CLASS_B_BREAK,
                score >= LABEL_THRESHOLD,
            ],
            ["A", "B", "C"],
            default="D",
        ),
        index=score.index,
    )


def _empty_candidate_events_frame() -> pd.DataFrame:
    schema: dict[str, pd.Series] = {}
    for col in _CORE_COLS:
        if col in {"datetime", "signal_datetime"}:
            schema[col] = pd.Series([], dtype="datetime64[ns]")
        elif col in {
            "candidate_flag",
            "executed_flag",
            "risk_block_flag",
            "capacity_block_flag",
            "execution_block_flag",
            "is_executed",
            "is_filtered",
            "is_triggered",
            "atr_warmed",
            "label_class",
        }:
            schema[col] = pd.Series([], dtype="int64")
        elif col in {
            "trigger",
            "entry_price_virtual",
            "stop_price_virtual",
            "target_price_virtual",
            "entry_price",
            "stop_price",
            "future_mfe_atr",
            "future_mae_atr",
            "future_return_atr",
            "opportunity_score",
            "is_good_opportunity",
        }:
            schema[col] = pd.Series([], dtype="float64")
        else:
            schema[col] = pd.Series([], dtype="object")
    return pd.DataFrame(schema)


def standardize_candidate_events(candidate_df: pd.DataFrame) -> pd.DataFrame:
    """Normalize candidate table to candidate_events schema."""
    if candidate_df is None or len(candidate_df) == 0:
        return _empty_candidate_events_frame()
    if "datetime" not in candidate_df.columns:
        raise KeyError("candidate_df missing datetime")

    out = candidate_df.copy()
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).sort_values("datetime").reset_index(drop=True)
    if out.empty:
        return _empty_candidate_events_frame()
    out["candidate_trade_date"] = out["datetime"].dt.strftime("%Y-%m-%d")

    if "signal_datetime" in out.columns:
        out["signal_datetime"] = pd.to_datetime(out["signal_datetime"], errors="coerce")
    else:
        out["signal_datetime"] = out["datetime"]

    if "symbol" not in out.columns:
        out["symbol"] = "UNKNOWN"
    out["symbol"] = out["symbol"].astype(str).str.upper()

    if "exchange" not in out.columns:
        out["exchange"] = ""
    out["exchange"] = out["exchange"].astype(str).str.upper()

    if "interval" not in out.columns:
        out["interval"] = "day"
    out["interval"] = out["interval"].astype(str).str.lower()
    out["timeframe"] = out["interval"]

    if "setup_type" in out.columns:
        out["setup_type"] = out["setup_type"].astype(str)
    elif "signal_type" in out.columns:
        out["setup_type"] = out["signal_type"].astype(str)
    else:
        out["setup_type"] = "unknown"
    if "signal_type" not in out.columns:
        out["signal_type"] = out["setup_type"]
    out["signal_type"] = out["signal_type"].astype(str)

    if "direction" in out.columns:
        out["direction"] = out["direction"].astype(str).str.lower()
    elif "side" in out.columns:
        out["direction"] = out["side"].astype(str).str.lower()
    else:
        out["direction"] = "long"
    out["side"] = out["direction"]
    out["candidate_flag"] = 1

    sample_status = _normalize_sample_status(out)
    out["sample_status"] = sample_status
    out["candidate_status"] = np.select(
        [sample_status == "executed", sample_status == "filtered_by_rule"],
        ["filled", "filtered"],
        default="not_triggered",
    )
    out["block_reason"] = _normalize_block_reason(out, sample_status=sample_status)
    out["filtered_reason"] = out["block_reason"].where(sample_status == "filtered_by_rule", "")

    trigger = _to_float_series(out, "trigger")
    entry_price = _to_float_series(out, "entry_price")
    stop_price = _to_float_series(out, "stop_price")
    feature_close = _to_float_series(out, "feature_close")
    close_px = _to_float_series(out, "close")

    entry_virtual = entry_price.copy()
    entry_virtual = entry_virtual.where(entry_virtual.notna(), trigger)
    entry_virtual = entry_virtual.where(entry_virtual.notna(), feature_close)
    entry_virtual = entry_virtual.where(entry_virtual.notna(), close_px)
    out["entry_price_virtual"] = entry_virtual
    out["stop_price_virtual"] = stop_price
    if "target_price_virtual" not in out.columns:
        out["target_price_virtual"] = np.nan

    out["entry_price"] = entry_price
    out["stop_price"] = stop_price
    out["trigger"] = trigger

    atr_entry = _to_float_series(out, "feature_atr14")
    if atr_entry.isna().all():
        atr_entry = _to_float_series(out, "atr14")
    if atr_entry.isna().all():
        atr_entry = _to_float_series(out, "atr_14")
    out["atr_pct_at_entry"] = (atr_entry / entry_virtual.replace(0, np.nan)).astype(float)

    mfe = _to_float_series(out, "future_mfe_atr")
    mae = _to_float_series(out, "future_mae_atr")
    if "atr_warmed" in out.columns:
        atr_warmed = pd.to_numeric(out["atr_warmed"], errors="coerce").fillna(1).astype(int)
    else:
        atr_warmed = pd.Series([1] * len(out), index=out.index, dtype=int)
    out["atr_warmed"] = atr_warmed

    label_known_mask = (atr_warmed == 1) & mfe.notna() & mae.notna()
    score = mfe - LABEL_MAE_PENALTY * mae
    score_masked = score.where(label_known_mask)
    out["future_mfe_atr"] = mfe.where(atr_warmed == 1)
    out["future_mae_atr"] = mae.where(atr_warmed == 1)
    out["opportunity_score"] = score_masked
    out["is_good_opportunity"] = np.where(label_known_mask & (score > LABEL_THRESHOLD), 1, 0).astype(int)
    bucket = _opportunity_class(score_masked.fillna(LABEL_THRESHOLD - 1))
    out["opportunity_class"] = np.where(label_known_mask, bucket, "U")

    if "future_pnl_atr" in out.columns:
        out["future_return_atr"] = pd.to_numeric(out["future_pnl_atr"], errors="coerce").where(label_known_mask)
    elif "future_return_atr" in out.columns:
        out["future_return_atr"] = pd.to_numeric(out["future_return_atr"], errors="coerce")
    else:
        out["future_return_atr"] = np.nan

    if "label_class" not in out.columns:
        out["label_class"] = 0
    out["label_class"] = pd.to_numeric(out["label_class"], errors="coerce").fillna(0).astype(int)

    if "executed_flag" not in out.columns:
        if "is_executed" in out.columns:
            out["executed_flag"] = pd.to_numeric(out["is_executed"], errors="coerce").fillna(0).astype(int)
        else:
            out["executed_flag"] = (sample_status == "executed").astype(int)
    out["is_executed"] = pd.to_numeric(out["executed_flag"], errors="coerce").fillna(0).astype(int)

    out["risk_block_flag"] = (sample_status == "blocked_by_risk").astype(int)
    out["capacity_block_flag"] = (sample_status == "blocked_by_capacity").astype(int)
    out["execution_block_flag"] = (sample_status == "blocked_by_execution").astype(int)
    out["is_filtered"] = (sample_status == "filtered_by_rule").astype(int)
    out["is_triggered"] = (sample_status == "executed").astype(int)
    if "linked_trade_id" not in out.columns:
        out["linked_trade_id"] = ""

    _assert_unique_primary_key(out)
    if "candidate_id" in out.columns:
        existing_id = out["candidate_id"].astype(str).str.strip()
        if existing_id.replace({"nan": ""}).eq("").any():
            out["candidate_id"] = _build_candidate_id(out)
    else:
        out["candidate_id"] = _build_candidate_id(out)

    trailing = [c for c in out.columns if c not in _CORE_COLS]
    return out[list(_CORE_COLS) + trailing].copy()


__all__ = [
    "_CORE_COLS",
    "CandidateTrainingDatasetResult",
    "standardize_candidate_events",
]
