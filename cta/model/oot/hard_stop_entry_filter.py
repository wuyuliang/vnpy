"""Entry-quality filter aimed at reducing hard-stop-heavy candidates."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.model.oot.block_reasons import BR_BLOCKED_HARD_STOP_ENTRY_FILTER
from cta.portfolio_logic.config import normalize_portfolio_interval


BLOCK_REASON = BR_BLOCKED_HARD_STOP_ENTRY_FILTER


def _signal_interval_key(signal_type: object, interval: object) -> str:
    st = str(signal_type or "").strip().lower()
    itv = normalize_portfolio_interval(interval)
    if not st or not itv:
        return ""
    return f"{st}|{itv}"


def apply_hard_stop_entry_filter(df: pd.DataFrame, cfg: object) -> pd.DataFrame:
    """Block low MFE/MAE candidates and scale high-MAE or neutral-HTF rows."""
    out = df.copy()
    if out.empty or not bool(getattr(cfg, "use_hard_stop_entry_filter", False)):
        return out
    if "execution_status" not in out.columns:
        out["execution_status"] = ""
    if "block_reason" not in out.columns:
        out["block_reason"] = ""
    if "risk_lots_mult" not in out.columns:
        out["risk_lots_mult"] = 1.0
    if "hard_stop_filter_reason" not in out.columns:
        out["hard_stop_filter_reason"] = ""
    else:
        out["hard_stop_filter_reason"] = out["hard_stop_filter_reason"].fillna("").astype(str)
    out["pred_mfe_mae_ratio"] = np.nan

    ratio_thresholds = dict(getattr(cfg, "min_pred_mfe_mae_ratio_by_signal_type_interval", {}) or {})
    mae_thresholds = dict(getattr(cfg, "max_pred_mae_atr_by_signal_type_interval", {}) or {})
    neutral_mult_by_interval = dict(getattr(cfg, "neutral_htf_size_multiplier_by_interval", {}) or {})
    high_mae_mult = float(getattr(cfg, "high_mae_size_multiplier", 1.0))

    pred_mfe = pd.to_numeric(
        out.get("pred_mfe_atr", pd.Series([np.nan] * len(out), index=out.index)),
        errors="coerce",
    )
    pred_mae = pd.to_numeric(
        out.get("pred_mae_atr", pd.Series([np.nan] * len(out), index=out.index)),
        errors="coerce",
    )
    ratio = pred_mfe / pred_mae.clip(lower=1e-9)
    out["pred_mfe_mae_ratio"] = ratio
    signal_type = out.get("signal_type", pd.Series([""] * len(out), index=out.index))
    interval = out.get("interval", pd.Series([""] * len(out), index=out.index))
    keys = [
        _signal_interval_key(st, itv)
        for st, itv in zip(signal_type.tolist(), interval.tolist(), strict=False)
    ]

    risk_mult = pd.to_numeric(out["risk_lots_mult"], errors="coerce").fillna(1.0)
    for idx, key in zip(out.index, keys, strict=False):
        if not key:
            continue
        r = float(ratio.loc[idx]) if np.isfinite(ratio.loc[idx]) else float("nan")
        ratio_threshold = ratio_thresholds.get(key)
        if ratio_threshold is not None and (not np.isfinite(r) or r < float(ratio_threshold)):
            out.at[idx, "execution_status"] = BLOCK_REASON
            out.at[idx, "block_reason"] = BLOCK_REASON
            out.at[idx, "hard_stop_filter_reason"] = "mfe_mae_ratio_low"
            continue
        mae = float(pred_mae.loc[idx]) if np.isfinite(pred_mae.loc[idx]) else float("nan")
        mae_threshold = mae_thresholds.get(key)
        if mae_threshold is not None and np.isfinite(mae) and mae > float(mae_threshold):
            existing_reason = str(out.at[idx, "hard_stop_filter_reason"])
            if "pred_mae_high" not in existing_reason.split(";"):
                risk_mult.loc[idx] = float(risk_mult.loc[idx]) * high_mae_mult
                out.at[idx, "hard_stop_filter_reason"] = (
                    "pred_mae_high" if not existing_reason else f"{existing_reason};pred_mae_high"
                )

    htf_alignment = out.get("htf_alignment", pd.Series([""] * len(out), index=out.index)).astype(str).str.lower()
    intervals = interval.map(normalize_portfolio_interval)
    for itv, mult in neutral_mult_by_interval.items():
        norm_itv = normalize_portfolio_interval(itv)
        neutral_mask = (intervals == norm_itv) & (htf_alignment == "neutral")
        if bool(neutral_mask.any()):
            already_neutral = out["hard_stop_filter_reason"].astype(str).str.split(";").map(
                lambda parts: "neutral_htf_size_mult" in parts
            )
            apply_mask = neutral_mask & (~already_neutral)
            if bool(apply_mask.any()):
                risk_mult.loc[apply_mask] = risk_mult.loc[apply_mask].astype(float) * float(mult)
                empty_reason = out["hard_stop_filter_reason"].astype(str) == ""
                out.loc[apply_mask & empty_reason, "hard_stop_filter_reason"] = "neutral_htf_size_mult"
                append_mask = apply_mask & (~empty_reason)
                out.loc[append_mask, "hard_stop_filter_reason"] = (
                    out.loc[append_mask, "hard_stop_filter_reason"].astype(str)
                    + ";neutral_htf_size_mult"
                )
    out["risk_lots_mult"] = risk_mult.astype(float)
    return out


__all__ = ["BLOCK_REASON", "apply_hard_stop_entry_filter"]
