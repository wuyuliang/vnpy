"""Online candidate generator: feature join + model scoring + OOT-consistent gates."""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.live.model_registry import LiveModelRegistry
from cta.model.oot.pipeline_oot_evaluation_gates import apply_oot_model_gates
from cta.risk.state.score_quantile_manifest import ScoreQuantileEntry, ScoreQuantileManifest

logger = logging.getLogger(__name__)


def _coerce_row(value: Any) -> dict[str, Any]:
    if isinstance(value, pd.DataFrame):
        if value.empty:
            return {}
        return dict(value.iloc[-1].to_dict())
    if isinstance(value, pd.Series):
        return dict(value.to_dict())
    if isinstance(value, dict):
        return dict(value)
    return {}


def _expand_candidates(
    bars_by_symbol_interval: dict[Any, Any],
    *,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    rows: list[dict[str, Any]] = []
    for key, value in bars_by_symbol_interval.items():
        row = _coerce_row(value)
        if not row:
            continue
        symbol = str(row.get("symbol", "")).strip().upper()
        interval = str(row.get("interval", "")).strip()
        if isinstance(key, tuple) and len(key) >= 2:
            if not symbol:
                symbol = str(key[0]).strip().upper()
            if not interval:
                interval = str(key[1]).strip()
        if not symbol or not interval:
            continue
        dt = pd.Timestamp(row.get("datetime", as_of))
        side_raw = str(row.get("side", "")).strip().lower()
        sides = [side_raw] if side_raw in {"long", "short"} else ["long", "short"]
        for side in sides:
            r = dict(row)
            r["symbol"] = symbol
            r["interval"] = interval
            r["datetime"] = dt
            r["side"] = side
            r.setdefault("signal_type", "donchian_breakout")
            rows.append(r)
    if not rows:
        return pd.DataFrame(columns=["datetime", "symbol", "interval", "side", "signal_type"])
    out = pd.DataFrame(rows)
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.dropna(subset=["datetime"]).reset_index(drop=True)
    return out


def _attach_online_features(
    df: pd.DataFrame,
    *,
    feature_loader: Any | None,
    as_of: pd.Timestamp,
) -> pd.DataFrame:
    if feature_loader is None or df.empty:
        return df
    out = df.copy()
    for idx, row in out.iterrows():
        symbol = str(row.get("symbol", "")).strip().upper()
        interval = str(row.get("interval", "")).strip()
        dt = pd.Timestamp(row.get("datetime", as_of))
        try:
            feat = feature_loader(symbol, interval, dt)
        except TypeError:
            # 兼容 OnlineFeatureLoader.__call__(adapter, columns) 旧签名场景：直接跳过
            continue
        except Exception as exc:  # noqa: BLE001
            logger.warning("feature loader failed for %s %s: %s", symbol, interval, exc)
            continue
        if feat is None:
            continue
        if isinstance(feat, pd.Series):
            feat_map = feat.to_dict()
        elif isinstance(feat, dict):
            feat_map = feat
        else:
            continue
        for k, v in feat_map.items():
            if k not in out.columns or pd.isna(out.at[idx, k]):
                out.at[idx, k] = v
    return out


def _interp_percentile(prob: float, entry: ScoreQuantileEntry) -> float:
    p = float(np.clip(prob, 0.0, 1.0))
    xs = [0.0, float(entry.p50), float(entry.p60), float(entry.p70), float(entry.p80), float(entry.p90), float(entry.p95), 1.0]
    ys = [0.0, 50.0, 60.0, 70.0, 80.0, 90.0, 95.0, 100.0]
    xs = [float(xs[i]) if i == 0 else float(max(xs[i], xs[i - 1] + 1e-9)) for i in range(len(xs))]
    return float(np.clip(np.interp(p, xs, ys), 0.0, 100.0))


def _fill_trade_filter_percentile(
    df: pd.DataFrame,
    *,
    manifest: ScoreQuantileManifest | None,
) -> pd.DataFrame:
    if df.empty:
        return df
    out = df.copy()
    if "trade_filter_prob_pctl" not in out.columns:
        out["trade_filter_prob_pctl"] = np.nan
    if manifest is None:
        return out
    for idx, row in out.iterrows():
        current = pd.to_numeric(pd.Series([row.get("trade_filter_prob_pctl")]), errors="coerce").iloc[0]
        if pd.notna(current):
            continue
        prob = float(pd.to_numeric(pd.Series([row.get("trade_filter_prob")]), errors="coerce").fillna(0.0).iloc[0])
        symbol = str(row.get("symbol", "")).strip().upper()
        interval = str(row.get("interval", "")).strip()
        cluster = str(row.get("cluster_name", "")).strip().lower()
        if not cluster:
            cluster = str(infer_symbol_cluster(symbol) or "").strip().lower()
        entry = manifest.lookup(cluster=cluster, symbol=symbol, interval=interval)
        if entry is None:
            continue
        out.at[idx, "trade_filter_prob_pctl"] = _interp_percentile(prob, entry)
    return out


def _apply_signal_type_blacklist(df: pd.DataFrame, *, cfg: OotEvaluationConfig) -> pd.DataFrame:
    if df.empty or "signal_type" not in df.columns:
        return df
    blocked = {
        str(signal_type).strip().lower()
        for signal_type in getattr(cfg, "signal_type_blacklist", ())
        if str(signal_type).strip()
    }
    if not blocked:
        return df
    signal_type_series = df["signal_type"].astype(str).str.strip().str.lower()
    return df.loc[~signal_type_series.isin(blocked)].copy()


def _apply_model_predictions(
    df: pd.DataFrame,
    *,
    model_registry: LiveModelRegistry | Any | None,
) -> pd.DataFrame:
    if model_registry is None or df.empty:
        return df
    out = df.copy()
    for kind in ("trade_filter", "regime_classifier", "mfe_mae", "final_decision_stack"):
        try:
            out = model_registry.predict(out, model_kind=kind)
        except Exception as exc:  # noqa: BLE001
            logger.warning("model prediction skipped kind=%s: %s", kind, exc)
            continue
    # 统一 final score 列名
    if "final_decision_score" not in out.columns and "final_decision_stack_prob" in out.columns:
        out["final_decision_score"] = pd.to_numeric(out["final_decision_stack_prob"], errors="coerce")
    return out


def generate_today_candidates(
    *,
    bars_by_symbol_interval: dict[Any, Any],
    cfg: OotEvaluationConfig,
    as_of: pd.Timestamp,
    model_registry: LiveModelRegistry | Any | None = None,
    feature_loader: Any | None = None,
    score_manifest: ScoreQuantileManifest | None = None,
) -> pd.DataFrame:
    """Generate candidate table for current bar-close decision.

    输出列兼容 OOT 评估输入，至少包含：
    ``datetime/symbol/interval/side/signal_type/trade_filter_prob/trade_filter_prob_pctl``
    以及 ``_model_pass`` / ``_model_block_reason``。
    """
    cands = _expand_candidates(bars_by_symbol_interval, as_of=pd.Timestamp(as_of))
    if cands.empty:
        return cands
    cands = _apply_signal_type_blacklist(cands, cfg=cfg)
    if cands.empty:
        return cands
    cands = _attach_online_features(cands, feature_loader=feature_loader, as_of=pd.Timestamp(as_of))
    cands = _apply_model_predictions(cands, model_registry=model_registry)
    cands = _fill_trade_filter_percentile(cands, manifest=score_manifest)
    cands, gate, reason = apply_oot_model_gates(cands, cfg)
    cands = cands.copy()
    cands["_model_pass"] = gate.astype(bool)
    cands["_model_block_reason"] = reason.astype(str)
    cands = cands.sort_values(["datetime", "symbol", "interval", "side"]).reset_index(drop=True)
    return cands


__all__ = ["generate_today_candidates"]
