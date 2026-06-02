"""Input preprocessing helpers for OOT real-execution evaluation."""
from __future__ import annotations

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.config.cost_manifest import impact_cost_pct
from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.config.symbol_disable import mask_disabled_rows
from cta.model.oot.pipeline_oot_evaluation_base import logger
from cta.portfolio_logic.config import normalize_portfolio_interval
from cta.live.risk import RiskContext
from cta.risk import RiskOrchestrator, SignalContext
from cta.risk.guards.consecutive_loss_guard import ConsecutiveLossGuard
from cta.risk.guards.liquidity_floor_guard import LiquidityFloorGuard
from cta.risk.guards.score_distribution_drift_guard import ScoreDistributionDriftGuard
from cta.risk.guards.signal_concentration_guard import SignalConcentrationGuard
from cta.risk.monitors.score_distribution_drift import ScoreDistributionDriftMonitor


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


def resolve_per_row_cost_pct(
    df: pd.DataFrame,
    cfg: OotEvaluationConfig,
) -> np.ndarray:
    """按 (cluster, interval) 解析每行 commission + slippage 单边比例的总和。

    返回每行 `commission_pct + slippage_pct`（cost_pct）数组：
      * 优先用 cfg.commission_pct_by_cluster_interval / slippage_pct_by_cluster_interval；
      * 未命中时回退到全局 cfg.commission_pct_per_trade + cfg.slippage_pct_per_trade。

    与现有 `_resolve_per_row_intrabar_stop_pct` 同款实现风格，避免 surprise。
    """
    default_commission = float(getattr(cfg, "commission_pct_per_trade", 0.0))
    default_slippage = float(getattr(cfg, "slippage_pct_per_trade", 0.0))
    default_total = default_commission + default_slippage

    def _normalize_overrides(raw: object) -> dict[str, float]:
        out: dict[str, float] = {}
        for raw_key, raw_val in dict(raw or {}).items():
            parts = str(raw_key).strip().lower().split("|")
            if len(parts) != 2 or not parts[0] or not parts[1]:
                continue
            norm_key = f"{parts[0]}|{normalize_portfolio_interval(parts[1])}"
            out[norm_key] = float(raw_val)
        return out

    comm_overrides = _normalize_overrides(getattr(cfg, "commission_pct_by_cluster_interval", {}))
    slip_overrides = _normalize_overrides(getattr(cfg, "slippage_pct_by_cluster_interval", {}))
    comm_by_symbol = {
        str(k).strip().upper(): float(v)
        for k, v in dict(getattr(cfg, "commission_pct_by_symbol", {}) or {}).items()
        if str(k).strip()
    }
    slip_by_symbol = {
        str(k).strip().upper(): float(v)
        for k, v in dict(getattr(cfg, "slippage_pct_by_symbol", {}) or {}).items()
        if str(k).strip()
    }

    n = len(df)
    if n == 0:
        return np.zeros(0, dtype=float)
    out = np.full(n, default_total, dtype=float)
    symbols = df.get("symbol", pd.Series([""] * n, index=df.index)).astype(str).str.upper().tolist()
    intervals_raw = df.get("interval", pd.Series([""] * n, index=df.index)).astype(str).tolist()
    adv_col = str(getattr(cfg, "impact_adv_lots_column", "adv_lots"))
    if adv_col in df.columns:
        adv_values = pd.to_numeric(df[adv_col], errors="coerce").to_numpy(dtype=float)
    elif "volume_adv20" in df.columns:
        adv_values = pd.to_numeric(df["volume_adv20"], errors="coerce").to_numpy(dtype=float)
    else:
        adv_values = np.full(n, np.nan, dtype=float)
    if "position_qty" in df.columns:
        order_lots = pd.to_numeric(df["position_qty"], errors="coerce").to_numpy(dtype=float)
    elif "lots" in df.columns:
        order_lots = pd.to_numeric(df["lots"], errors="coerce").to_numpy(dtype=float)
    else:
        order_lots = np.zeros(n, dtype=float)
    use_impact = bool(getattr(cfg, "use_impact_cost", False))
    impact_k = float(getattr(cfg, "impact_cost_k", 0.0))
    if not comm_overrides and not slip_overrides and not comm_by_symbol and not slip_by_symbol and not use_impact:
        return out
    for i, (sym, itv_raw) in enumerate(zip(symbols, intervals_raw)):
        cluster = str(infer_symbol_cluster(sym) or "").lower()
        if not cluster:
            comm = float(comm_by_symbol.get(sym, default_commission))
            slip = float(slip_by_symbol.get(sym, default_slippage))
            out[i] = comm + slip
            if use_impact:
                out[i] += impact_cost_pct(order_lots[i], adv_values[i], impact_k)
            continue
        itv = normalize_portfolio_interval(itv_raw)
        if not itv:
            continue
        key = f"{cluster}|{itv}"
        comm = float(comm_by_symbol.get(sym, comm_overrides.get(key, default_commission)))
        slip = float(slip_by_symbol.get(sym, slip_overrides.get(key, default_slippage)))
        out[i] = comm + slip
        if use_impact:
            out[i] += impact_cost_pct(order_lots[i], adv_values[i], impact_k)
    return out


