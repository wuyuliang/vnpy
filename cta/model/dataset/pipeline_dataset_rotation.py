"""Cross-sectional rotation candidate helpers for dataset preparation."""
from __future__ import annotations

from typing import Sequence

from cta.config.baseline_skill_suite_config import TRAINING_FEATURE_COLUMNS
from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig
from cta.model.orchestration.pipeline_base import (
    BacktestConfig,
    LABEL_MAE_PENALTY,
    LABEL_THRESHOLD,
    load_bars,
    logger,
    normalize_interval,
    np,
    pd,
    prepare_master_feature_frame,
    resolve_exchange,
)
from cta.strategy.baseline_candidate_gen import _infer_regime_label, _simulate_candidate_execution_path
from cta.strategy.cross_sectional_momentum_rotation import (
    CrossSectionalMomentumRotation,
    SIGNAL_TYPE as CROSS_SECTIONAL_SIGNAL_TYPE,
    _is_rebalance_day,
)


def _build_pool_cross_sectional_candidate_table(
    pool_symbols: Sequence[tuple[str, str | None]],
    *,
    interval: str,
    start_date: str,
    end_date: str,
    trade_side_mode: str,
    rotation_cfg: CrossSectionalRotationConfig,
) -> pd.DataFrame:
    """Build day-level cross-sectional rotation candidates for pool training."""
    if not bool(getattr(rotation_cfg, "use_cross_sectional_momentum_rotation", False)):
        return pd.DataFrame()
    interval_norm = normalize_interval(interval)
    probe_clusters = ("black", "metal", "chemical", "agri", "precious", "index", "bond", "other")
    if not any(rotation_cfg.is_enabled(c, interval_norm) for c in probe_clusters):
        return pd.DataFrame()
    if len(pool_symbols) < 2:
        return pd.DataFrame()

    mode = str(trade_side_mode).strip().lower()
    if mode not in {"both", "long", "short"}:
        mode = "both"

    frame_map: dict[str, pd.DataFrame] = {}
    exchange_map: dict[str, str] = {}
    dt_map: dict[str, np.ndarray] = {}
    bcfg = BacktestConfig(interval=interval_norm)
    for sym_raw, ex_raw in pool_symbols:
        sym = str(sym_raw).upper()
        ex = str(ex_raw).upper() if ex_raw else resolve_exchange(sym, bcfg.symbols_list_path)
        try:
            bars = load_bars(sym, bcfg, start_date, end_date, exchange=ex)
            frame = prepare_master_feature_frame(bars, interval=interval_norm)
        except Exception:
            logger.exception("cross-sectional: failed to load frame for symbol=%s, skip", sym)
            continue
        if frame.empty or "datetime" not in frame.columns:
            continue
        frame = frame.sort_values("datetime").reset_index(drop=True)
        dt_series = pd.to_datetime(frame["datetime"], errors="coerce")
        if dt_series.isna().all():
            continue
        frame_map[sym] = frame
        exchange_map[sym] = ex
        dt_map[sym] = dt_series.to_numpy(dtype="datetime64[ns]")
    if len(frame_map) < 2:
        return pd.DataFrame()

    all_dates = sorted(
        {
            pd.Timestamp(v).normalize()
            for dt_values in dt_map.values()
            for v in dt_values
            if not pd.isna(v)
        }
    )
    if not all_dates:
        return pd.DataFrame()

    rotation = CrossSectionalMomentumRotation(rotation_cfg)
    last_rebalance_dt: pd.Timestamp | None = None
    rows: list[pd.DataFrame] = []
    for date in all_dates:
        if not _is_rebalance_day(
            date,
            rebalance_weekday=rotation_cfg.rebalance_weekday,
            last_rebalance_dt=last_rebalance_dt,
            max_holding_days=rotation_cfg.max_holding_days,
        ):
            continue
        last_rebalance_dt = pd.Timestamp(date)
        universe_as_of: dict[str, pd.DataFrame] = {}
        for sym, frame in frame_map.items():
            pos = int(dt_map[sym].searchsorted(np.datetime64(date), side="right"))
            if pos > 0:
                universe_as_of[sym] = frame.iloc[:pos]
        if len(universe_as_of) < 2:
            continue
        cand = rotation.generate_rebalance_candidates(
            pd.Timestamp(date),
            universe_as_of,
            interval=interval_norm,
            last_rebalance_dt=last_rebalance_dt,
            current_drawdown_pct=0.0,
        )
        if cand.empty:
            continue
        if mode in {"long", "short"}:
            cand = cand.loc[cand["side"].astype(str).str.lower() == mode].copy()
        if not cand.empty:
            rows.append(
                _convert_pool_cross_sectional_candidates_to_training_rows(
                    cand,
                    frame_map=frame_map,
                    exchange_map=exchange_map,
                    interval=interval_norm,
                    stop_loss_pct=float(rotation_cfg.stop_loss_pct),
                )
            )
    if not rows:
        return pd.DataFrame()
    out = pd.concat(rows, axis=0, ignore_index=True)
    if out.empty:
        return out
    return out.sort_values(["datetime", "symbol", "side"]).reset_index(drop=True)


def _numeric_one(row: pd.Series | dict, key: str) -> float:
    return pd.to_numeric(pd.Series([row.get(key, np.nan)]), errors="coerce").iloc[0]


