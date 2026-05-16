"""Pool-building helpers for model pipeline."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Callable, Sequence

import pandas as pd

from cta.strategy.skill_tight_range_backtest import normalize_interval

logger = logging.getLogger(__name__)


def _build_pooled_feature_df(
    pool_symbols: Sequence[tuple[str, str | None]],
    *,
    interval: str,
    start_date: str,
    end_date: str,
    trade_side_mode: str,
    synthetic_periods: int,
    feature_root: Path,
    generic_columns: Any,
    build_candidate_table_fn: Callable[..., tuple[pd.DataFrame, str]],
    ensure_training_columns_fn: Callable[[pd.DataFrame], pd.DataFrame],
    build_training_feature_table_with_auto_fallback_fn: Callable[..., pd.DataFrame],
) -> tuple[pd.DataFrame, pd.DataFrame]:
    """Build pooled candidate/feature table across symbols."""
    interval_norm = normalize_interval(interval)
    cand_parts: list[pd.DataFrame] = []
    feat_parts: list[pd.DataFrame] = []
    for sym, ex in pool_symbols:
        sym_norm = str(sym).upper()
        try:
            cand_df, _ = build_candidate_table_fn(
                symbol=sym_norm,
                exchange=ex,
                interval=interval_norm,
                start_date=start_date,
                end_date=end_date,
                trade_side_mode=trade_side_mode,
                synthetic_periods=synthetic_periods,
                allow_synthetic_fallback=False,
            )
            cand_df = ensure_training_columns_fn(cand_df)
            cand_df = cand_df.copy()
            cand_df["symbol"] = sym_norm
            cand_parts.append(cand_df)
            feat_df = build_training_feature_table_with_auto_fallback_fn(
                candidate_df=cand_df,
                symbol=sym_norm,
                interval=interval_norm,
                feature_root=feature_root,
                generic_columns=generic_columns,
            )
            feat_df = ensure_training_columns_fn(feat_df)
            feat_df = feat_df.copy()
            feat_df["symbol"] = sym_norm
            feat_parts.append(feat_df)
        except Exception:
            logger.exception("pool: build real-data feature table failed for %s; skipping", sym_norm)
            continue

    if not feat_parts:
        return pd.DataFrame(), pd.DataFrame()
    pooled_cand = pd.concat(cand_parts, axis=0, ignore_index=True) if cand_parts else pd.DataFrame()
    pooled_feat = pd.concat(feat_parts, axis=0, ignore_index=True)
    if "datetime" in pooled_cand.columns:
        pooled_cand = pooled_cand.sort_values("datetime").reset_index(drop=True)
    if "datetime" in pooled_feat.columns:
        pooled_feat = pooled_feat.sort_values("datetime").reset_index(drop=True)
    return pooled_cand, pooled_feat


__all__ = ["_build_pooled_feature_df"]

