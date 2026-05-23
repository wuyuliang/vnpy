"""Candidate generation and sample conversion for baseline strategies."""
from __future__ import annotations

import logging
from typing import Any

import numpy as np
import pandas as pd

from cta.config.baseline_skill_suite_config import BASELINE_SIGNAL_TYPES, LABEL_MAE_PENALTY, LABEL_THRESHOLD, TRAINING_FEATURE_COLUMNS
from cta.config.winning_position_setup_diversity_config import WinningPositionSetupDiversityConfig
from cta.config.skill_tight_range_breakout_config import VALID_SIDE_MODES
from cta.strategy.baseline_helpers import _safe_float
from cta.strategy.baseline_setup_detection import (
    _build_raw_setup_candidates,
    _infer_regime_label,
    _resolve_candidate_entry,
    _simulate_candidate_execution_path,
)
from cta.strategy.baseline_strategies import create_baseline_strategy
from cta.strategy.skill_tight_range_backtest import build_contract_spec

logger = logging.getLogger(__name__)


_SIGNAL_PRIORITY: dict[str, int] = {name: idx for idx, name in enumerate(BASELINE_SIGNAL_TYPES)}


def _apply_default_priority_dedup(candidates: pd.DataFrame) -> pd.DataFrame:
    """Default one-candidate dedup by signal priority.

    Dedup key: ``(symbol, side, datetime)``.
    """
    if candidates.empty:
        return candidates.copy()
    out = candidates.copy()
    sig = out.get("signal_type", pd.Series([""] * len(out), index=out.index)).astype(str).str.lower()
    out["_priority"] = sig.map(lambda x: int(_SIGNAL_PRIORITY.get(x, 10_000)))
    if "rank_score" in out.columns:
        rank = pd.to_numeric(out["rank_score"], errors="coerce")
        out["_rank_score"] = rank.fillna(float("-inf"))
    else:
        out["_rank_score"] = float("-inf")
    for col, default in (("symbol", ""), ("side", ""), ("datetime", pd.NaT)):
        if col not in out.columns:
            out[col] = default
    out["datetime"] = pd.to_datetime(out["datetime"], errors="coerce")
    out = out.sort_values(["symbol", "side", "datetime", "_priority", "_rank_score"], ascending=[True, True, True, True, False])
    out = out.drop_duplicates(subset=["symbol", "side", "datetime"], keep="first")
    return out.drop(columns=["_priority", "_rank_score"], errors="ignore").reset_index(drop=True)


def _expand_compatible_signal_types(
    candidates: pd.DataFrame,
    cfg: WinningPositionSetupDiversityConfig,
) -> pd.DataFrame:
    """Keep compatible signal families and cap concurrency."""
    if candidates.empty:
        return candidates.copy()
    out = candidates.copy()
    sig = out.get("signal_type", pd.Series([""] * len(out), index=out.index)).astype(str).str.lower()
    allowed: set[str] = set()
    for group in cfg.compatible_groups:
        allowed.update(group)
    out = out.loc[sig.isin(allowed)].copy()
    if out.empty:
        return out
    if "rank_score" in out.columns:
        out["_rank"] = pd.to_numeric(out["rank_score"], errors="coerce").fillna(float("-inf"))
    else:
        out["_rank"] = 0.0
    out = out.sort_values("_rank", ascending=False).head(int(cfg.max_concurrent_signal_types_per_symbol))
    return out.drop(columns=["_rank"], errors="ignore").reset_index(drop=True)