def apply_oot_liquidity_floor_guard(df: pd.DataFrame, cfg: OotEvaluationConfig) -> pd.DataFrame:
    """Apply LiquidityFloorGuard to OOT candidate rows using row-level liquidity columns."""
    out = df.copy()
    out["liquidity_blocked"] = False
    out["liquidity_block_reason"] = ""
    if out.empty or not bool(getattr(cfg, "use_liquidity_floor_guard", False)):
        return out
    guard_cfg = getattr(cfg, "liquidity_floor_guard", None)
    guard = LiquidityFloorGuard(guard_cfg)
    ctx = RiskContext(
        pos={},
        daily_pnl=0.0,
        capital=float(getattr(cfg, "initial_capital", 0.0)),
        now=pd.Timestamp.now(),
    )
    for idx, row in out.iterrows():
        order = row.to_dict()
        offset = str(order.get("offset", "")).strip().lower()
        if not offset:
            order["offset"] = "open"
        decision = guard.check(order, ctx)
        if not decision.allowed:
            out.at[idx, "liquidity_blocked"] = True
            out.at[idx, "liquidity_block_reason"] = str(decision.reason)
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
    blocked_signal_types = {
        str(signal_type).strip().lower()
        for signal_type in getattr(cfg, "signal_type_blacklist", ())
        if str(signal_type).strip()
    }
    if htf_reference_df is None:
        if blocked_signal_types and "signal_type" in df.columns:
            signal_type_series = df["signal_type"].astype(str).str.strip().str.lower()
            return df.loc[~signal_type_series.isin(blocked_signal_types)].copy()
        return df.copy()
    htf_reference = htf_reference_df.copy()
    if "symbol" in htf_reference.columns:
        htf_reference = mask_disabled_rows(htf_reference, symbol_column="symbol")
    if blocked_signal_types and "signal_type" in htf_reference.columns:
        signal_type_series = htf_reference["signal_type"].astype(str).str.strip().str.lower()
        htf_reference = htf_reference.loc[~signal_type_series.isin(blocked_signal_types)].copy()
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


def _build_risk_orchestrator_from_cfg(cfg: OotEvaluationConfig) -> RiskOrchestrator | None:
    """Build RiskOrchestrator from OOT cfg; returns None when risk system is disabled."""
    risk_cfg = getattr(cfg, "risk_system", None)
    if risk_cfg is None:
        return None
    static_overrides = dict(
        getattr(cfg, "trade_filter_percentile_threshold_by_cluster_interval", {}) or {}
    )
    global_default = float(getattr(cfg, "trade_filter_percentile_threshold", 70.0))
    return RiskOrchestrator.from_config(
        risk_cfg,
        static_overrides=static_overrides,
        global_default_threshold=global_default,
    )


def _normalize_entry_order_fields(row_dict: dict) -> tuple[str, str, str]:
    symbol = str(row_dict.get("symbol", "")).strip().upper()
    cluster = str(row_dict.get("cluster_name") or row_dict.get("cluster") or "").strip().lower()
    if not cluster and symbol:
        cluster = str(infer_symbol_cluster(symbol) or "").strip().lower()
    side = str(row_dict.get("side") or row_dict.get("direction") or "").strip().lower()
    if side not in {"long", "short"}:
        side = "long"
    return symbol, cluster, side


