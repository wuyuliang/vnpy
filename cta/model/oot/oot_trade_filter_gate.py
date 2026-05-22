"""Trade-filter gate helpers for OOT execution.

The raw trade-filter probability is not comparable across clusters or
intervals. This module supports both legacy raw thresholds and
cluster+interval percentile gates while keeping the evaluator compact.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.model.oot.block_reasons import BR_BLOCKED_TRADE_FILTER
from cta.portfolio_logic.config import normalize_portfolio_interval


def _base_cluster(value: object) -> str:
    raw = str(value or "").strip().lower()
    if raw.startswith("grp_"):
        raw = raw[4:]
    if raw.startswith("cluster_"):
        raw = raw[8:]
    return raw or "other"


def _normalize_override_key(value: object) -> str:
    raw = str(value or "").strip().lower()
    for sep in (":", "/", ","):
        raw = raw.replace(sep, "|")
    parts = [p.strip() for p in raw.split("|") if p.strip()]
    if len(parts) < 2:
        return raw
    head = f"{_base_cluster(parts[0])}|{normalize_portfolio_interval(parts[1])}"
    if len(parts) == 2:
        return head
    return "|".join([head, *parts[2:]])


def _cluster_series(df: pd.DataFrame) -> pd.Series:
    for col in ("cluster_name", "cluster", "group_name", "pool_name"):
        if col in df.columns:
            raw = df[col].astype(str)
            if raw.str.strip().ne("").any():
                return raw.map(_base_cluster)
    symbols = df.get("symbol", pd.Series([""] * len(df), index=df.index)).astype(str)
    return symbols.map(infer_symbol_cluster).map(_base_cluster)


def _interval_series(df: pd.DataFrame) -> pd.Series:
    raw = df.get("interval", pd.Series([""] * len(df), index=df.index)).astype(str)
    return raw.map(normalize_portfolio_interval)


def _normalized_thresholds(raw: dict[str, float] | None) -> dict[str, float]:
    out: dict[str, float] = {}
    for key, value in (raw or {}).items():
        out[_normalize_override_key(key)] = float(value)
    return out


def _threshold_series(
    *,
    keys: pd.Series,
    default: float,
    overrides: dict[str, float] | None,
) -> pd.Series:
    override_map = _normalized_thresholds(overrides)
    return keys.map(lambda key: float(override_map.get(str(key), float(default)))).astype(float)


def _threshold_series_side_bull(
    *,
    base_threshold: pd.Series,
    keys: pd.Series,
    sides: pd.Series,
    bull_modes: pd.Series,
    overrides: dict[str, float] | None,
) -> pd.Series:
    """Resolve cluster|interval|side|bull_mode override with base fallback."""
    out = base_threshold.astype(float).copy()
    override_map = _normalized_thresholds(overrides)
    if not override_map:
        return out
    side_s = sides.astype(str).str.strip().str.lower()
    bull_s = bull_modes.astype(str).str.strip().str.lower()
    full_keys = keys.astype(str) + "|" + side_s + "|" + bull_s
    vals = full_keys.map(lambda key: override_map.get(str(key), np.nan))
    mask = pd.to_numeric(vals, errors="coerce").notna()
    out.loc[mask] = pd.to_numeric(vals.loc[mask], errors="coerce").astype(float)
    return out.astype(float)


def _percentile_score(df: pd.DataFrame, prob: pd.Series, keys: pd.Series) -> pd.Series:
    if "trade_filter_prob_pctl" in df.columns:
        pctl = pd.to_numeric(df["trade_filter_prob_pctl"], errors="coerce")
        if pctl.notna().any():
            if float(pctl.max(skipna=True)) <= 1.0:
                pctl = pctl * 100.0
            return pctl

    # Do not rank the current OOT batch here. Percentiles must be generated
    # from non-OOT calibration data during model prediction; otherwise the gate
    # leaks the OOT score distribution into evaluation.
    return pd.Series(np.nan, index=df.index, dtype=float)


def apply_trade_filter_gate(
    df: pd.DataFrame,
    *,
    cfg: Any,
    gate_by_legacy: pd.Series,
    model_block_reason: pd.Series,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Apply trade-filter gate and annotate score/threshold diagnostics."""
    if not bool(getattr(cfg, "use_trade_filter_gate", True)) or "trade_filter_prob" not in df.columns:
        return df, gate_by_legacy, model_block_reason

    out = df.copy()
    prob = pd.to_numeric(out["trade_filter_prob"], errors="coerce").fillna(0.0)
    clusters = _cluster_series(out)
    intervals = _interval_series(out)
    keys = clusters.astype(str) + "|" + intervals.astype(str)
    side = out.get("side", pd.Series([""] * len(out), index=out.index)).astype(str).str.strip().str.lower()
    bull_mode = out.get("bull_mode", pd.Series(["normal"] * len(out), index=out.index)).astype(str).str.strip().str.lower()
    mode = str(getattr(cfg, "trade_filter_gate_mode", "raw")).strip().lower()

    if mode == "cluster_interval_percentile":
        score = _percentile_score(out, prob, keys)
        base_threshold = _threshold_series(
            keys=keys,
            default=float(getattr(cfg, "trade_filter_percentile_threshold", 70.0)),
            overrides=getattr(cfg, "trade_filter_percentile_threshold_by_cluster_interval", {}),
        )
        threshold = _threshold_series_side_bull(
            base_threshold=base_threshold,
            keys=keys,
            sides=side,
            bull_modes=bull_mode,
            overrides=getattr(cfg, "trade_filter_percentile_threshold_by_cluster_interval_side_bull_mode", {}),
        )
        # attack 模式 fallback 偏移：只在没有显式 side+bull override 时生效。
        attack_long_delta = float(getattr(cfg, "trade_filter_percentile_threshold_attack_long_delta", 0.0))
        attack_short_delta = float(getattr(cfg, "trade_filter_percentile_threshold_attack_short_delta", 0.0))
        has_side_override = (
            _threshold_series_side_bull(
                base_threshold=base_threshold,
                keys=keys,
                sides=side,
                bull_modes=bull_mode,
                overrides=getattr(cfg, "trade_filter_percentile_threshold_by_cluster_interval_side_bull_mode", {}),
            )
            != base_threshold
        )
        long_attack = (bull_mode == "attack") & (side == "long") & (~has_side_override)
        short_attack = (bull_mode == "attack") & (side == "short") & (~has_side_override)
        threshold.loc[long_attack] = threshold.loc[long_attack] + attack_long_delta
        threshold.loc[short_attack] = threshold.loc[short_attack] + attack_short_delta
        threshold = threshold.clip(lower=0.0, upper=100.0)
        pass_trade = score.fillna(-np.inf) >= threshold
        out["trade_filter_prob_pctl"] = score
    else:
        score = prob
        base_threshold = _threshold_series(
            keys=keys,
            default=float(getattr(cfg, "trade_filter_threshold", 0.5)),
            overrides=getattr(cfg, "trade_filter_raw_threshold_by_cluster_interval", {}),
        )
        threshold = _threshold_series_side_bull(
            base_threshold=base_threshold,
            keys=keys,
            sides=side,
            bull_modes=bull_mode,
            overrides=getattr(cfg, "trade_filter_raw_threshold_by_cluster_interval_side_bull_mode", {}),
        )
        attack_long_delta = float(getattr(cfg, "trade_filter_raw_threshold_attack_long_delta", 0.0))
        attack_short_delta = float(getattr(cfg, "trade_filter_raw_threshold_attack_short_delta", 0.0))
        has_side_override = (
            _threshold_series_side_bull(
                base_threshold=base_threshold,
                keys=keys,
                sides=side,
                bull_modes=bull_mode,
                overrides=getattr(cfg, "trade_filter_raw_threshold_by_cluster_interval_side_bull_mode", {}),
            )
            != base_threshold
        )
        long_attack = (bull_mode == "attack") & (side == "long") & (~has_side_override)
        short_attack = (bull_mode == "attack") & (side == "short") & (~has_side_override)
        threshold.loc[long_attack] = threshold.loc[long_attack] + attack_long_delta
        threshold.loc[short_attack] = threshold.loc[short_attack] + attack_short_delta
        threshold = threshold.clip(lower=0.0, upper=1.0)
        pass_trade = score >= threshold

    out["trade_filter_cluster"] = clusters
    out["trade_filter_gate_key"] = keys
    out["trade_filter_gate_mode"] = mode
    out["trade_filter_gate_score"] = score
    out["trade_filter_gate_threshold"] = threshold
    model_block_reason.loc[~pass_trade & (model_block_reason == "")] = BR_BLOCKED_TRADE_FILTER
    gate_by_legacy = gate_by_legacy & pass_trade.astype(bool)
    return out, gate_by_legacy, model_block_reason


__all__ = ["apply_trade_filter_gate"]