def filter_candidates_with_diversity(
    candidates: pd.DataFrame,
    *,
    current_position: Any | None,
    state: Any | None,
    cfg: WinningPositionSetupDiversityConfig,
    cluster: str | None = None,
    interval: str = "",
) -> pd.DataFrame:
    """Apply winning-position setup diversity policy."""
    if candidates.empty:
        return candidates.copy()
    if not bool(cfg.use_winning_position_setup_diversity):
        return _apply_default_priority_dedup(candidates)
    if not cfg.is_enabled(cluster, interval):
        return _apply_default_priority_dedup(candidates)
    if current_position is None or state is None:
        return _apply_default_priority_dedup(candidates)
    pnl_pct = float(getattr(state, "current_pnl_pct", np.nan))
    if not np.isfinite(pnl_pct) or pnl_pct < float(cfg.activation_pnl_pct):
        return _apply_default_priority_dedup(candidates)
    trend_score = float(getattr(state, "trend_score", np.nan))
    if bool(cfg.require_trend_confirmed) and (not np.isfinite(trend_score) or trend_score <= 0.0):
        return _apply_default_priority_dedup(candidates)
    return _expand_compatible_signal_types(candidates, cfg)


def build_training_samples_from_trade_log(
    trade_log: pd.DataFrame,
    frame: pd.DataFrame,
    symbol: str,
    exchange: str,
    interval: str,
    signal_type: str,
    drop_exit_truncated: bool = False,
    feature_columns: tuple[str, ...] = TRAINING_FEATURE_COLUMNS,
) -> pd.DataFrame:
    """Convert executed trades into ML-friendly samples."""
    if trade_log.empty:
        cols = [
            "symbol",
            "exchange",
            "interval",
            "datetime",
            "signal_datetime",
            "signal_type",
            "side",
            "signal_i",
            "entry_i",
            "exit_i",
            "is_exit_truncated",
            "holding_bars",
            "entry_price",
            "exit_price",
            "label_gross_pnl",
            "label_cost",
            "label_net_pnl",
            "label_win",
            "label_mfe_atr",
            "label_mae_atr",
            "atr_warmed",
        ] + [f"feature_{c}" for c in feature_columns]
        return pd.DataFrame(columns=cols)

    rows: list[dict[str, Any]] = []
    dt_series = pd.to_datetime(frame.get("datetime", pd.Series([pd.NaT] * len(frame))), errors="coerce")
    for _, tr in trade_log.iterrows():
        entry_i = int(_safe_float(tr.get("entry_i", -1)))
        exit_i_raw = int(_safe_float(tr.get("exit_i", -1)))
        if entry_i < 0 or exit_i_raw < entry_i or entry_i >= len(frame):
            continue
        is_exit_truncated = int(exit_i_raw > len(frame) - 1)
        if bool(drop_exit_truncated) and bool(is_exit_truncated):
            continue
        exit_i = min(exit_i_raw, len(frame) - 1)
        signal_i = max(0, entry_i - 1)
        signal_row = frame.iloc[signal_i]
        entry_row = frame.iloc[entry_i]
        entry_price = _safe_float(tr.get("entry_price", entry_row.get("close", np.nan)))
        exit_price = _safe_float(tr.get("exit_price", frame.iloc[exit_i].get("close", np.nan)))
        side = str(tr.get("side", "")).lower()
        if side not in {"long", "short"}:
            continue
        seg = frame.iloc[entry_i : exit_i + 1]
        seg_high = seg["high"].astype(float).max()
        seg_low = seg["low"].astype(float).min()
        atr_entry = _safe_float(entry_row.get("atr14", np.nan))

        if side == "short":
            mfe = entry_price - seg_low
            mae = seg_high - entry_price
        else:
            mfe = seg_high - entry_price
            mae = entry_price - seg_low
        mfe_atr = mfe / atr_entry if np.isfinite(atr_entry) and atr_entry > 0 else np.nan
        mae_atr = mae / atr_entry if np.isfinite(atr_entry) and atr_entry > 0 else np.nan
        atr_warmed_flag = int(np.isfinite(atr_entry) and atr_entry > 0)

        sample: dict[str, Any] = {
            "symbol": str(symbol).upper(),
            "exchange": str(exchange).upper(),
            "interval": str(interval),
            "datetime": dt_series.iloc[entry_i],
            "signal_datetime": dt_series.iloc[signal_i],
            "signal_type": str(signal_type),
            "side": side,
            "signal_i": signal_i,
            "entry_i": entry_i,
            "exit_i": exit_i,
            "is_exit_truncated": is_exit_truncated,
            "holding_bars": int(exit_i - entry_i),
            "entry_price": entry_price,
            "exit_price": exit_price,
            "label_gross_pnl": _safe_float(tr.get("gross_pnl", np.nan)),
            "label_cost": _safe_float(tr.get("cost", np.nan)),
            "label_net_pnl": _safe_float(tr.get("net_pnl", np.nan)),
            "label_win": int(_safe_float(tr.get("net_pnl", 0.0)) > 0.0),
            "label_mfe_atr": mfe_atr,
            "label_mae_atr": mae_atr,
            "atr_warmed": atr_warmed_flag,
        }
        for c in feature_columns:
            sample[f"feature_{c}"] = signal_row[c] if c in frame.columns else np.nan
        rows.append(sample)
    out = pd.DataFrame(rows)
    if not out.empty:
        out = out.sort_values("datetime").reset_index(drop=True)
    return out