def _apply_oot_guard_chain_columns(out: pd.DataFrame, cfg: OotEvaluationConfig) -> pd.DataFrame:
    """Replay configured guard rules in chronological order and append block reasons."""
    if out.empty or not bool(getattr(cfg, "use_oot_guard_chain", False)):
        return out

    rules: list = []
    if bool(getattr(cfg, "use_oot_consecutive_loss_guard", False)):
        rules.append(ConsecutiveLossGuard(getattr(cfg, "oot_consecutive_loss_guard")))
    if bool(getattr(cfg, "use_oot_signal_concentration_guard", False)):
        rules.append(SignalConcentrationGuard(getattr(cfg, "oot_signal_concentration_guard")))
    drift_monitor: ScoreDistributionDriftMonitor | None = None
    if bool(getattr(cfg, "use_oot_score_distribution_guard", False)):
        drift_monitor = ScoreDistributionDriftMonitor(getattr(cfg, "oot_score_distribution_guard"))
        rules.append(
            ScoreDistributionDriftGuard(
                getattr(cfg, "oot_score_distribution_guard"),
                monitor=drift_monitor,
            )
        )
    if not rules:
        return out

    dt_series = pd.to_datetime(
        out.get("datetime", pd.Series([pd.NaT] * len(out), index=out.index)),
        errors="coerce",
    )
    entry_dt = pd.to_datetime(out.get("entry_datetime", dt_series), errors="coerce")
    exit_dt = pd.to_datetime(out.get("exit_datetime", entry_dt), errors="coerce")
    # 质量优先（2026-05-30）：guard 名额竞争时优先放高置信度候选。
    # 防泄露铁律：仅在**同一 entry_dt 内**按 score 降序，跨 bar 仍严格按时间顺序，
    # 绝不用未来 bar 的信息改变更早 bar 的成交资格。
    rank_score = pd.to_numeric(
        out.get("trade_filter_prob_pctl", out.get("trade_filter_prob", pd.Series(0.0, index=out.index))),
        errors="coerce",
    ).fillna(-1.0)
    order_df = pd.DataFrame(
        {
            "idx": out.index,
            "entry_dt": entry_dt,
            "exit_dt": exit_dt,
            "_rank_score": rank_score.values,
        }
    ).sort_values(
        ["entry_dt", "_rank_score", "idx"],
        ascending=[True, False, True],
        kind="stable",
    )

    open_positions: list[dict[str, object]] = []
    open_by_cluster: dict[str, int] = {}
    open_by_cluster_direction: dict[str, dict[str, int]] = {}

    def _release_positions(now: pd.Timestamp) -> None:
        if not open_positions:
            return
        keep: list[dict[str, object]] = []
        for pos in open_positions:
            pos_exit = pd.to_datetime(pos.get("exit_dt"), errors="coerce")
            if pd.notna(pos_exit) and pos_exit <= now:
                cluster = str(pos.get("cluster", "")).strip().lower()
                side = str(pos.get("side", "")).strip().lower()
                if cluster:
                    open_by_cluster[cluster] = max(0, int(open_by_cluster.get(cluster, 0) - 1))
                    by_dir = open_by_cluster_direction.get(cluster, {})
                    if isinstance(by_dir, dict):
                        by_dir[side] = max(0, int(by_dir.get(side, 0) - 1))
                        open_by_cluster_direction[cluster] = by_dir
                continue
            keep.append(pos)
        open_positions[:] = keep

    for rec in order_df.itertuples(index=False):
        idx = rec.idx
        cur_dt = pd.to_datetime(rec.entry_dt, errors="coerce")
        if pd.isna(cur_dt):
            cur_dt = pd.Timestamp.now()
        _release_positions(pd.Timestamp(cur_dt))
        existing_reason = str(out.at[idx, "risk_block_reason"]).strip()
        if existing_reason:
            continue
        row_dict = out.loc[idx].to_dict()
        symbol, cluster, side = _normalize_entry_order_fields(row_dict)
        row_dict["symbol"] = symbol
        row_dict["cluster"] = cluster
        row_dict["cluster_name"] = cluster
        row_dict["direction"] = side
        row_dict["side"] = side
        row_dict["offset"] = "open"
        row_dict["interval"] = normalize_portfolio_interval(row_dict.get("interval", ""))

        ctx = RiskContext(
            pos={},
            daily_pnl=0.0,
            capital=float(getattr(cfg, "initial_capital", 0.0)),
            now=pd.Timestamp(cur_dt),
            open_positions_total=len(open_positions),
            open_positions_by_cluster=dict(open_by_cluster),
        )
        setattr(ctx, "open_positions_by_cluster_direction", open_by_cluster_direction)
        if drift_monitor is not None:
            score = row_dict.get("trade_filter_prob_pctl")
            if score is None or (isinstance(score, float) and not np.isfinite(score)):
                score = row_dict.get("trade_filter_prob")
            drift_monitor.observe(score, pd.Timestamp(cur_dt))

        blocked_reason = ""
        for rule in rules:
            decision = rule.check(row_dict, ctx)
            if not decision.allowed:
                blocked_reason = str(decision.reason or "")
                break
        if blocked_reason:
            out.at[idx, "risk_block_reason"] = blocked_reason
            out.at[idx, "risk_lots_mult"] = 0.0
            continue
        if cluster:
            open_by_cluster[cluster] = int(open_by_cluster.get(cluster, 0)) + 1
            by_dir = open_by_cluster_direction.setdefault(cluster, {"long": 0, "short": 0})
            by_dir[side] = int(by_dir.get(side, 0)) + 1
            open_by_cluster_direction[cluster] = by_dir
        open_positions.append(
            {
                "cluster": cluster,
                "side": side,
                "exit_dt": pd.to_datetime(rec.exit_dt, errors="coerce"),
            }
        )
    return out


