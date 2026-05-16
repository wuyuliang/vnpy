"""Feature enrichment helpers used by model_pipeline."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

from cta.model.feature.training_feature_builder import build_training_feature_table
from cta.strategy.skill_tight_range_backtest import normalize_interval

logger = logging.getLogger(__name__)


def _to_num_series(df: pd.DataFrame, col: str, fill: float = np.nan) -> pd.Series:
    if col not in df.columns:
        return pd.Series([fill] * len(df), index=df.index, dtype=float)
    return pd.to_numeric(df[col], errors="coerce")


def _auto_enrich_candidate_features_for_models(
    df: pd.DataFrame,
    *,
    force_generic_fallback: bool = False,
) -> pd.DataFrame:
    """Auto-enrich fallback generic features + model-specific features."""
    out = df.copy()
    has_generic = any(str(c).startswith("generic_") for c in out.columns)

    close = _to_num_series(out, "feature_close")
    open_ = _to_num_series(out, "feature_open")
    high = _to_num_series(out, "feature_high")
    low = _to_num_series(out, "feature_low")
    volume = _to_num_series(out, "feature_volume")
    atr14 = _to_num_series(out, "feature_atr14")
    trend = _to_num_series(out, "feature_trend_score").fillna(0.0)
    breakout = _to_num_series(out, "feature_breakout_score").fillna(0.0)
    setup = _to_num_series(out, "feature_setup_quality").fillna(0.0)
    tr_range_atr = _to_num_series(out, "feature_tr_range_atr")
    side = (
        pd.Series(np.where(out.get("side", pd.Series([""] * len(out), index=out.index)).astype(str).str.lower() == "long", 1.0, -1.0), index=out.index)
        if len(out) > 0
        else pd.Series(dtype=float)
    )
    signal_code = (
        pd.Series(pd.Categorical(out.get("signal_type", pd.Series(["unknown"] * len(out), index=out.index)).astype(str)).codes.astype(float), index=out.index)
        if len(out) > 0
        else pd.Series(dtype=float)
    )

    price_range = (high - low).abs()
    safe_range = price_range.replace(0.0, np.nan)
    safe_atr = atr14.replace(0.0, np.nan)
    body_ratio = (close - open_).abs() / safe_range
    vol_ratio = price_range / safe_atr
    trend_breakout = trend * breakout

    if force_generic_fallback or not has_generic:
        out["generic_auto_close"] = close
        out["generic_auto_open"] = open_
        out["generic_auto_high"] = high
        out["generic_auto_low"] = low
        out["generic_auto_volume"] = volume
        out["generic_auto_atr14"] = atr14
        out["generic_auto_trend"] = trend
        out["generic_auto_breakout"] = breakout
        out["generic_auto_setup_quality"] = setup
        out["generic_auto_side_code"] = side
        out["generic_auto_signal_code"] = signal_code
        out["generic_auto_body_ratio"] = body_ratio.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        out["generic_auto_vol_ratio"] = vol_ratio.replace([np.inf, -np.inf], np.nan).fillna(0.0)

    if force_generic_fallback or not has_generic:
        out["generic_model_trade_setup"] = setup
        out["generic_model_trade_breakout_trend"] = trend_breakout
        out["generic_model_regime_state"] = trend + 0.15 * side
        out["generic_model_regime_volatility"] = vol_ratio.replace([np.inf, -np.inf], np.nan).fillna(0.0)
        edge_base = breakout + 0.4 * trend + 0.3 * setup
        edge_penalty = 0.4 * tr_range_atr.fillna(0.0).abs()
        out["generic_model_mfe_edge"] = edge_base - edge_penalty
        out["generic_model_mfe_side_interaction"] = out["generic_model_mfe_edge"] * side
    return out


def _build_training_feature_table_with_auto_fallback(
    *,
    candidate_df: pd.DataFrame,
    symbol: str,
    interval: str,
    feature_root: Path,
    generic_columns: Iterable[str] | None,
) -> pd.DataFrame:
    interval_norm = normalize_interval(interval)
    sym = str(symbol).upper()
    generic_dir = feature_root / interval_norm / sym
    if not generic_dir.exists():
        logger.warning(
            "generic feature dir missing for %s %s (%s), fallback to candidate-derived features",
            sym,
            interval_norm,
            generic_dir,
        )
        merged = candidate_df.copy()
    else:
        merged = build_training_feature_table(
            candidate_df=candidate_df,
            symbol=symbol,
            interval=interval,
            feature_root=feature_root,
            generic_columns=generic_columns,
        )
    has_generic = any(str(c).startswith("generic_") for c in merged.columns)
    if not has_generic:
        logger.warning(
            "generic feature dir/columns missing for %s %s, auto-enrich with candidate-derived generic/model features",
            sym,
            interval_norm,
        )
        merged = _auto_enrich_candidate_features_for_models(merged, force_generic_fallback=True)
    else:
        merged = _auto_enrich_candidate_features_for_models(merged, force_generic_fallback=False)
    return merged


__all__ = [
    "_auto_enrich_candidate_features_for_models",
    "_build_training_feature_table_with_auto_fallback",
]