def generate_candidate_opportunities(
    frame: pd.DataFrame,
    symbol: str,
    exchange: str,
    interval: str,
    signal_type: str,
    horizon_bars: int = 20,
    trade_side_mode: str = "both",
    label_stop_loss_pct: float = 0.01,
    drop_horizon_truncated: bool = False,
    feature_columns: tuple[str, ...] = TRAINING_FEATURE_COLUMNS,
) -> pd.DataFrame:
    """Generate candidate opportunities from baseline signal logic."""
    cols = [
        "symbol",
        "exchange",
        "interval",
        "datetime",
        "signal_datetime",
        "exit_datetime",
        "signal_type",
        "side",
        "order_type",
        "signal_i",
        "entry_i",
        "horizon_i",
        "is_horizon_truncated",
        "entry_price",
        "exit_price_ref",
        "stop_price",
        "target_price",
        "trigger",
        "future_mfe_atr",
        "future_mae_atr",
        "future_pnl_atr",
        "atr_warmed",
        "label_class",
        "regime_label",
        "candidate_status",
        "is_executed",
        "is_filtered",
        "is_triggered",
        "filtered_reason",
        "adaptive_window_used",
        "diversity_signal_types_count",
    ] + [f"feature_{c}" for c in feature_columns]
    if frame.empty:
        return pd.DataFrame(columns=cols)

    st = str(signal_type).strip().lower()
    if st not in BASELINE_SIGNAL_TYPES:
        raise ValueError(f"unsupported signal_type={signal_type}, valid={BASELINE_SIGNAL_TYPES}")

    mode = str(trade_side_mode).strip().lower()
    if mode not in VALID_SIDE_MODES:
        raise ValueError(f"invalid trade_side_mode={trade_side_mode}, valid={sorted(VALID_SIDE_MODES)}")

    contract = build_contract_spec(str(symbol).upper(), str(exchange).upper())
    strategy = create_baseline_strategy(
        signal_type=st,
        frame=frame,
        contract=contract,
        trade_side_mode=mode,
    )
    dt = pd.to_datetime(frame.get("datetime", pd.Series([pd.NaT] * len(frame))), errors="coerce")
    hz = max(2, int(horizon_bars))

    rows: list[dict[str, Any]] = []
    for i in range(1, len(frame) - 1):
        bar = frame.iloc[i]
        entry_i = i + 1
        if entry_i >= len(frame):
            continue
        entry_bar = frame.iloc[entry_i]
        intended_horizon_i = entry_i + hz
        horizon_i = min(len(frame) - 1, intended_horizon_i)
        is_horizon_truncated = int(intended_horizon_i > len(frame) - 1)
        if horizon_i <= entry_i:
            continue
        if bool(drop_horizon_truncated) and bool(is_horizon_truncated):
            continue

        try:
            live_orders = strategy.on_bar(i, bar, position=0)
        except Exception as exc:
            logger.debug("candidate scan skipped i=%s signal=%s due to: %s", i, st, exc)
            live_orders = []
        order_by_side: dict[str, dict[str, Any]] = {}
        for od in live_orders:
            side = str(od.get("side", "")).strip().lower()
            if side in {"long", "short"}:
                order_by_side[side] = od

        raw_setups = _build_raw_setup_candidates(
            frame,
            i,
            signal_type=st,
            contract=contract,
            mode=mode,
        )
        if not raw_setups and order_by_side:
            for side, od in order_by_side.items():
                raw_setups.append(
                    {
                        "side": side,
                        "order_type": str(od.get("order_type", "market")).strip().lower(),
                        "trigger": _safe_float(od.get("price", np.nan)),
                        "filtered_reason": None,
                    }
                )
        for setup in raw_setups:
            side = str(setup.get("side", "")).strip().lower()
            order_type = str(setup.get("order_type", "stop")).strip().lower()
            trigger = _safe_float(setup.get("trigger", np.nan))
            setup_target_price = _safe_float(setup.get("target_price", np.nan))
            setup_stop_price = _safe_float(setup.get("stop_price", np.nan))
            filtered_reason = setup.get("filtered_reason")
            order = order_by_side.get(side)
            if order is None:
                order = {"side": side, "order_type": order_type, "price": trigger}

            if filtered_reason is not None:
                triggered = False
                entry_price = trigger if np.isfinite(trigger) else np.nan
                stop_price = trigger if order_type == "stop" and np.isfinite(trigger) else np.nan
                candidate_status = "filtered"
            else:
                triggered, entry_price, stop_price = _resolve_candidate_entry(order, entry_bar)
                candidate_status = "filled" if triggered and np.isfinite(entry_price) else "not_triggered"
            if np.isfinite(setup_stop_price):
                stop_price = setup_stop_price

            atr_entry = _safe_float(entry_bar.get("atr14", np.nan))
            atr_signal = _safe_float(bar.get("atr14", np.nan))
            if np.isfinite(atr_entry) and atr_entry > 0:
                atr_v = atr_entry
                atr_warmed = True
            elif np.isfinite(atr_signal) and atr_signal > 0:
                atr_v = atr_signal
                atr_warmed = True
            else:
                hi_lo = _safe_float(entry_bar.get("high", np.nan)) - _safe_float(
                    entry_bar.get("low", np.nan)
                )
                atr_v = hi_lo if np.isfinite(hi_lo) and hi_lo > 0 else np.nan
                atr_warmed = False

            if candidate_status == "filled":
                sim = _simulate_candidate_execution_path(
                    frame,
                    entry_i=entry_i,
                    horizon_i=horizon_i,
                    side=side,
                    entry_price=float(entry_price),
                    atr_v=float(atr_v),
                    stop_loss_pct=float(label_stop_loss_pct),
                )
                mfe_atr = _safe_float(sim.get("mfe_atr", np.nan))
                mae_atr = _safe_float(sim.get("mae_atr", np.nan))
                future_pnl_atr = _safe_float(sim.get("pnl_atr", np.nan))
                exit_close = _safe_float(sim.get("exit_price", np.nan))
                future_pnl = future_pnl_atr * atr_v if np.isfinite(future_pnl_atr) and np.isfinite(atr_v) else np.nan
                if atr_warmed and np.isfinite(mfe_atr) and np.isfinite(mae_atr):
                    edge = float(mfe_atr) - LABEL_MAE_PENALTY * float(mae_atr)
                    label_class = int(edge > LABEL_THRESHOLD)
                elif atr_warmed and np.isfinite(future_pnl_atr):
                    label_class = int(future_pnl_atr > LABEL_THRESHOLD)
                elif np.isfinite(future_pnl):
                    label_class = int(future_pnl > 0.0)
                else:
                    label_class = 0
            else:
                hypo_entry = (
                    trigger
                    if np.isfinite(trigger)
                    else (
                        entry_price
                        if np.isfinite(entry_price)
                        else _safe_float(entry_bar.get("open", np.nan))
                    )
                )
                if np.isfinite(hypo_entry):
                    sim = _simulate_candidate_execution_path(
                        frame,
                        entry_i=entry_i,
                        horizon_i=horizon_i,
                        side=side,
                        entry_price=float(hypo_entry),
                        atr_v=float(atr_v),
                        stop_loss_pct=float(label_stop_loss_pct),
                    )
                    mfe_atr = _safe_float(sim.get("mfe_atr", np.nan))
                    mae_atr = _safe_float(sim.get("mae_atr", np.nan))
                    future_pnl_atr = _safe_float(sim.get("pnl_atr", np.nan))
                    exit_close = _safe_float(sim.get("exit_price", np.nan))
                else:
                    mfe_atr = np.nan
                    mae_atr = np.nan
                    future_pnl_atr = np.nan
                    exit_close = _safe_float(frame.iloc[horizon_i].get("close", np.nan)) if horizon_i < len(frame) else np.nan
                label_class = 0

            row: dict[str, Any] = {
                "symbol": str(symbol).upper(),
                "exchange": str(exchange).upper(),
                "interval": str(interval),
                "datetime": dt.iloc[entry_i] if entry_i < len(dt) else pd.NaT,
                "signal_datetime": dt.iloc[i],
                "exit_datetime": dt.iloc[horizon_i] if horizon_i < len(dt) else pd.NaT,
                "signal_type": st,
                "side": side,
                "order_type": order_type,
                "signal_i": i,
                "entry_i": entry_i,
                "horizon_i": horizon_i,
                "is_horizon_truncated": is_horizon_truncated,
                "entry_price": float(entry_price) if np.isfinite(entry_price) else np.nan,
                "exit_price_ref": float(exit_close) if np.isfinite(exit_close) else np.nan,
                "stop_price": float(stop_price) if np.isfinite(stop_price) else np.nan,
                "target_price": float(setup_target_price) if np.isfinite(setup_target_price) else np.nan,
                "trigger": float(trigger) if np.isfinite(trigger) else np.nan,
                "future_mfe_atr": float(mfe_atr) if np.isfinite(mfe_atr) else np.nan,
                "future_mae_atr": float(mae_atr) if np.isfinite(mae_atr) else np.nan,
                "future_pnl_atr": float(future_pnl_atr) if np.isfinite(future_pnl_atr) else np.nan,
                "atr_warmed": int(bool(atr_warmed)),
                "label_class": int(label_class),
                "regime_label": _infer_regime_label(bar),
                "candidate_status": candidate_status,
                "is_executed": int(candidate_status == "filled"),
                "is_filtered": int(candidate_status == "filtered"),
                "is_triggered": int(triggered),
                "filtered_reason": str(filtered_reason) if filtered_reason is not None else "",
                "adaptive_window_used": _safe_float(bar.get("adaptive_window_used", np.nan)),
                "diversity_signal_types_count": 1,
            }
            for c in feature_columns:
                row[f"feature_{c}"] = bar[c] if c in frame.columns else np.nan
            rows.append(row)

    out = pd.DataFrame(rows)
    if out.empty:
        return pd.DataFrame(columns=cols)
    out = out.dropna(subset=["datetime"]).sort_values(["datetime", "signal_type", "side"]).reset_index(drop=True)
    return out


__all__ = [
    "build_training_samples_from_trade_log",
    "filter_candidates_with_diversity",
    "_infer_regime_label",
    "_resolve_candidate_entry",
    "_simulate_candidate_execution_path",
    "_build_raw_setup_candidates",
    "_apply_default_priority_dedup",
    "_expand_compatible_signal_types",
    "generate_candidate_opportunities",
]