def apply_risk_orchestrator_columns(
    df: pd.DataFrame,
    cfg: OotEvaluationConfig,
    *,
    portfolio_snapshot: dict[str, float] | None = None,
) -> pd.DataFrame:
    """Attach risk_orchestrator diagnostics columns to candidate rows.

    Added columns:
      - ``risk_effective_threshold``
      - ``risk_lots_mult``
      - ``risk_block_reason``
    """
    out = df.copy()
    n = len(out)
    if n == 0:
        out["risk_effective_threshold"] = pd.Series([], dtype=float)
        out["risk_lots_mult"] = pd.Series([], dtype=float)
        out["risk_block_reason"] = pd.Series([], dtype=object)
        return out

    out["risk_effective_threshold"] = np.nan
    out["risk_lots_mult"] = 1.0
    out["risk_block_reason"] = ""
    orchestrator = _build_risk_orchestrator_from_cfg(cfg)
    if orchestrator is not None:
        base_portfolio = dict(portfolio_snapshot or {})
        for idx, row in out.iterrows():
            row_dict = row.to_dict()
            symbol, cluster, _side = _normalize_entry_order_fields(row_dict)
            if cluster:
                row_dict["cluster_name"] = cluster
                row_dict["cluster"] = cluster
            interval = normalize_portfolio_interval(row_dict.get("interval", ""))
            if interval:
                row_dict["interval"] = interval
            dt_raw = row_dict.get("datetime")
            dt = pd.to_datetime(dt_raw, errors="coerce")
            if pd.isna(dt):
                dt = pd.Timestamp.now()
            lots_val = row_dict.get("lots", row_dict.get("position_qty", 1))
            try:
                original_lots = max(1, int(float(lots_val)))
            except (TypeError, ValueError):
                original_lots = 1
            decision = orchestrator.evaluate(
                SignalContext(
                    candidate=row_dict,
                    portfolio=dict(base_portfolio),
                    bar_dt=pd.Timestamp(dt),
                ),
                original_lots=original_lots,
            )
            out.at[idx, "risk_effective_threshold"] = float(decision.effective_threshold)
            out.at[idx, "risk_block_reason"] = str(decision.block_reason or "")
            if decision.passed and original_lots > 0:
                out.at[idx, "risk_lots_mult"] = float(decision.adjusted_lots / original_lots)
            else:
                out.at[idx, "risk_lots_mult"] = 0.0
    out = _apply_oot_guard_chain_columns(out, cfg)
    return out


__all__ = [
    "apply_risk_orchestrator_columns",
    "apply_oot_liquidity_floor_guard",
    "attach_bull_mode_columns",
    "resolve_htf_reference_df",
    "resolve_per_row_intrabar_stop_pct",
]