def _convert_pool_cross_sectional_candidates_to_training_rows(
    candidates: pd.DataFrame,
    *,
    frame_map: dict[str, pd.DataFrame],
    exchange_map: dict[str, str],
    interval: str,
    stop_loss_pct: float,
) -> pd.DataFrame:
    """Convert rotation rebalance candidates to canonical candidate rows."""
    out_rows: list[dict[str, object]] = []
    for _, c in candidates.iterrows():
        sym = str(c.get("symbol", "")).upper()
        side = str(c.get("side", "")).lower()
        frame = frame_map.get(sym)
        if not sym or side not in {"long", "short"} or frame is None or frame.empty:
            continue
        dt = pd.to_datetime(frame["datetime"], errors="coerce")
        if dt.isna().all():
            continue
        signal_dt = pd.Timestamp(c.get("signal_datetime"))
        entry_dt = pd.Timestamp(c.get("entry_datetime"))
        planned_exit_dt = pd.Timestamp(c.get("planned_exit_datetime"))
        signal_i = int(dt.searchsorted(signal_dt, side="right") - 1)
        entry_i = int(dt.searchsorted(entry_dt, side="left"))
        if signal_i < 0 or entry_i <= signal_i or entry_i >= len(frame):
            continue
        horizon_i = min(max(entry_i + 1, int(dt.searchsorted(planned_exit_dt, side="right") - 1)), len(frame) - 1)
        if horizon_i <= entry_i:
            continue

        signal_bar = frame.iloc[signal_i]
        entry_bar = frame.iloc[entry_i]
        atr = _numeric_one(entry_bar, "atr14")
        if not np.isfinite(atr) or atr <= 0:
            hi = _numeric_one(entry_bar, "high")
            lo = _numeric_one(entry_bar, "low")
            atr = float(hi - lo) if np.isfinite(hi) and np.isfinite(lo) and hi > lo else np.nan
        atr_warmed = int(np.isfinite(atr) and atr > 0)

        entry_open = _numeric_one(entry_bar, "open")
        entry_close = _numeric_one(entry_bar, "close")
        entry_price = float(entry_open if np.isfinite(entry_open) else entry_close)
        if not np.isfinite(entry_price):
            continue
        sim = _simulate_candidate_execution_path(
            frame,
            entry_i=entry_i,
            horizon_i=horizon_i,
            side=side,
            entry_price=entry_price,
            atr_v=float(atr if np.isfinite(atr) else 1.0),
            stop_loss_pct=float(stop_loss_pct),
        )
        mfe_atr = _numeric_one(sim, "mfe_atr")
        mae_atr = _numeric_one(sim, "mae_atr")
        pnl_atr = _numeric_one(sim, "pnl_atr")
        exit_price = _numeric_one(sim, "exit_price")
        if atr_warmed and np.isfinite(mfe_atr) and np.isfinite(mae_atr):
            label_class = int(float(mfe_atr) - LABEL_MAE_PENALTY * float(mae_atr) > LABEL_THRESHOLD)
        else:
            label_class = int(float(pnl_atr) > LABEL_THRESHOLD) if np.isfinite(pnl_atr) else 0

        stop_price = _numeric_one(c, "stop_price")
        if not np.isfinite(stop_price):
            stop_price = entry_price * (1.0 - float(stop_loss_pct)) if side == "long" else entry_price * (1.0 + float(stop_loss_pct))

        row: dict[str, object] = {
            "symbol": sym,
            "exchange": exchange_map.get(sym, ""),
            "interval": interval,
            "datetime": dt.iloc[entry_i],
            "signal_datetime": dt.iloc[signal_i],
            "exit_datetime": dt.iloc[horizon_i],
            "signal_type": CROSS_SECTIONAL_SIGNAL_TYPE,
            "side": side,
            "order_type": "market",
            "signal_i": signal_i,
            "entry_i": entry_i,
            "horizon_i": horizon_i,
            "is_horizon_truncated": 0,
            "entry_price": entry_price,
            "exit_price_ref": float(exit_price) if np.isfinite(exit_price) else np.nan,
            "stop_price": float(stop_price) if np.isfinite(stop_price) else np.nan,
            "target_price": np.nan,
            "trigger": entry_price,
            "future_mfe_atr": float(mfe_atr) if np.isfinite(mfe_atr) else np.nan,
            "future_mae_atr": float(mae_atr) if np.isfinite(mae_atr) else np.nan,
            "future_pnl_atr": float(pnl_atr) if np.isfinite(pnl_atr) else np.nan,
            "atr_warmed": atr_warmed,
            "label_class": int(label_class),
            "regime_label": _infer_regime_label(signal_bar),
            "candidate_status": "filled",
            "is_executed": 1,
            "is_filtered": 0,
            "is_triggered": 1,
            "filtered_reason": "",
            "feature_rotation_momentum_score": _numeric_one(c, "momentum_score"),
            "feature_rotation_momentum_rank": _numeric_one(c, "momentum_rank"),
            "feature_rotation_percentile_cluster": _numeric_one(c, "percentile_in_cluster"),
            "feature_rotation_percentile_universe": _numeric_one(c, "percentile_in_universe"),
            "feature_rotation_vol_target_scale": _numeric_one(c, "vol_target_scale"),
            "feature_rotation_target_weight": _numeric_one(c, "target_weight"),
        }
        for col in TRAINING_FEATURE_COLUMNS:
            row[f"feature_{col}"] = signal_bar[col] if col in frame.columns else np.nan
        out_rows.append(row)
    return pd.DataFrame(out_rows)


__all__ = ["_build_pool_cross_sectional_candidate_table"]
