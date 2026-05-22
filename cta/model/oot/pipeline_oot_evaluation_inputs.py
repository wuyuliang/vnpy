"""Input preprocessing helpers for OOT real-execution evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.config.symbol_disable import mask_disabled_rows
from cta.model.oot.pipeline_oot_evaluation_base import logger
from cta.portfolio_logic.config import normalize_portfolio_interval


def resolve_per_row_intrabar_stop_pct(
    df: pd.DataFrame,
    cfg: OotEvaluationConfig,
) -> np.ndarray:
    """Resolve intrabar stop pct by row from cluster+interval overrides."""
    default = float(getattr(cfg, "intrabar_stop_loss_pct", 0.01))
    overrides_raw = dict(getattr(cfg, "intrabar_stop_loss_pct_by_cluster_interval", {}) or {})
    overrides: dict[str, float] = {}
    for raw_key, raw_val in overrides_raw.items():
        parts = str(raw_key).strip().lower().split("|")
        if len(parts) != 2 or not parts[0] or not parts[1]:
            continue
        norm_key = f"{parts[0]}|{normalize_portfolio_interval(parts[1])}"
        overrides[norm_key] = float(raw_val)
    n = len(df)
    if n == 0:
        return np.zeros(0, dtype=float)
    out = np.full(n, default, dtype=float)
    if not overrides:
        return out
    symbols = df.get("symbol", pd.Series([""] * n, index=df.index)).astype(str).str.upper().tolist()
    intervals_raw = df.get("interval", pd.Series([""] * n, index=df.index)).astype(str).tolist()
    for i, (sym, itv_raw) in enumerate(zip(symbols, intervals_raw)):
        cluster = str(infer_symbol_cluster(sym) or "").lower()
        if not cluster:
            continue
        itv = normalize_portfolio_interval(itv_raw)
        if not itv:
            continue
        key = f"{cluster}|{itv}"
        if key in overrides:
            out[i] = float(overrides[key])
    return out


def attach_bull_mode_columns(df: pd.DataFrame, cfg: OotEvaluationConfig) -> pd.DataFrame:
    """Attach bull_strength_proxy and bull_mode for scenario-aware gates."""
    out = df.copy()
    score_col = str(getattr(cfg, "bull_strength_score_column", "bull_strength_score"))
    if score_col in out.columns:
        score = pd.to_numeric(out[score_col], errors="coerce")
        score = score.where(score <= 1.0, score / 100.0)
        score_pct = score * 100.0
    elif "trade_filter_prob_pctl" in out.columns:
        score_pct = pd.to_numeric(out["trade_filter_prob_pctl"], errors="coerce")
        score_pct = score_pct.where(score_pct > 1.0, score_pct * 100.0)
    else:
        raw_src = out.get("trade_filter_prob")
        if raw_src is None:
            raw_src = pd.Series([0.0] * len(out), index=out.index, dtype=float)
        raw = pd.to_numeric(raw_src, errors="coerce").fillna(0.0)
        score_pct = raw * 100.0
    score_pct = pd.to_numeric(score_pct, errors="coerce")
    out["bull_strength_proxy"] = score_pct
    attack_p = float(getattr(cfg, "bull_attack_percentile_threshold", 70.0))
    late_p = float(getattr(cfg, "bull_late_risk_percentile_threshold", 35.0))
    regime = out.get("pred_regime_label", pd.Series([""] * len(out), index=out.index)).astype(str).str.lower()
    side = out.get("side", pd.Series([""] * len(out), index=out.index)).astype(str).str.lower()
    mode = pd.Series("normal", index=out.index, dtype=object)
    mode.loc[score_pct >= attack_p] = "attack"
    mode.loc[score_pct <= late_p] = "late_risk"
    mode.loc[(regime == "trend_down") & (side == "long")] = "late_risk"
    out["bull_mode"] = mode.astype(str)
    return out


def resolve_htf_reference_df(
    *,
    df: pd.DataFrame,
    cfg: OotEvaluationConfig,
    htf_reference_df: pd.DataFrame | None,
) -> pd.DataFrame:
    """Return the filtered HTF reference frame aligned with current OOT rows."""
    if htf_reference_df is None:
        return df.copy()
    htf_reference = htf_reference_df.copy()
    if "symbol" in htf_reference.columns:
        htf_reference = mask_disabled_rows(htf_reference, symbol_column="symbol")
    if cfg.use_test_split_only and "pred_split" in htf_reference.columns:
        htf_reference = htf_reference.loc[
            htf_reference["pred_split"].astype(str).str.lower() == "test"
        ].copy()
    if cfg.use_last_window_only and "window_id" in htf_reference.columns:
        w_ref = pd.to_numeric(htf_reference["window_id"], errors="coerce")
        aligned = False
        if "window_id" in df.columns:
            w_cur = pd.to_numeric(df["window_id"], errors="coerce")
            if w_cur.notna().any():
                target_windows = set(w_cur[w_cur.notna()].tolist())
                mask = w_ref.isin(target_windows)
                if bool(mask.any()):
                    htf_reference = htf_reference.loc[mask].copy()
                    aligned = True
                else:
                    logger.warning(
                        "htf_reference_df has no rows matching target window_id=%s; fallback to reference max window",
                        sorted(target_windows),
                    )
        if not aligned and w_ref.notna().any():
            htf_reference = htf_reference.loc[w_ref == w_ref.max()].copy()
    if htf_reference.empty:
        logger.warning("htf_reference_df provided but empty after filters; fallback to local prediction_df")
        return df.copy()
    return htf_reference


__all__ = [
    "attach_bull_mode_columns",
    "resolve_htf_reference_df",
    "resolve_per_row_intrabar_stop_pct",
]
