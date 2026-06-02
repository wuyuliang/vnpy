"""OOT real-execution evaluation public API."""
from __future__ import annotations

from cta.model.oot.pipeline_oot_evaluation_base import BR_BLOCKED_CLUSTER_CAP, BR_BLOCKED_DAILY_POSITION, BR_BLOCKED_FINAL_DECISION_GATE, BR_BLOCKED_LEVERAGE, BR_BLOCKED_MARGIN_CASH, BR_BLOCKED_MFE_MAE_GATE, BR_BLOCKED_MONTHLY_DRAWDOWN, BR_BLOCKED_PORTFOLIO_CONSTRAINT, BR_BLOCKED_REGIME_GATE, BR_BLOCKED_SIGNAL_TYPE_CONCURRENT, BR_BLOCKED_SYMBOL_CAP, BR_BLOCKED_TOTAL_NOTIONAL, BR_BLOCKED_TOTAL_CONCURRENT, BR_BLOCKED_TRADE_FILTER, BR_BLOCKED_WEEKLY_BUDGET, BR_BLOCKED_WEEKLY_DRAWDOWN, BR_ZERO_NOTIONAL
from cta.model.oot.pipeline_oot_evaluation_base import DEFAULT_OOT_EVAL_CONFIG, EquityTracker, HtfGate, OpportunityRanker, OotEvaluationConfig, PortfolioState, PyramidManager, PyramidPosition, RiskThrottle
from cta.model.oot.pipeline_oot_evaluation_base import _IntrabarBarCache, _build_position_lifetime_table, _calc_roll_cost, _log_block_reason_distribution, _resolve_contract_spec, _simulate_intrabar_exit
from cta.model.oot.pipeline_oot_evaluation_base import dataclasses, infer_symbol_cluster, logger, normalize_interval, normalize_portfolio_interval, np, pd, simulate_trailing_exit
from cta.model.oot.pipeline_oot_evaluation_gates import apply_oot_model_gates
from cta.model.oot.pipeline_oot_evaluation_inputs import (
    apply_oot_liquidity_floor_guard,
    apply_risk_orchestrator_columns,
    attach_bull_mode_columns,
    resolve_per_row_cost_pct,
    resolve_per_row_intrabar_stop_pct,
    resolve_htf_reference_df,
)
from cta.utils.limit_move import is_limit_move_blocked_by_flags

_resolve_per_row_intrabar_stop_pct = resolve_per_row_intrabar_stop_pct
_resolve_per_row_cost_pct = resolve_per_row_cost_pct
_attach_bull_mode_columns = attach_bull_mode_columns


def _to_dt_mixed(series: pd.Series) -> pd.Series:
    """Parse a datetime column that may MIX formats (date-only + datetime).

    2026-06-01 fix（统一组合多 interval 混合回放）：day 预测的时间是"日期-only"
    （``2024-01-03``），minute 预测带时分秒（``2024-01-03 09:30:00``）。当 day + minute
    候选合并进同一次回放时，裸 ``pd.to_datetime(errors='coerce')`` 会从列里推断**单一**
    格式，把不匹配那种（取决于 concat 顺序，day 或 minute）整段 coerce 成 NaT，随后被
    ``dropna`` 丢掉 → 输出塌成单一 interval。``format='mixed'`` 逐行推断格式，两种都正确解析。
    对老版 pandas 不支持 ``format='mixed'`` 时回退到裸解析（保持向后兼容）。
    """
    try:
        return pd.to_datetime(series, errors="coerce", format="mixed")
    except (TypeError, ValueError):
        return pd.to_datetime(series, errors="coerce")


def _first_bar_with_capacity(
    bars: pd.DataFrame,
    *,
    start_ts: pd.Timestamp,
    qty: float,
    max_participation_pct: float,
    volume_column: str,
) -> pd.Timestamp:
    """Return earliest bar where cumulative volume capacity can fill ``qty`` lots."""
    if bars.empty or "datetime" not in bars.columns:
        return pd.NaT
    if not np.isfinite(float(qty)) or float(qty) <= 0.0:
        return pd.NaT
    if not np.isfinite(float(max_participation_pct)) or float(max_participation_pct) <= 0.0:
        return pd.NaT

    vol_col = str(volume_column or "").strip()
    if not vol_col:
        return pd.NaT
    if vol_col not in bars.columns:
        lower_map = {str(c).lower(): str(c) for c in bars.columns}
        vol_col = lower_map.get(vol_col.lower(), "")
    if not vol_col or vol_col not in bars.columns:
        return pd.NaT

    dt = pd.to_datetime(bars["datetime"], errors="coerce")
    mask = dt.notna() & (dt >= pd.Timestamp(start_ts))
    if not bool(mask.any()):
        return pd.NaT
    caps = pd.to_numeric(bars.loc[mask, vol_col], errors="coerce").fillna(0.0)
    caps = caps.clip(lower=0.0) * float(max_participation_pct)
    ok = caps.cumsum() >= float(qty)
    if not bool(ok.any()):
        return pd.NaT
    idx = caps.loc[ok].index[0]
    return pd.Timestamp(dt.loc[idx])


def _evaluate_oot_real_execution(prediction_df: pd.DataFrame, *, cfg: OotEvaluationConfig=DEFAULT_OOT_EVAL_CONFIG, intrabar_bar_provider: Callable[[str, str, pd.Timestamp, pd.Timestamp, str], pd.DataFrame] | None=None, extra_outputs: dict[str, pd.DataFrame] | None=None, htf_reference_df: pd.DataFrame | None=None) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    """Evaluate OOT performance on real executed trades with model gating.

    Args:
        prediction_df: 当前 interval 的预测样本（通常来自 *_predictions.csv）。; cfg: OOT 评估参数。; intrabar_bar_provider: 可选的分时 bar 提供器（单测/外部注入）。; extra_outputs: 可选输出容器（throttle_log/position_lifetime）。; htf_reference_df: 可选跨 interval HTF 参考样本。
            - 为空时：保持旧行为，使用 ``prediction_df`` 作为 HTF 参考；; - 非空时：用于构建 day/60min 等 HTF state，解决单 interval 评估时
              ``htf_missing`` 过多的问题。
    """
    monthly_cols = ['month', 'trade_count', 'win_count', 'loss_count', 'win_rate', 'gross_pnl', 'net_pnl', 'month_start_equity', 'month_end_equity', 'monthly_return_pct', 'monthly_excess_return_pct', 'cum_return_pct']; summary_cols = ['oot_rows', 'executed_rows', 'trade_count', 'selected_rows', 'blocked_rows', 'blocked_trade_filter_rows', 'blocked_regime_gate_rows', 'blocked_mfe_mae_rows', 'blocked_final_decision_rows', 'blocked_margin_cash_rows', 'blocked_leverage_rows', 'blocked_limit_move_rows', 'blocked_daily_position_rows', 'blocked_weekly_drawdown_rows', 'blocked_weekly_budget_rows', 'blocked_monthly_drawdown_rows', 'blocked_symbol_cap_rows', 'blocked_total_notional_rows', 'blocked_symbol_concurrent_rows', 'blocked_total_concurrent_rows', 'blocked_htf_rows', 'blocked_ranker_rows', 'blocked_throttle_rows', 'blocked_pyramid_rows', 'exit_datetime_fixup_rows', 'stop_loss_exit_rows', 'trailing_stop_exit_rows', 'horizon_exit_rows', 'monthly_obs', 'gross_pnl', 'net_pnl', 'roll_cost_total', 'total_return_pct', 'avg_monthly_return_pct', 'std_monthly_return_pct', 'monthly_excess_return_pct', 'std_monthly_excess_return_pct', 'monthly_sharpe', 'max_drawdown_pct', 'annualized_return_pct', 'calmar_like']
    trade_cols = ['datetime', 'entry_datetime', 'signal_datetime', 'exit_datetime', 'exit_datetime_fixup', 'symbol', 'exchange', 'interval', 'signal_type', 'spread_pair_key', 'spread_side', 'spread_leg_id', 'spread_zscore_at_entry', 'spread_zscore_at_exit', 'spread_pnl_pct', 'side', 'entry_action', 'exit_action', 'execution_status', 'block_reason', 'entry_fill_datetime', 'entry_fill_price', 'planned_exit_datetime', 'planned_exit_price', 'stop_loss_price', 'stop_hit_datetime', 'stop_hit_price', 'stop_triggered', 'final_exit_datetime', 'final_exit_price', 'stop_tracking_interval', 'exit_reason', 'trailing_activated', 'trailing_stop_price', 'trailing_tp_active', 'trailing_tp_highwater', 'extensions_used', 'horizon_extended_to', 'pos_id', 'layer_id', 'layer_interval', 'htf_alignment', 'throttle_level_at_entry', 'ranker_score', 'ranker_score_threshold', 'model_signal_type', 'window_id', 'pred_split', 'entry_price', 'exit_price_ref', 'trigger', 'stop_price', 'trade_filter_prob', 'trade_filter_prob_pctl', 'trade_filter_cluster', 'trade_filter_gate_key', 'trade_filter_gate_mode', 'trade_filter_gate_score', 'trade_filter_gate_threshold', 'trend_aware_threshold_delta', 'trend_aware_relaxed', 'risk_effective_threshold', 'risk_lots_mult', 'risk_block_reason', 'liquidity_blocked', 'liquidity_block_reason', 'bull_strength_proxy', 'bull_mode', 'pred_regime_label', 'regime_label', 'ma_alignment', 'pred_mfe_atr', 'pred_mae_atr', 'final_decision_score', 'final_decision_model_kind', 'hold_extend_score', 'recommended_horizon_extension_bars', 'pyramid_add_score', 'pyramid_size_mult', 'pyramid_add_score_threshold', 'pyramid_size_mult_used', 'future_mfe_atr', 'future_mae_atr', 'future_pnl_atr', 'realized_return_atr', 'position_scale', 'position_notional', 'entry_amount', 'entry_margin', 'exit_amount', 'position_qty', 'contract_size', 'lot_size', 'margin_rate_used', 'max_loss_amount', 'expected_loss_pct', 'expected_loss_amount', 'available_cash_before_entry', 'margin_used_before_entry', 'open_notional_before_entry', 'open_notional_at_entry', 'position_notional_after_trade', 'weekly_drawdown_pct_before_entry', 'gross_return_pct', 'trade_return_pct', 'net_return_pct', 'gross_pnl', 'cost_pct', 'net_pnl', 'roll_cost', 'pnl_amount', 'equity_before', 'equity_after', 'month']; throttle_cols = ['timestamp', 'equity', 'drawdown_pct', 'weekly_return_pct', 'monthly_return_pct', 'level', 'score_threshold']

    def _init_empty_extra_outputs() -> None:
        if extra_outputs is None:
            return
        extra_outputs['throttle_log'] = pd.DataFrame(columns=throttle_cols); extra_outputs['position_lifetime'] = _build_position_lifetime_table(pd.DataFrame(columns=trade_cols))
    if prediction_df.empty:
        _init_empty_extra_outputs(); return (pd.DataFrame(columns=monthly_cols), pd.DataFrame(columns=summary_cols), pd.DataFrame(columns=trade_cols))
    df = prediction_df.copy(); from cta.config.symbol_disable import mask_disabled_rows; df = mask_disabled_rows(df, symbol_column='symbol')
    blocked_signal_types = {
        str(signal_type).strip().lower()
        for signal_type in getattr(cfg, "signal_type_blacklist", ())
        if str(signal_type).strip()
    }
    if blocked_signal_types and "signal_type" in df.columns:
        signal_type_series = df["signal_type"].astype(str).str.strip().str.lower()
        before_rows = int(len(df))
        df = df.loc[~signal_type_series.isin(blocked_signal_types)].copy()
        removed = before_rows - int(len(df))
        if removed > 0:
            logger.info(
                "signal_type_blacklist filtered %d rows (blocked=%s)",
                removed,
                sorted(blocked_signal_types),
            )
    if cfg.use_test_split_only and 'pred_split' in df.columns:
        df = df.loc[df['pred_split'].astype(str).str.lower() == 'test'].copy()
    if df.empty:
        _init_empty_extra_outputs(); return (pd.DataFrame(columns=monthly_cols), pd.DataFrame(columns=summary_cols), pd.DataFrame(columns=trade_cols))
    if cfg.use_last_window_only and 'window_id' in df.columns:
        w = pd.to_numeric(df['window_id'], errors='coerce')
        if w.notna().any():
            df = df.loc[w == w.max()].copy()
    if df.empty:
        _init_empty_extra_outputs(); return (pd.DataFrame(columns=monthly_cols), pd.DataFrame(columns=summary_cols), pd.DataFrame(columns=trade_cols))
    oot_rows = int(len(df))
    htf_reference = resolve_htf_reference_df(df=df, cfg=cfg, htf_reference_df=htf_reference_df)
    exec_mask = pd.to_numeric(df.get('is_executed', 0), errors='coerce').fillna(0).astype(int) == 1
    if cfg.require_executed_only:
        df = df.loc[exec_mask].copy()
    executed_rows = int(exec_mask.sum()) if cfg.require_executed_only else int(len(df))
    if df.empty:
        summary = pd.DataFrame([{'oot_rows': oot_rows, 'executed_rows': executed_rows, 'trade_count': 0, 'selected_rows': 0, 'blocked_rows': 0, 'blocked_trade_filter_rows': 0, 'blocked_regime_gate_rows': 0, 'blocked_mfe_mae_rows': 0, 'blocked_final_decision_rows': 0, 'blocked_margin_cash_rows': 0, 'blocked_leverage_rows': 0, 'blocked_limit_move_rows': 0, 'blocked_daily_position_rows': 0, 'blocked_weekly_drawdown_rows': 0, 'blocked_weekly_budget_rows': 0, 'blocked_monthly_drawdown_rows': 0, 'blocked_symbol_cap_rows': 0, 'blocked_total_notional_rows': 0, 'blocked_symbol_concurrent_rows': 0, 'blocked_total_concurrent_rows': 0, 'blocked_htf_rows': 0, 'blocked_ranker_rows': 0, 'blocked_throttle_rows': 0, 'blocked_pyramid_rows': 0, 'exit_datetime_fixup_rows': 0, 'stop_loss_exit_rows': 0, 'trailing_stop_exit_rows': 0, 'horizon_exit_rows': 0, 'monthly_obs': 0, 'gross_pnl': 0.0, 'net_pnl': 0.0, 'roll_cost_total': 0.0, 'total_return_pct': 0.0, 'avg_monthly_return_pct': float('nan'), 'std_monthly_return_pct': float('nan'), 'monthly_excess_return_pct': float('nan'), 'std_monthly_excess_return_pct': float('nan'), 'monthly_sharpe': float('nan'), 'max_drawdown_pct': float('nan'), 'annualized_return_pct': float('nan'), 'calmar_like': float('nan')}]); return (pd.DataFrame(columns=monthly_cols), summary, pd.DataFrame(columns=trade_cols))
    df, gate, model_block_reason = apply_oot_model_gates(df, cfg)
    df = apply_risk_orchestrator_columns(df, cfg)
    risk_block_reason = df.get("risk_block_reason", pd.Series([""] * len(df), index=df.index)).astype(str)
    risk_block_mask = risk_block_reason.str.strip() != ""
    if bool(risk_block_mask.any()):
        model_block_reason.loc[risk_block_mask & (model_block_reason == "")] = risk_block_reason.loc[
            risk_block_mask
        ]
        gate = gate & (~risk_block_mask)
    df = apply_oot_liquidity_floor_guard(df, cfg)
    liquidity_block_mask = df.get("liquidity_blocked", pd.Series([False] * len(df), index=df.index)).astype(bool)
    if bool(liquidity_block_mask.any()):
        model_block_reason.loc[liquidity_block_mask & (model_block_reason == "")] = "blocked_liquidity_floor"
        gate = gate & (~liquidity_block_mask)
    selected = df.copy(); selected['_model_pass'] = gate.astype(bool); selected['_model_block_reason'] = model_block_reason.astype(str); selected_rows = int(gate.sum()); selected['datetime'] = _to_dt_mixed(selected['datetime'])
    if 'signal_datetime' in selected.columns:
        selected['signal_datetime'] = _to_dt_mixed(selected['signal_datetime'])
    if 'entry_datetime' in selected.columns:
        selected['entry_datetime'] = _to_dt_mixed(selected['entry_datetime'])
    else:
        selected['entry_datetime'] = selected['datetime']
    if 'exit_datetime' in selected.columns:
        selected['exit_datetime'] = _to_dt_mixed(selected['exit_datetime'])
    else:
        selected['exit_datetime'] = pd.NaT
    exit_missing_mask = selected['exit_datetime'].isna(); selected['entry_datetime'] = selected['entry_datetime'].fillna(selected['datetime']); selected['exit_datetime'] = selected['exit_datetime'].fillna(selected['entry_datetime']); selected['exit_datetime_fixup'] = 0
    if exit_missing_mask.any():
        selected.loc[exit_missing_mask, 'exit_datetime'] = selected.loc[exit_missing_mask, 'entry_datetime'] + pd.to_timedelta(1, unit='us'); selected.loc[exit_missing_mask, 'exit_datetime_fixup'] = 1
    non_increasing_exit = selected['exit_datetime'] <= selected['entry_datetime']
    if non_increasing_exit.any():
        selected.loc[non_increasing_exit, 'exit_datetime'] = selected.loc[non_increasing_exit, 'entry_datetime'] + pd.to_timedelta(1, unit='us'); selected.loc[non_increasing_exit, 'exit_datetime_fixup'] = 1
    exit_fixup_rows = int(pd.to_numeric(selected.get('exit_datetime_fixup', 0), errors='coerce').fillna(0).astype(int).sum())
    if exit_fixup_rows > 0:
        logger.warning('exit_datetime fixup applied on %d rows (missing/non-increasing exit timestamps).', exit_fixup_rows)
    selected = selected.dropna(subset=['datetime', 'entry_datetime', 'exit_datetime']).copy()
    if selected.empty:
        _init_empty_extra_outputs(); return (pd.DataFrame(columns=monthly_cols), pd.DataFrame(columns=summary_cols), pd.DataFrame(columns=trade_cols))
    realized_atr = pd.to_numeric(selected.get('future_mfe_atr', 0.0), errors='coerce').fillna(0.0) - float(cfg.mae_penalty) * pd.to_numeric(selected.get('future_mae_atr', 0.0), errors='coerce').fillna(0.0); selected['realized_return_atr'] = realized_atr; gross_ret_pct = realized_atr * float(cfg.risk_per_trade_pct)
    # 按 (cluster, interval) 解析每行 intrabar_stop_loss_pct（覆盖全局默认，详见 cfg.intrabar_stop_loss_pct_by_cluster_interval）
    intrabar_stop_pct_series = pd.Series(_resolve_per_row_intrabar_stop_pct(selected, cfg), index=selected.index, dtype=float)
    trade_ret_pct = np.maximum(gross_ret_pct, -intrabar_stop_pct_series); cost_pct_series = pd.Series(_resolve_per_row_cost_pct(selected, cfg), index=selected.index, dtype=float); net_ret_pct = trade_ret_pct - cost_pct_series; selected['gross_return_pct'] = pd.to_numeric(gross_ret_pct, errors='coerce').fillna(0.0); selected['trade_return_pct'] = pd.to_numeric(net_ret_pct, errors='coerce').fillna(0.0); selected['net_return_pct'] = selected['trade_return_pct']; selected['cost_pct'] = cost_pct_series.values; selected['month'] = selected['exit_datetime'].dt.to_period('M').dt.to_timestamp(); selected = selected.sort_values(['entry_datetime', 'exit_datetime']).reset_index(drop=True); n = len(selected)
    intrabar_stop_pct_arr = _resolve_per_row_intrabar_stop_pct(selected, cfg)
    # 与 cost_pct 列对齐的逐行数组；用于 intrabar 回放循环中对每笔的真实成本扣减。
    cost_pct_arr = _resolve_per_row_cost_pct(selected, cfg)
    if '_model_pass' not in selected.columns:
        selected['_model_pass'] = True
    if '_model_block_reason' not in selected.columns:
        selected['_model_block_reason'] = ''
    model_pass = selected['_model_pass'].astype(bool); model_reason = selected['_model_block_reason'].astype(str); selected['execution_status'] = np.where(model_pass, 'pending', model_reason.replace('', BR_BLOCKED_FINAL_DECISION_GATE)); selected['block_reason'] = np.where(model_pass, '', model_reason.replace('', BR_BLOCKED_FINAL_DECISION_GATE)); selected['equity_before'] = np.nan; selected['equity_after'] = np.nan; selected['gross_pnl'] = 0.0; selected['net_pnl'] = 0.0; selected['roll_cost'] = 0.0; selected['pnl_amount'] = 0.0; selected['max_loss_amount'] = np.nan; selected['position_scale'] = 0.0; selected['position_notional'] = 0.0; selected['entry_amount'] = 0.0; selected['entry_margin'] = 0.0; selected['exit_amount'] = 0.0; selected['position_qty'] = np.nan; selected['contract_size'] = np.nan; selected['lot_size'] = np.nan; selected['margin_rate_used'] = np.nan; selected['expected_loss_pct'] = np.nan; selected['expected_loss_amount'] = np.nan; selected['available_cash_before_entry'] = np.nan; selected['margin_used_before_entry'] = np.nan; selected['open_notional_before_entry'] = np.nan; selected['open_notional_at_entry'] = np.nan; selected['position_notional_after_trade'] = np.nan; selected['weekly_drawdown_pct_before_entry'] = np.nan; selected['entry_fill_datetime'] = pd.NaT; selected['entry_fill_price'] = np.nan; selected['planned_exit_datetime'] = selected['exit_datetime']; selected['planned_exit_price'] = np.nan; selected['stop_loss_price'] = np.nan; selected['stop_hit_datetime'] = pd.NaT; selected['stop_hit_price'] = np.nan; selected['stop_triggered'] = 0; selected['final_exit_datetime'] = selected['exit_datetime']; selected['final_exit_price'] = np.nan; selected['stop_tracking_interval'] = ''; selected['exit_reason'] = ''; selected['trailing_activated'] = 0; selected['trailing_stop_price'] = np.nan; selected['trailing_tp_active'] = 0; selected['trailing_tp_highwater'] = np.nan; selected['pos_id'] = ''; selected['layer_id'] = np.nan; selected['layer_interval'] = ''; selected['htf_alignment'] = ''
    selected['throttle_level_at_entry'] = ''; selected['ranker_score'] = np.nan; selected['ranker_score_threshold'] = np.nan
    if 'pred_mae_atr' in selected.columns:
        pred_mae_series = pd.to_numeric(selected['pred_mae_atr'], errors='coerce')
    else:
        pred_mae_series = pd.Series(np.nan, index=selected.index, dtype=float)
    pred_mae_arr = pred_mae_series.to_numpy()
    hold_extend_arr = pd.to_numeric(
        selected.get('hold_extend_score', pd.Series(np.nan, index=selected.index, dtype=float)),
        errors='coerce',
    ).to_numpy(dtype=float)
    recommended_ext_arr = pd.to_numeric(
        selected.get('recommended_horizon_extension_bars', pd.Series(0, index=selected.index, dtype=float)),
        errors='coerce',
    ).fillna(0).astype(int).to_numpy()
    pyramid_add_score_arr = pd.to_numeric(
        selected.get('pyramid_add_score', pd.Series(np.nan, index=selected.index, dtype=float)),
        errors='coerce',
    ).to_numpy(dtype=float)
    pyramid_size_mult_arr = pd.to_numeric(
        selected.get('pyramid_size_mult', pd.Series(np.nan, index=selected.index, dtype=float)),
        errors='coerce',
    ).to_numpy(dtype=float)
    selected['pyramid_add_score_threshold'] = np.nan
    selected['pyramid_size_mult_used'] = np.nan
    selected['extensions_used'] = 0
    selected['horizon_extended_to'] = 0
    if 'entry_price' in selected.columns:
        entry_price_series = pd.to_numeric(selected['entry_price'], errors='coerce')
    else:
        entry_price_series = pd.Series(np.nan, index=selected.index, dtype=float)
    entry_price_arr = entry_price_series.to_numpy(); gross_pct_arr = np.array(pd.to_numeric(selected['gross_return_pct'], errors='coerce').fillna(0.0), dtype=float, copy=True); net_pct_arr = np.array(pd.to_numeric(selected['trade_return_pct'], errors='coerce').fillna(0.0), dtype=float, copy=True); side_arr = selected.get('side', pd.Series([''] * n, index=selected.index)).astype(str).str.lower().to_numpy(); symbol_arr = selected.get('symbol', pd.Series([''] * n, index=selected.index)).astype(str).str.upper().to_numpy(); exchange_arr = selected.get('exchange', pd.Series([''] * n, index=selected.index)).astype(str).str.upper().to_numpy(); signal_type_arr = selected.get('signal_type', pd.Series([''] * n, index=selected.index)).astype(str).str.strip().str.lower().to_numpy()
    if 'entry_feature_is_limit_up_close' in selected.columns:
        limit_up_series = pd.to_numeric(selected['entry_feature_is_limit_up_close'], errors='coerce').fillna(0.0)
    elif 'feature_is_limit_up_close' in selected.columns:
        limit_up_series = pd.to_numeric(selected['feature_is_limit_up_close'], errors='coerce').fillna(0.0)
    else:
        limit_up_series = pd.Series(0.0, index=selected.index, dtype=float)
    if 'entry_feature_is_limit_down_close' in selected.columns:
        limit_down_series = pd.to_numeric(selected['entry_feature_is_limit_down_close'], errors='coerce').fillna(0.0)
    elif 'feature_is_limit_down_close' in selected.columns:
        limit_down_series = pd.to_numeric(selected['feature_is_limit_down_close'], errors='coerce').fillna(0.0)
    else:
        limit_down_series = pd.Series(0.0, index=selected.index, dtype=float)
    limit_up_arr = limit_up_series.to_numpy(dtype=float); limit_down_arr = limit_down_series.to_numpy(dtype=float)
    if 'atr_pct_at_entry' in selected.columns:
        atr_pct_arr = pd.to_numeric(selected['atr_pct_at_entry'], errors='coerce').to_numpy(dtype=float)
    elif 'feature_atr14' in selected.columns:
        atr_raw = pd.to_numeric(selected['feature_atr14'], errors='coerce').to_numpy(dtype=float); atr_pct_arr = np.divide(atr_raw, np.where(np.isfinite(entry_price_arr) & (entry_price_arr > 0), entry_price_arr, np.nan))
    else:
        atr_pct_arr = np.full(n, np.nan, dtype=float)
    pl_cfg = getattr(cfg, 'portfolio_logic', None)
    use_pl_runtime = bool(getattr(cfg, 'use_portfolio_logic_runtime', False))
    use_pl_trailing = bool(use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, 'enable_trailing', False)))
    use_pl_htf = bool(use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, 'enable_htf_gate', False)))
    use_pl_ranker = bool(use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, 'enable_ranker', False)))
    use_pl_throttle = bool(use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, 'enable_risk_throttle', False)))
    use_pl_pyramid = bool(use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, 'enable_pyramid', False)))
    runtime_caps_cfg = None
    if use_pl_runtime and pl_cfg is not None:
        base_caps = pl_cfg.caps
        symbol_cap_pct = float(np.clip(
            float(cfg.max_symbol_notional_pct),
            1e-9,
            float(base_caps.max_cluster_notional_pct),
        ))
        runtime_caps_cfg = dataclasses.replace(
            base_caps,
            max_total_positions=max(1, int(cfg.max_concurrent_positions_total)),
            max_total_per_cluster=max(
                int(base_caps.max_total_per_cluster),
                max(1, int(cfg.max_concurrent_positions_total)),
            ),
            max_per_symbol=max(1, int(cfg.max_concurrent_positions_per_symbol)),
            max_symbol_notional_pct=symbol_cap_pct,
        )
    htf_gate: HtfGate | None = None; htf_ref_by_interval: dict[str, pd.DataFrame] = {}; htf_state_cache: dict[pd.Timestamp, dict[tuple[str, str], dict[str, Any]]] = {}
    if use_pl_htf:
        ref = htf_reference.copy()

        def _to_dt_per_interval(series: pd.Series) -> pd.Series:
            try:
                return pd.to_datetime(series, errors='coerce', format='mixed')
            except TypeError:
                return pd.to_datetime(series, errors='coerce')
        interval_rank = dict(pl_cfg.interval_gate.interval_rank or {}); pred_intervals_norm: set[str] = set()
        if 'interval' in df.columns:
            pred_intervals_norm = {normalize_portfolio_interval(v) for v in df['interval'].dropna().astype(str).unique() if str(v).strip()}
        configured_intervals = tuple((normalize_portfolio_interval(i) for i in pl_cfg.interval_gate.htf_intervals))
        extra_pred_intervals = sorted(
            (itv for itv in pred_intervals_norm if itv not in configured_intervals),
            key=lambda itv: float(interval_rank.get(itv, 0.0)),
            reverse=True,
        )
        intervals_to_load = list(dict.fromkeys([*configured_intervals, *extra_pred_intervals]))
        ref_intervals_seen: list[str] = []
        ref_interval = ref.get('interval', pd.Series([''] * len(ref), index=ref.index))
        ref_interval_norm = ref_interval.map(normalize_portfolio_interval)
        for itv in intervals_to_load:
            part = ref.loc[ref_interval_norm == normalize_portfolio_interval(itv)].copy()
            if 'datetime' in part.columns:
                part['datetime'] = _to_dt_per_interval(part['datetime'])
            if not part.empty:
                key = normalize_portfolio_interval(itv); htf_ref_by_interval[key] = part; ref_intervals_seen.append(key)
        if not ref_intervals_seen:
            logger.warning('HTF gate enabled but htf_reference contains no rows for any of %s; disabling HTF gate for this run (see cta/docs/block_reason.md §4).', intervals_to_load); use_pl_htf = False; htf_gate = None
        elif set(ref_intervals_seen) != set(configured_intervals):
            narrowed_cfg = dataclasses.replace(pl_cfg.interval_gate, htf_intervals=tuple(ref_intervals_seen)); htf_gate = HtfGate(narrowed_cfg); logger.info('HTF intervals adjusted for runtime from %s to %s (htf_reference available intervals).', configured_intervals, tuple(ref_intervals_seen))
            if len(ref_intervals_seen) == 1:
                logger.warning('HTF gate narrowed to single interval %s — this degenerates to self-consistency gate (no actual higher-timeframe filtering). Pass htf_reference_df with higher interval predictions for true HTF semantics.', ref_intervals_seen[0])
        else:
            htf_gate = HtfGate(pl_cfg.interval_gate)
    ranker: OpportunityRanker | None = None
    if use_pl_ranker:
        # 由 signal_type_size_multiplier 派生抢占名额的排序优先级：mult>1 加分、mult<1 减分。
        # 2026-05-31 目标配比版：派生系数 1.00，尽量把有限槽位向高质量 signal_type 倾斜。
        ranker_cfg = pl_cfg.ranker
        if not getattr(ranker_cfg, "signal_type_rank_bonus", None):
            derived_bonus = {
                str(st): (float(mult) - 1.0) * 1.00
                for st, mult in dict(cfg.signal_type_size_multiplier).items()
            }
            ranker_cfg = dataclasses.replace(ranker_cfg, signal_type_rank_bonus=derived_bonus)
        ranker = OpportunityRanker(
            ranker_cfg,
            interval_rank=dict(pl_cfg.interval_gate.interval_rank),
        )
    risk_throttle: RiskThrottle | None = None; equity_tracker: EquityTracker | None = None; throttle_level = None
    if use_pl_throttle:
        risk_throttle = RiskThrottle(pl_cfg.risk_throttle); equity_tracker = EquityTracker()
    pyramid_manager: PyramidManager | None = None; active_pyramid_by_sym_dir: dict[tuple[str, str, str], PyramidPosition] = {}; idx_layer_ref: dict[int, tuple[PyramidPosition, int]] = {}
    if use_pl_pyramid:
        pyramid_manager = PyramidManager(pl_cfg.pyramid)
    use_intrabar = bool(getattr(cfg, 'use_intrabar_stop_tracking', False))
    preferred_intrabar_raw = str(getattr(cfg, 'intrabar_tracking_interval', '60min'))
    interval_candidates: list[str] = []
    bar_cache_by_interval: dict[str, _IntrabarBarCache] = {}

    def _load_intrabar_bars(idx: int, *, load_end_ts: pd.Timestamp | None = None) -> tuple[pd.DataFrame, str]:
        return (pd.DataFrame(), normalize_interval(preferred_intrabar_raw))

    if use_intrabar:
        preferred_intrabar = normalize_interval(preferred_intrabar_raw)
        fallback_raw = tuple(getattr(cfg, 'intrabar_tracking_fallback_intervals', ()))
        for raw in (preferred_intrabar, *fallback_raw):
            try:
                norm = normalize_interval(str(raw))
            except Exception:
                continue
            if norm not in interval_candidates:
                interval_candidates.append(norm)
        if not interval_candidates:
            interval_candidates = ['60min']
        span_by_symbol: dict[tuple[str, str], tuple[pd.Timestamp, pd.Timestamp]] = {}
        for key, grp in selected.groupby([
            selected.get('symbol', pd.Series([''] * n, index=selected.index)).astype(str).str.upper(),
            selected.get('exchange', pd.Series([''] * n, index=selected.index)).astype(str).str.upper(),
        ]):
            entry_min = pd.to_datetime(grp['entry_datetime'], errors='coerce').min()
            exit_max = pd.to_datetime(grp['exit_datetime'], errors='coerce').max()
            if pd.notna(entry_min) and pd.notna(exit_max):
                span_by_symbol[str(key[0]).upper(), str(key[1]).upper()] = (
                    pd.Timestamp(entry_min),
                    pd.Timestamp(exit_max),
                )
        bar_cache_by_interval = {
            itv: _IntrabarBarCache(interval=itv, date_span_by_symbol=span_by_symbol)
            for itv in interval_candidates
        }

        def _load_intrabar_bars(idx: int, *, load_end_ts: pd.Timestamp | None = None) -> tuple[pd.DataFrame, str]:
            ent = pd.to_datetime(selected.iloc[idx]['entry_datetime'], errors='coerce')
            exi = pd.to_datetime(selected.iloc[idx]['exit_datetime'], errors='coerce')
            if pd.isna(ent) or pd.isna(exi):
                return (pd.DataFrame(), interval_candidates[0])
            end_ts = pd.Timestamp(load_end_ts) if load_end_ts is not None else pd.Timestamp(exi)
            if pd.isna(end_ts) or end_ts < pd.Timestamp(ent):
                end_ts = pd.Timestamp(exi)
            sym = str(symbol_arr[idx]).upper()
            ex = str(exchange_arr[idx]).upper()
            used_interval = interval_candidates[0]
            if intrabar_bar_provider is not None:
                used_interval = preferred_intrabar_raw
                try:
                    bars = intrabar_bar_provider(sym, ex, pd.Timestamp(ent), pd.Timestamp(end_ts), used_interval)
                except Exception:
                    bars = pd.DataFrame()
                return (bars, used_interval)
            bars = pd.DataFrame()
            for itv in interval_candidates:
                bars_try = bar_cache_by_interval[itv].slice(sym, ex, pd.Timestamp(ent), pd.Timestamp(end_ts))
                if not bars_try.empty:
                    bars = bars_try
                    used_interval = itv
                    break
            return (bars, used_interval)

        # intrabar_stop_pct_arr 在文件上方已按 (cluster, interval) 解析；此处 fallback 仅在 array 长度异常时给标量。
        _intrabar_default = float(getattr(cfg, 'intrabar_stop_loss_pct', float(cfg.max_single_loss_pct)))
        for idx in range(n):
            ent = pd.to_datetime(selected.iloc[idx]['entry_datetime'], errors='coerce')
            exi = pd.to_datetime(selected.iloc[idx]['exit_datetime'], errors='coerce')
            if pd.isna(ent) or pd.isna(exi):
                continue
            hint = float(entry_price_arr[idx]) if np.isfinite(entry_price_arr[idx]) else float('nan')
            bars, used_interval = _load_intrabar_bars(idx)
            sym = str(symbol_arr[idx]).upper()
            if use_pl_trailing:
                row_interval = normalize_portfolio_interval(selected.iloc[idx].get('interval', ''))
                row_regime = str(selected.iloc[idx].get('pred_regime_label', '')).strip().lower()
                atr_pct = float(atr_pct_arr[idx]) if idx < len(atr_pct_arr) and np.isfinite(atr_pct_arr[idx]) else None
                _row_stop_pct = float(intrabar_stop_pct_arr[idx]) if idx < len(intrabar_stop_pct_arr) else _intrabar_default
                hold_score = float(hold_extend_arr[idx]) if idx < len(hold_extend_arr) and np.isfinite(hold_extend_arr[idx]) else None
                rec_ext = int(recommended_ext_arr[idx]) if idx < len(recommended_ext_arr) else None
                _cluster = str(infer_symbol_cluster(sym)).strip().lower()
                sim = simulate_trailing_exit(
                    side=str(side_arr[idx]),
                    entry_ts=pd.Timestamp(ent),
                    planned_exit_ts=pd.Timestamp(exi),
                    entry_price_hint=hint,
                    stop_loss_pct=_row_stop_pct,
                    bars=bars,
                    interval=row_interval,
                    atr_pct_at_entry=atr_pct,
                    regime_label=row_regime,
                    cfg=pl_cfg.trailing,
                    horizon_cfg=pl_cfg.horizon_extend if bool(getattr(pl_cfg, 'enable_horizon_extend', False)) else None,
                    hold_extend_score=hold_score,
                    recommended_extension_bars=rec_ext,
                    symbol_cluster=_cluster,
                )
            else:
                _row_stop_pct = float(intrabar_stop_pct_arr[idx]) if idx < len(intrabar_stop_pct_arr) else _intrabar_default
                sim = _simulate_intrabar_exit(
                    side=str(side_arr[idx]),
                    entry_ts=pd.Timestamp(ent),
                    planned_exit_ts=pd.Timestamp(exi),
                    entry_price_hint=hint,
                    stop_loss_pct=_row_stop_pct,
                    bars=bars,
                )
            selected.at[idx, 'entry_fill_datetime'] = sim['entry_fill_datetime']; selected.at[idx, 'entry_fill_price'] = sim['entry_fill_price']; selected.at[idx, 'planned_exit_datetime'] = sim['planned_exit_datetime']; selected.at[idx, 'planned_exit_price'] = sim['planned_exit_price']; selected.at[idx, 'stop_loss_price'] = sim['stop_loss_price']; selected.at[idx, 'stop_hit_datetime'] = sim['stop_hit_datetime']; selected.at[idx, 'stop_hit_price'] = sim['stop_hit_price']; selected.at[idx, 'stop_triggered'] = int(sim['stop_triggered']); selected.at[idx, 'final_exit_datetime'] = sim['final_exit_datetime']; selected.at[idx, 'final_exit_price'] = sim['final_exit_price']; selected.at[idx, 'stop_tracking_interval'] = used_interval; selected.at[idx, 'exit_reason'] = sim['exit_reason']; selected.at[idx, 'trailing_activated'] = int(sim.get('trailing_activated', 0)); selected.at[idx, 'trailing_stop_price'] = sim.get('trailing_stop_price', np.nan); selected.at[idx, 'trailing_tp_active'] = int(sim.get('trailing_tp_active', 0)); selected.at[idx, 'trailing_tp_highwater'] = sim.get('trailing_tp_highwater', np.nan); selected.at[idx, 'extensions_used'] = int(sim.get('extensions_used', 0)); selected.at[idx, 'horizon_extended_to'] = int(sim.get('horizon_extended_to', 0)); ret = float(sim['price_return_pct']) if np.isfinite(sim['price_return_pct']) else float('nan')
            if np.isfinite(ret):
                _row_stop_pct = float(intrabar_stop_pct_arr[idx]) if idx < len(intrabar_stop_pct_arr) else _intrabar_default; _row_cost_pct = float(cost_pct_arr[idx]) if idx < len(cost_pct_arr) else float(cfg.commission_pct_per_trade) + float(cfg.slippage_pct_per_trade); g = max(ret, -_row_stop_pct); gross_pct_arr[idx] = float(g); net_pct_arr[idx] = float(g - _row_cost_pct)
        selected['gross_return_pct'] = pd.Series(gross_pct_arr, index=selected.index, dtype=float); selected['trade_return_pct'] = pd.Series(net_pct_arr, index=selected.index, dtype=float); selected['net_return_pct'] = selected['trade_return_pct']; final_exit_dt = pd.to_datetime(selected['final_exit_datetime'], errors='coerce'); valid_final = final_exit_dt.notna(); selected.loc[valid_final, 'exit_datetime'] = final_exit_dt.loc[valid_final]
    else:
        selected['entry_fill_datetime'] = selected['entry_datetime']; selected['entry_fill_price'] = pd.to_numeric(selected.get('entry_price', np.nan), errors='coerce'); selected['planned_exit_datetime'] = selected['exit_datetime']; selected['final_exit_datetime'] = selected['exit_datetime']; selected['planned_exit_price'] = pd.to_numeric(selected.get('exit_price_ref', np.nan), errors='coerce'); selected['final_exit_price'] = pd.to_numeric(selected.get('exit_price_ref', np.nan), errors='coerce')
        # 非 intrabar tracking 模式下的 stop_loss_price：用 per-row resolver
        _intrabar_default = float(getattr(cfg, 'intrabar_stop_loss_pct', float(cfg.max_single_loss_pct)))
        stop_pct_arr = _resolve_per_row_intrabar_stop_pct(selected, cfg)
        if len(stop_pct_arr) != len(selected):
            stop_pct_arr = np.full(len(selected), _intrabar_default, dtype=float)
        stop_pct_series = pd.Series(stop_pct_arr, index=selected.index, dtype=float)
        side_s = selected.get('side', pd.Series([''] * len(selected), index=selected.index)).astype(str).str.lower(); entry_fill_s = pd.to_numeric(selected.get('entry_fill_price', np.nan), errors='coerce'); selected['stop_loss_price'] = np.where(side_s == 'short', entry_fill_s * (1.0 + stop_pct_series), entry_fill_s * (1.0 - stop_pct_series)); selected['stop_tracking_interval'] = 'atr_proxy'; selected['exit_reason'] = 'atr_proxy'
    entry_fill_arr = pd.to_numeric(selected.get('entry_fill_price', np.nan), errors='coerce').to_numpy(dtype=float); stop_loss_arr = pd.to_numeric(selected.get('stop_loss_price', np.nan), errors='coerce').to_numpy(dtype=float); entry_action_arr: list[str] = []; exit_action_arr: list[str] = []
    for side in side_arr:
        if side == 'long':
            entry_action_arr.append('buy'); exit_action_arr.append('sell')
        elif side == 'short':
            entry_action_arr.append('short'); exit_action_arr.append('cover')
        else:
            entry_action_arr.append('open'); exit_action_arr.append('close')
    selected['entry_action'] = entry_action_arr; selected['exit_action'] = exit_action_arr

    def _week_start(ts: pd.Timestamp) -> pd.Timestamp:
        return (ts.normalize() - pd.Timedelta(days=int(ts.weekday()))).normalize()
    cash = float(cfg.initial_capital); margin_used = 0.0; open_notional = 0.0; runtime_state = PortfolioState(equity=float(cash)); signal_type_open_counts: dict[str, int] = {}; signal_type_open_notional: dict[str, float] = {}; pre_blocked_mask = selected['execution_status'].astype(str) != 'pending'
    if pre_blocked_mask.any():
        selected.loc[pre_blocked_mask, 'equity_before'] = float(cash); selected.loc[pre_blocked_mask, 'equity_after'] = float(cash)
    open_positions: dict[int, dict[str, Any]] = {}; symbol_open_notional: dict[str, float] = {}; symbol_open_count: dict[str, int] = {}; cluster_open_notional: dict[str, float] = {}; cluster_open_count: dict[str, int] = {}; missing_symbol_count = int(pd.Series(symbol_arr).astype(str).str.strip().eq('').sum() + pd.Series(symbol_arr).isna().sum())
    if missing_symbol_count > 0:
        logger.warning('OOT evaluator: %d/%d rows missing symbol; per-symbol cap will be skipped for these rows (organization cap still applies). check upstream feature pipeline.', missing_symbol_count, int(len(symbol_arr)))
    day_key: pd.Timestamp | None = None; day_start_equity = float(cfg.initial_capital); day_new_notional = 0.0; week_key: pd.Timestamp | None = None; week_peak_equity = float(cfg.initial_capital); week_dd_breached = False; month_key: pd.Timestamp | None = None; month_peak_equity = float(cfg.initial_capital); month_dd_breached = False; throttle_log_rows: list[dict[str, Any]] = []; entries_map: dict[pd.Timestamp, list[int]] = {}; exits_map: dict[pd.Timestamp, list[int]] = {}
    for idx in range(n):
        ent = pd.to_datetime(selected.iloc[idx]['entry_datetime'], errors='coerce'); exi = pd.to_datetime(selected.iloc[idx]['exit_datetime'], errors='coerce')
        if pd.isna(ent) or pd.isna(exi) or exi < ent:
            selected.at[idx, 'execution_status'] = 'blocked_invalid_time'; selected.at[idx, 'block_reason'] = 'invalid_time'; continue
        exi_event = exi if exi > ent else ent + pd.Timedelta(1, unit='ns'); entries_map.setdefault(ent, []).append(idx); exits_map.setdefault(exi_event, []).append(idx)
    timeline = sorted(set(entries_map.keys()) | set(exits_map.keys()))
    for ts in timeline:
        cur_day = ts.normalize()
        if day_key is None or cur_day != day_key:
            day_key = cur_day; day_start_equity = float(cash); day_new_notional = 0.0
        cur_week = _week_start(ts)
        if week_key is None or cur_week != week_key:
            week_key = cur_week; week_peak_equity = float(cash); week_dd_breached = False
        cur_month = ts.to_period('M').to_timestamp()
        if month_key is None or cur_month != month_key:
            month_key = cur_month; month_peak_equity = float(cash); month_dd_breached = False
        for idx in exits_map.get(ts, []):
            if idx not in open_positions:
                continue
            pos = open_positions.pop(idx); margin_used = max(0.0, float(margin_used - float(pos['margin']))); open_notional = max(0.0, float(open_notional - float(pos['notional']))); pos_sym = str(pos.get('symbol', '')); pos_signal_type = str(pos.get('signal_type', '')).strip().lower(); runtime_state.remove_position(str(pos.get('pos_id', '')))
            if pos_signal_type:
                signal_type_open_counts[pos_signal_type] = max(0, int(signal_type_open_counts.get(pos_signal_type, 0) - 1))
                if int(signal_type_open_counts.get(pos_signal_type, 0)) <= 0:
                    signal_type_open_counts.pop(pos_signal_type, None)
                signal_type_open_notional[pos_signal_type] = max(0.0, float(signal_type_open_notional.get(pos_signal_type, 0.0) - float(pos['notional'])))
                if float(signal_type_open_notional.get(pos_signal_type, 0.0)) <= 1e-9:
                    signal_type_open_notional.pop(pos_signal_type, None)
            if pos_sym:
                symbol_open_notional[pos_sym] = max(0.0, float(symbol_open_notional.get(pos_sym, 0.0) - float(pos['notional']))); symbol_open_count[pos_sym] = max(0, int(symbol_open_count.get(pos_sym, 0) - 1))
            pos_cluster = str(pos.get('cluster', 'other')); cluster_open_notional[pos_cluster] = max(0.0, float(cluster_open_notional.get(pos_cluster, 0.0) - float(pos['notional']))); cluster_open_count[pos_cluster] = max(0, int(cluster_open_count.get(pos_cluster, 0) - 1))
            if use_pl_pyramid and idx in idx_layer_ref:
                pos_ref, layer_id_ref = idx_layer_ref.pop(idx)
                for layer in pos_ref.layers:
                    if int(layer.layer_id) == int(layer_id_ref):
                        layer.exited = True; break
                sym_dir = (pos_ref.symbol, pos_ref.exchange, pos_ref.direction)
                if len(pos_ref.active_layers) == 0:
                    active_pyramid_by_sym_dir.pop(sym_dir, None)
            gross_pnl = float(pos['notional']) * float(pos['gross_ret_pct']); roll_cost = _calc_roll_cost(symbol=pos_sym, notional=float(pos['notional']), entry_ts=pd.Timestamp(pos.get('entry_ts', ts)), exit_ts=pd.Timestamp(ts), cfg=cfg); net_pnl = float(pos['notional']) * float(pos['net_ret_pct']) - float(roll_cost); cash = float(cash + net_pnl); selected.at[idx, 'equity_before'] = float(pos['entry_equity']); selected.at[idx, 'equity_after'] = float(cash); selected.at[idx, 'gross_pnl'] = gross_pnl; selected.at[idx, 'net_pnl'] = net_pnl; selected.at[idx, 'roll_cost'] = float(roll_cost); selected.at[idx, 'pnl_amount'] = net_pnl; selected.at[idx, 'entry_amount'] = float(pos['notional']); selected.at[idx, 'exit_amount'] = float(pos['notional'] + net_pnl); selected.at[idx, 'position_notional_after_trade'] = float(open_notional); selected.at[idx, 'execution_status'] = 'executed'
        week_peak_equity = max(float(week_peak_equity), float(cash)); month_peak_equity = max(float(month_peak_equity), float(cash))
        if week_peak_equity > 0:
            dd_now = float(cash / week_peak_equity - 1.0)
            if np.isfinite(dd_now) and dd_now <= -float(cfg.weekly_max_drawdown_pct):
                week_dd_breached = True
        if month_peak_equity > 0:
            mdd_now = float(cash / month_peak_equity - 1.0)
            if np.isfinite(mdd_now) and mdd_now <= -float(cfg.monthly_max_drawdown_pct):
                month_dd_breached = True
        pending_entries = [idx for idx in entries_map.get(ts, []) if str(selected.at[idx, 'execution_status']) == 'pending']; entry_order = list(pending_entries); score_threshold_for_bar = float('nan'); throttle_level_name = ''
        if use_pl_throttle and risk_throttle is not None and (equity_tracker is not None):
            equity_tracker.on_bar(float(cash), pd.Timestamp(ts)); snap = equity_tracker.snapshot(); throttle_level = risk_throttle.compute(snap, throttle_level); throttle_level_name = str(throttle_level.name); score_threshold_for_bar = float(throttle_level.score_pctl_threshold) / 100.0; throttle_log_rows.append({'timestamp': pd.Timestamp(ts), 'equity': float(cash), 'drawdown_pct': float(snap.drawdown_pct), 'weekly_return_pct': float(snap.weekly_return_pct), 'monthly_return_pct': float(snap.monthly_return_pct), 'level': throttle_level_name, 'score_threshold': float(score_threshold_for_bar)})
            if str(throttle_level.name) == 'halt':
                for idx in pending_entries:
                    selected.at[idx, 'execution_status'] = 'blocked_throttle_halt'; selected.at[idx, 'block_reason'] = 'blocked_throttle_halt'; selected.at[idx, 'equity_before'] = float(cash); selected.at[idx, 'equity_after'] = float(cash); selected.at[idx, 'throttle_level_at_entry'] = throttle_level_name
                entry_order = []
        if use_pl_htf and htf_gate is not None and entry_order:
            if ts not in htf_state_cache:
                htf_state_cache[ts] = htf_gate.compute_htf_state(htf_ref_by_interval, as_of=pd.Timestamp(ts))
            htf_state_now = htf_state_cache.get(ts, {}); gate_rows = []
            for idx in entry_order:
                gate_rows.append({'_row_idx': int(idx), 'symbol': str(symbol_arr[idx]).upper(), 'exchange': str(exchange_arr[idx]).upper(), 'direction': str(side_arr[idx]).lower(), 'interval': normalize_portfolio_interval(selected.iloc[idx].get('interval', ''))})
            gate_df = pd.DataFrame(gate_rows); gate_out = htf_gate.filter(gate_df, htf_state=htf_state_now, current_time=pd.Timestamp(ts)); allow_mask = gate_out.get('htf_allowed', pd.Series([True] * len(gate_out))); blocked_idx = gate_out.loc[~allow_mask, '_row_idx'].tolist()
            for ridx in blocked_idx:
                i = int(ridx); selected.at[i, 'execution_status'] = 'blocked_htf_gate'; selected.at[i, 'block_reason'] = str(gate_out.loc[gate_out['_row_idx'] == ridx, 'htf_block_reason'].astype(str).iloc[0]); selected.at[i, 'equity_before'] = float(cash); selected.at[i, 'equity_after'] = float(cash)
            entry_order = [int(x) for x in gate_out.loc[allow_mask, '_row_idx'].tolist()]
            for _, row in gate_out.iterrows():
                i = int(row['_row_idx']); selected.at[i, 'htf_alignment'] = str(row.get('htf_alignment', 'neutral'))
        if use_pl_ranker and ranker is not None and entry_order:
            cand_rows: list[dict[str, Any]] = []
            blocked_by_signal_type_cap: set[int] = set()
            for idx in entry_order:
                signal_type_key = str(signal_type_arr[idx]).strip().lower() if idx < len(signal_type_arr) else ''
                interval_key = normalize_portfolio_interval(selected.iloc[idx].get('interval', ''))
                signal_type_cap = cfg.resolve_signal_type_max_concurrent(signal_type_key)
                signal_type_count_now = int(signal_type_open_counts.get(signal_type_key, 0))
                if signal_type_cap is not None and signal_type_count_now >= int(signal_type_cap):
                    blocked_by_signal_type_cap.add(int(idx))
                    continue

                prob_raw = pd.to_numeric(
                    pd.Series([selected.iloc[idx].get('trade_filter_prob', np.nan)]),
                    errors='coerce',
                ).iloc[0]
                prob_pctl = pd.to_numeric(
                    pd.Series([selected.iloc[idx].get('trade_filter_prob_pctl', np.nan)]),
                    errors='coerce',
                ).iloc[0]
                if not np.isfinite(prob_pctl):
                    prob_pctl = float(np.clip((float(prob_raw) if np.isfinite(prob_raw) else 0.0) * 100.0, 0.0, 100.0))
                prob_pctl = float(
                    np.clip(
                        float(prob_pctl) - float(cfg.resolve_ranker_prob_pctl_delta(signal_type_key, interval_key)),
                        0.0,
                        100.0,
                    )
                )
                cand_rows.append(
                    {
                        '_row_idx': int(idx),
                        'symbol': str(symbol_arr[idx]).upper(),
                        'exchange': str(exchange_arr[idx]).upper(),
                        'interval': interval_key,
                        'direction': str(side_arr[idx]).lower(),
                        'cluster_name': infer_symbol_cluster(str(symbol_arr[idx]).upper()),
                        'signal_type': signal_type_key,
                        'trade_filter_prob_pctl': float(prob_pctl),
                        'pred_mfe_atr': float(
                            pd.to_numeric(
                                pd.Series([selected.iloc[idx].get('pred_mfe_atr', np.nan)]),
                                errors='coerce',
                            ).iloc[0]
                        ),
                        'pred_mae_atr': float(
                            pd.to_numeric(
                                pd.Series([selected.iloc[idx].get('pred_mae_atr', np.nan)]),
                                errors='coerce',
                            ).iloc[0]
                        ),
                        'htf_alignment': str(selected.iloc[idx].get('htf_alignment', 'neutral') or 'neutral'),
                        'base_notional': float(cash) * float(cfg.max_position_scale),
                    }
                )

            for i in blocked_by_signal_type_cap:
                selected.at[i, 'execution_status'] = BR_BLOCKED_SIGNAL_TYPE_CONCURRENT
                selected.at[i, 'block_reason'] = BR_BLOCKED_SIGNAL_TYPE_CONCURRENT
                selected.at[i, 'equity_before'] = float(cash)
                selected.at[i, 'equity_after'] = float(cash)

            cand_df = pd.DataFrame(cand_rows)
            if not cand_df.empty:
                scored = ranker.score(cand_df, htf_state={})
                signal_type_col = scored.get(
                    'signal_type',
                    pd.Series([''] * len(scored), index=scored.index, dtype=str),
                ).astype(str).str.strip().str.lower()
                bonus_map = dict(getattr(ranker.cfg, 'signal_type_rank_bonus', {}) or {})
                rank_key = (
                    pd.to_numeric(scored.get('score'), errors='coerce').fillna(float('-inf'))
                    + signal_type_col.map(lambda st: float(bonus_map.get(st, 0.0))).astype(float)
                )
                scored['_signal_type_key'] = signal_type_col
                scored['_rank_key'] = rank_key
                keep_mask = pd.Series(True, index=scored.index, dtype=bool)
                for st_key, grp in scored.groupby('_signal_type_key', sort=False, dropna=False):
                    cap = cfg.resolve_signal_type_max_concurrent(st_key)
                    if cap is None:
                        continue
                    remain = int(cap) - int(signal_type_open_counts.get(str(st_key), 0))
                    if remain <= 0:
                        keep_mask.loc[grp.index] = False
                        continue
                    if len(grp) > remain:
                        drop_idx = grp.sort_values('_rank_key', ascending=False).index[int(remain):]
                        keep_mask.loc[drop_idx] = False
                blocked_due_cap = scored.loc[~keep_mask, '_row_idx']
                for ridx in blocked_due_cap.tolist():
                    i = int(ridx)
                    selected.at[i, 'execution_status'] = BR_BLOCKED_SIGNAL_TYPE_CONCURRENT
                    selected.at[i, 'block_reason'] = BR_BLOCKED_SIGNAL_TYPE_CONCURRENT
                    selected.at[i, 'equity_before'] = float(cash)
                    selected.at[i, 'equity_after'] = float(cash)
                scored = scored.loc[keep_mask].drop(columns=['_signal_type_key', '_rank_key'], errors='ignore')
            else:
                scored = pd.DataFrame(columns=['_row_idx', 'score'])

            for _, row in scored.iterrows():
                i = int(row['_row_idx'])
                selected.at[i, 'ranker_score'] = float(row.get('score', np.nan))

            state = PortfolioState.from_dict(runtime_state.to_dict())
            state.equity = float(cash)
            caps_eff = runtime_caps_cfg if runtime_caps_cfg is not None else pl_cfg.caps
            min_prob_pctl_for_bar = 0.0
            if use_pl_throttle and risk_throttle is not None and (throttle_level is not None):
                caps_eff = risk_throttle.apply_to_caps(caps_eff, throttle_level)
                score_threshold_for_bar = max(
                    float(pl_cfg.ranker.score_threshold_baseline),
                    float(throttle_level.score_pctl_threshold) / 100.0,
                )
                min_prob_pctl_for_bar = float(getattr(throttle_level, 'effective_min_prob_pctl', 0.0))
            else:
                score_threshold_for_bar = float(pl_cfg.ranker.score_threshold_baseline)

            picks = ranker.allocate(
                scored,
                state=state,
                caps=caps_eff,
                score_threshold=float(score_threshold_for_bar),
                min_prob_pctl=float(min_prob_pctl_for_bar),
            )
            selected_idx = {int(x) for x in picks.get('_row_idx', pd.Series([], dtype=int)).tolist()}
            ranker_entry_order = [int(x) for x in scored.get('_row_idx', pd.Series([], dtype=int)).tolist()]
            for idx in ranker_entry_order:
                selected.at[idx, 'ranker_score_threshold'] = float(score_threshold_for_bar)
                selected.at[idx, 'ranker_prob_pctl_threshold'] = float(min_prob_pctl_for_bar)
                if int(idx) not in selected_idx:
                    selected.at[idx, 'execution_status'] = 'blocked_ranker'
                    selected.at[idx, 'block_reason'] = 'ranker_dropped'
                    selected.at[idx, 'equity_before'] = float(cash)
                    selected.at[idx, 'equity_after'] = float(cash)
            entry_order = [int(x) for x in picks.get('_row_idx', pd.Series([], dtype=int)).tolist()]
        for idx in entry_order:
            if str(selected.at[idx, 'execution_status']) != 'pending':
                continue
            side_now = str(side_arr[idx]).strip().lower() if idx < len(side_arr) else ''; selected.at[idx, 'throttle_level_at_entry'] = throttle_level_name; is_limit_up = bool(idx < len(limit_up_arr) and np.isfinite(limit_up_arr[idx]) and (float(limit_up_arr[idx]) > 0.5)); is_limit_down = bool(idx < len(limit_down_arr) and np.isfinite(limit_down_arr[idx]) and (float(limit_down_arr[idx]) > 0.5))
            if is_limit_move_blocked_by_flags(
                side=side_now,
                is_limit_up_close=is_limit_up,
                is_limit_down_close=is_limit_down,
            ):
                selected.at[idx, 'execution_status'] = 'blocked_limit_move'; selected.at[idx, 'block_reason'] = 'blocked_limit_move'; selected.at[idx, 'equity_before'] = float(cash); selected.at[idx, 'equity_after'] = float(cash); selected.at[idx, 'position_scale'] = 0.0; selected.at[idx, 'position_notional'] = 0.0; selected.at[idx, 'entry_amount'] = 0.0; selected.at[idx, 'exit_amount'] = 0.0; selected.at[idx, 'max_loss_amount'] = 0.0; selected.at[idx, 'expected_loss_pct'] = 0.0; selected.at[idx, 'expected_loss_amount'] = 0.0; continue
            entry_equity = float(cash); avail_cash = float(cash - margin_used); dd_before = float(entry_equity / week_peak_equity - 1.0) if week_peak_equity > 0 else float('nan'); selected.at[idx, 'available_cash_before_entry'] = avail_cash; selected.at[idx, 'margin_used_before_entry'] = float(margin_used); selected.at[idx, 'open_notional_before_entry'] = float(open_notional); selected.at[idx, 'weekly_drawdown_pct_before_entry'] = dd_before; pred_mae = float(pred_mae_arr[idx]) if np.isfinite(pred_mae_arr[idx]) else float(cfg.min_pred_mae_atr_for_sizing); pred_mae = max(pred_mae, float(cfg.min_pred_mae_atr_for_sizing)); stop_risk_pct = float('nan'); ent_fill = float(entry_fill_arr[idx]) if np.isfinite(entry_fill_arr[idx]) else float('nan'); sl_price = float(stop_loss_arr[idx]) if np.isfinite(stop_loss_arr[idx]) else float('nan')
            if np.isfinite(ent_fill) and ent_fill > 0 and np.isfinite(sl_price) and (sl_price > 0):
                stop_risk_pct = abs(ent_fill - sl_price) / ent_fill
            else:
                ent_px = float(entry_price_arr[idx]) if np.isfinite(entry_price_arr[idx]) else float('nan')
                if np.isfinite(ent_px) and ent_px > 0 and np.isfinite(sl_price) and (sl_price > 0):
                    stop_risk_pct = abs(ent_px - sl_price) / ent_px
            # 按 signal_type 调整单笔仓位上限：effective_cap = max_position_scale * multiplier（已 clamp 到 [0,1]）
            eff_pos_cap = float(
                cfg.resolve_effective_position_scale_cap(
                    signal_type_arr[idx] if idx < len(signal_type_arr) else '',
                    normalize_portfolio_interval(selected.iloc[idx].get('interval', '')),
                )
            )
            if bool(cfg.use_position_sizing):
                if np.isfinite(stop_risk_pct) and stop_risk_pct > 0:
                    expected_loss_pct = float(stop_risk_pct)
                else:
                    expected_loss_pct = float(pred_mae) * float(cfg.risk_per_trade_pct)
                if expected_loss_pct > 0:
                    position_scale = float(min(eff_pos_cap, float(cfg.max_single_loss_pct) / expected_loss_pct))
                else:
                    position_scale = float(min(eff_pos_cap, 1.0))
            else:
                expected_loss_pct = float(cfg.max_single_loss_pct); position_scale = float(min(eff_pos_cap, 1.0))
            if week_dd_breached:
                position_scale = float(position_scale * float(cfg.weekly_dd_position_scale_after_breach))
            position_scale = max(position_scale, 0.0); desired_notional = max(0.0, float(entry_equity * position_scale)); sym_key = str(symbol_arr[idx]) if idx < len(symbol_arr) else ''; ex_key = str(exchange_arr[idx]) if idx < len(exchange_arr) else ''; signal_type_key = str(signal_type_arr[idx]).strip().lower() if idx < len(signal_type_arr) else ''; signal_type_cap = cfg.resolve_signal_type_max_concurrent(signal_type_key); contract_size, lot_size, margin_rate_cfg = _resolve_contract_spec(cfg, sym_key); margin_rate = max(float(margin_rate_cfg), 1e-09); notional = desired_notional; reason = ''; cluster_key = infer_symbol_cluster(sym_key); sym_dir_key = (sym_key, ex_key, side_now); sym_tuple = (str(sym_key).upper(), str(ex_key).upper()); sym_notional_now = float(runtime_state.symbol_notional.get(sym_tuple, 0.0)); sym_count_now = int(runtime_state.symbol_counts.get(sym_tuple, 0)); cluster_notional_now = float(runtime_state.cluster_notional.get(cluster_key, 0.0)); cluster_count_now = int(runtime_state.cluster_counts.get(cluster_key, 0)); total_count_now = int(runtime_state.total_positions); signal_type_count_now = int(signal_type_open_counts.get(signal_type_key, 0)); signal_type_notional_now = float(signal_type_open_notional.get(signal_type_key, 0.0)); signal_type_notional_pct = cfg.resolve_signal_type_max_notional_pct(signal_type_key); is_add_layer = False; current_pos_obj: PyramidPosition | None = None
            if use_pl_pyramid and pyramid_manager is not None:
                current_pos_obj = active_pyramid_by_sym_dir.get(sym_dir_key)
                if current_pos_obj is not None:
                    ent_for_add = float(ent_fill) if np.isfinite(ent_fill) else float(entry_price_arr[idx]) if np.isfinite(entry_price_arr[idx]) else 0.0; can_add = pyramid_manager.decide_add_layer(pos=current_pos_obj, new_interval=normalize_portfolio_interval(selected.iloc[idx].get('interval', '')), current_time=pd.Timestamp(ts), current_price=ent_for_add, min_profit_atr_to_add=float(pl_cfg.pyramid.min_profit_atr_to_add), htf_aligned=True); add_score = float(pyramid_add_score_arr[idx]) if idx < len(pyramid_add_score_arr) and np.isfinite(pyramid_add_score_arr[idx]) else float('nan'); add_score_threshold = float(getattr(pl_cfg.pyramid, 'min_model_add_score', 0.0)); selected.at[idx, 'pyramid_add_score_threshold'] = add_score_threshold; size_mult_used = 1.0
                    if bool(getattr(pl_cfg.pyramid, 'apply_model_add_score_gate', False)) and np.isfinite(add_score) and add_score < add_score_threshold:
                        can_add = False
                    if bool(getattr(pl_cfg.pyramid, 'apply_model_size_multiplier', False)):
                        raw_mult = float(pyramid_size_mult_arr[idx]) if idx < len(pyramid_size_mult_arr) and np.isfinite(pyramid_size_mult_arr[idx]) else 1.0; lo = float(getattr(pl_cfg.pyramid, 'min_model_size_multiplier', 0.0)); hi = float(getattr(pl_cfg.pyramid, 'max_model_size_multiplier', 1.0)); size_mult_used = float(np.clip(raw_mult, lo, hi))
                    selected.at[idx, 'pyramid_size_mult_used'] = float(size_mult_used)
                    if not can_add:
                        selected.at[idx, 'execution_status'] = 'blocked_pyramid_rule'; selected.at[idx, 'block_reason'] = 'blocked_pyramid_rule'; selected.at[idx, 'equity_before'] = float(cash); selected.at[idx, 'equity_after'] = float(cash); selected.at[idx, 'position_scale'] = 0.0; selected.at[idx, 'position_notional'] = 0.0; selected.at[idx, 'entry_amount'] = 0.0; selected.at[idx, 'entry_margin'] = 0.0; selected.at[idx, 'exit_amount'] = 0.0; selected.at[idx, 'max_loss_amount'] = 0.0; selected.at[idx, 'expected_loss_pct'] = expected_loss_pct; selected.at[idx, 'expected_loss_amount'] = 0.0; continue
                    if size_mult_used <= 0.0:
                        selected.at[idx, 'execution_status'] = 'blocked_pyramid_rule'; selected.at[idx, 'block_reason'] = 'blocked_pyramid_rule'; selected.at[idx, 'equity_before'] = float(cash); selected.at[idx, 'equity_after'] = float(cash); selected.at[idx, 'position_scale'] = 0.0; selected.at[idx, 'position_notional'] = 0.0; selected.at[idx, 'entry_amount'] = 0.0; selected.at[idx, 'entry_margin'] = 0.0; selected.at[idx, 'exit_amount'] = 0.0; selected.at[idx, 'max_loss_amount'] = 0.0; selected.at[idx, 'expected_loss_pct'] = expected_loss_pct; selected.at[idx, 'expected_loss_amount'] = 0.0; continue
                    notional = float(notional * size_mult_used)
                    is_add_layer = True
            if bool(cfg.use_portfolio_constraints):
                if bool(cfg.block_new_entries_on_monthly_dd_breach) and month_dd_breached:
                    notional = 0.0; reason = BR_BLOCKED_MONTHLY_DRAWDOWN
                elif bool(cfg.block_new_entries_on_weekly_dd_breach) and week_dd_breached:
                    notional = 0.0; reason = BR_BLOCKED_WEEKLY_DRAWDOWN
                elif total_count_now >= int(cfg.max_concurrent_positions_total):
                    notional = 0.0; reason = BR_BLOCKED_TOTAL_CONCURRENT
                elif signal_type_cap is not None and signal_type_count_now >= int(signal_type_cap):
                    notional = 0.0; reason = BR_BLOCKED_SIGNAL_TYPE_CONCURRENT
                elif not use_pl_pyramid and sym_key and (sym_count_now >= int(cfg.max_concurrent_positions_per_symbol)):
                    notional = 0.0; reason = 'blocked_symbol_concurrent'
                else:
                    cap_daily = max(
                        0.0,
                        float(day_start_equity * float(cfg.max_daily_new_notional_pct) - day_new_notional),
                    )
                    cap_lev = max(
                        0.0,
                        float(entry_equity * float(cfg.max_total_leverage) - float(runtime_state.total_open_notional)),
                    )
                    cap_cash = max(0.0, float(avail_cash / margin_rate))
                    cap_symbol = max(
                        0.0,
                        float(entry_equity * float(cfg.max_symbol_notional_pct) - sym_notional_now),
                    )
                    cap_signal_type = (
                        float("inf")
                        if signal_type_notional_pct is None
                        else max(
                            0.0,
                            float(entry_equity * float(signal_type_notional_pct) - signal_type_notional_now),
                        )
                    )
                    cap_cluster = float("inf")
                    cap_total_notional = float("inf")
                    if use_pl_runtime and pl_cfg is not None:
                        _caps = runtime_caps_cfg if runtime_caps_cfg is not None else pl_cfg.caps
                        cap_symbol = max(
                            0.0,
                            float(entry_equity * float(_caps.max_symbol_notional_pct) - sym_notional_now),
                        )
                        cap_cluster = max(
                            0.0,
                            float(entry_equity * float(_caps.max_cluster_notional_pct) - cluster_notional_now),
                        )
                        cap_total_notional = max(
                            0.0,
                            float(
                                entry_equity * float(_caps.max_total_notional_pct)
                                - float(runtime_state.total_open_notional)
                            ),
                        )
                    cap_week = float("inf")
                    if bool(cfg.enforce_weekly_dd_budget_on_entry) and float(cfg.weekly_max_drawdown_pct) > 0:
                        used_dd_amt = max(0.0, float(week_peak_equity - entry_equity))
                        remain_dd_amt = max(
                            0.0,
                            float(week_peak_equity * float(cfg.weekly_max_drawdown_pct) - used_dd_amt),
                        )
                        if float(cfg.max_single_loss_pct) > 0:
                            cap_week = float(remain_dd_amt / float(cfg.max_single_loss_pct))
                        else:
                            cap_week = 0.0
                    notional = float(
                        min(
                            notional,
                            cap_daily,
                            cap_lev,
                            cap_cash,
                            cap_week,
                            cap_symbol,
                            cap_signal_type,
                            cap_cluster,
                            cap_total_notional,
                        )
                    )
                    if notional <= 0:
                        if cap_symbol <= 0:
                            reason = BR_BLOCKED_SYMBOL_CAP
                        elif cap_cluster <= 0:
                            reason = BR_BLOCKED_CLUSTER_CAP
                        elif cap_total_notional <= 0:
                            reason = BR_BLOCKED_TOTAL_NOTIONAL
                        elif cap_week <= 0:
                            reason = BR_BLOCKED_WEEKLY_BUDGET
                        elif cap_daily <= 0:
                            reason = BR_BLOCKED_DAILY_POSITION
                        elif cap_cash <= 0:
                            reason = BR_BLOCKED_MARGIN_CASH
                        elif cap_lev <= 0:
                            reason = BR_BLOCKED_LEVERAGE
                        else:
                            reason = BR_BLOCKED_PORTFOLIO_CONSTRAINT
                            logger.error(
                                "blocked_portfolio_constraint hit unexpectedly at idx=%d: "
                                "notional=%.6f cap_daily=%.6f cap_lev=%.6f cap_cash=%.6f "
                                "cap_week=%.6f cap_symbol=%.6f cap_cluster=%.6f cap_total_notional=%.6f",
                                int(idx),
                                float(notional),
                                float(cap_daily),
                                float(cap_lev),
                                float(cap_cash),
                                float(cap_week),
                                float(cap_symbol),
                                float(cap_cluster),
                                float(cap_total_notional),
                            )
            entry_price = float(entry_price_arr[idx]) if np.isfinite(entry_price_arr[idx]) else float('nan')
            if np.isfinite(entry_price) and entry_price > 0 and (contract_size > 0):
                raw_qty = float(notional / (entry_price * contract_size))
                if lot_size > 0:
                    position_qty = float(np.floor(raw_qty / lot_size) * lot_size)
                else:
                    position_qty = float(np.floor(raw_qty))
                if not np.isfinite(position_qty) or position_qty <= 0:
                    notional = 0.0
                else:
                    notional = float(position_qty * entry_price * contract_size)
            else:
                position_qty = float('nan')
            if notional <= 0:
                selected.at[idx, 'execution_status'] = reason or 'blocked_zero_notional'; selected.at[idx, 'block_reason'] = reason or BR_ZERO_NOTIONAL; selected.at[idx, 'equity_before'] = entry_equity; selected.at[idx, 'equity_after'] = entry_equity; selected.at[idx, 'position_scale'] = 0.0; selected.at[idx, 'position_notional'] = 0.0; selected.at[idx, 'entry_amount'] = 0.0; selected.at[idx, 'entry_margin'] = 0.0; selected.at[idx, 'exit_amount'] = 0.0; selected.at[idx, 'max_loss_amount'] = 0.0; selected.at[idx, 'expected_loss_pct'] = expected_loss_pct; selected.at[idx, 'expected_loss_amount'] = 0.0; selected.at[idx, 'position_qty'] = 0.0; selected.at[idx, 'contract_size'] = contract_size; selected.at[idx, 'lot_size'] = lot_size; selected.at[idx, 'margin_rate_used'] = margin_rate; continue
            margin = float(notional * margin_rate); margin_used = float(margin_used + margin); open_notional = float(open_notional + notional); day_new_notional = float(day_new_notional + notional)
            if sym_key:
                symbol_open_notional[sym_key] = float(symbol_open_notional.get(sym_key, 0.0) + notional); symbol_open_count[sym_key] = int(symbol_open_count.get(sym_key, 0) + 1)
            cluster_open_notional[cluster_key] = float(cluster_open_notional.get(cluster_key, 0.0) + notional); cluster_open_count[cluster_key] = int(cluster_open_count.get(cluster_key, 0) + 1); max_loss_amount = float(entry_equity * float(cfg.max_single_loss_pct)); expected_loss_amount = float(notional * float(expected_loss_pct)); selected.at[idx, 'execution_status'] = 'opened'; selected.at[idx, 'equity_before'] = entry_equity; selected.at[idx, 'position_scale'] = float(notional / entry_equity) if entry_equity > 0 else 0.0; selected.at[idx, 'position_notional'] = notional; selected.at[idx, 'entry_margin'] = margin; selected.at[idx, 'open_notional_at_entry'] = float(open_notional); selected.at[idx, 'max_loss_amount'] = max_loss_amount; selected.at[idx, 'position_qty'] = position_qty; selected.at[idx, 'contract_size'] = contract_size; selected.at[idx, 'lot_size'] = lot_size; selected.at[idx, 'margin_rate_used'] = margin_rate; selected.at[idx, 'expected_loss_pct'] = expected_loss_pct; selected.at[idx, 'expected_loss_amount'] = expected_loss_amount
            if use_pl_pyramid and pyramid_manager is not None:
                if is_add_layer and current_pos_obj is not None:
                    new_layer = pyramid_manager.add_layer(pos=current_pos_obj, interval=normalize_portfolio_interval(selected.iloc[idx].get('interval', '')), entry_time=pd.Timestamp(ts), entry_price=float(entry_price) if np.isfinite(entry_price) else float(ent_fill) if np.isfinite(ent_fill) else 0.0, notional=float(notional), atr_pct_at_entry=float(atr_pct_arr[idx]) if idx < len(atr_pct_arr) and np.isfinite(atr_pct_arr[idx]) else float('nan'), signal_score=float(pd.to_numeric(pd.Series([selected.iloc[idx].get('ranker_score', np.nan)]), errors='coerce').fillna(0.0).iloc[0]), trade_filter_prob=float(pd.to_numeric(pd.Series([selected.iloc[idx].get('trade_filter_prob', np.nan)]), errors='coerce').fillna(0.0).iloc[0]), trade_filter_prob_pctl=float(pd.to_numeric(pd.Series([selected.iloc[idx].get('trade_filter_prob_pctl', np.nan)]), errors='coerce').fillna(0.0).iloc[0]), trailing_cfg=pl_cfg.trailing, size_multiplier=1.0); pos_obj = current_pos_obj; layer_id = int(new_layer.layer_id)
                else:
                    pos_obj = pyramid_manager.open_first_layer(symbol=sym_key, exchange=ex_key, direction=side_now, interval=normalize_portfolio_interval(selected.iloc[idx].get('interval', '')), entry_time=pd.Timestamp(ts), entry_price=float(entry_price) if np.isfinite(entry_price) else float(ent_fill) if np.isfinite(ent_fill) else 0.0, notional=float(notional), atr_pct_at_entry=float(atr_pct_arr[idx]) if idx < len(atr_pct_arr) and np.isfinite(atr_pct_arr[idx]) else float('nan'), signal_score=float(pd.to_numeric(pd.Series([selected.iloc[idx].get('ranker_score', np.nan)]), errors='coerce').fillna(0.0).iloc[0]), trade_filter_prob=float(pd.to_numeric(pd.Series([selected.iloc[idx].get('trade_filter_prob', np.nan)]), errors='coerce').fillna(0.0).iloc[0]), trade_filter_prob_pctl=float(pd.to_numeric(pd.Series([selected.iloc[idx].get('trade_filter_prob_pctl', np.nan)]), errors='coerce').fillna(0.0).iloc[0]), trailing_cfg=pl_cfg.trailing); active_pyramid_by_sym_dir[sym_dir_key] = pos_obj; layer_id = 0
                selected.at[idx, 'pos_id'] = str(pos_obj.pos_id); selected.at[idx, 'layer_id'] = int(layer_id); selected.at[idx, 'layer_interval'] = normalize_portfolio_interval(selected.iloc[idx].get('interval', '')); idx_layer_ref[idx] = (pos_obj, int(layer_id))
            else:
                selected.at[idx, 'pos_id'] = f'{sym_key}_{ex_key}_{side_now}_{pd.Timestamp(ts).isoformat()}'; selected.at[idx, 'layer_id'] = 0; selected.at[idx, 'layer_interval'] = normalize_portfolio_interval(selected.iloc[idx].get('interval', ''))
            pos_id_now = str(selected.at[idx, 'pos_id']); open_positions[idx] = {'notional': notional, 'margin': margin, 'gross_ret_pct': float(gross_pct_arr[idx]), 'net_ret_pct': float(net_pct_arr[idx]), 'entry_equity': entry_equity, 'symbol': sym_key, 'exchange': ex_key, 'side': side_now, 'signal_type': signal_type_key, 'cluster': cluster_key, 'pos_id': pos_id_now, 'layer_id': int(pd.to_numeric(pd.Series([selected.at[idx, 'layer_id']]), errors='coerce').fillna(0).iloc[0]), 'entry_ts': pd.Timestamp(ts)}
            if signal_type_key:
                signal_type_open_counts[signal_type_key] = int(signal_type_open_counts.get(signal_type_key, 0) + 1)
                signal_type_open_notional[signal_type_key] = float(signal_type_open_notional.get(signal_type_key, 0.0) + float(notional))
            runtime_state.add_position({'pos_id': pos_id_now, 'symbol': sym_key, 'exchange': ex_key, 'cluster': cluster_key, 'direction': side_now, 'notional': float(notional)})
    for idx, pos in list(open_positions.items()):
        margin_used = max(0.0, float(margin_used - float(pos['margin']))); open_notional = max(0.0, float(open_notional - float(pos['notional']))); pos_sym = str(pos.get('symbol', '')); pos_signal_type = str(pos.get('signal_type', '')).strip().lower(); runtime_state.remove_position(str(pos.get('pos_id', '')))
        if pos_signal_type:
            signal_type_open_counts[pos_signal_type] = max(0, int(signal_type_open_counts.get(pos_signal_type, 0) - 1))
            if int(signal_type_open_counts.get(pos_signal_type, 0)) <= 0:
                signal_type_open_counts.pop(pos_signal_type, None)
            signal_type_open_notional[pos_signal_type] = max(0.0, float(signal_type_open_notional.get(pos_signal_type, 0.0) - float(pos['notional'])))
            if float(signal_type_open_notional.get(pos_signal_type, 0.0)) <= 1e-9:
                signal_type_open_notional.pop(pos_signal_type, None)
        if pos_sym:
            symbol_open_notional[pos_sym] = max(0.0, float(symbol_open_notional.get(pos_sym, 0.0) - float(pos['notional']))); symbol_open_count[pos_sym] = max(0, int(symbol_open_count.get(pos_sym, 0) - 1))
        pos_cluster = str(pos.get('cluster', 'other')); cluster_open_notional[pos_cluster] = max(0.0, float(cluster_open_notional.get(pos_cluster, 0.0) - float(pos['notional']))); cluster_open_count[pos_cluster] = max(0, int(cluster_open_count.get(pos_cluster, 0) - 1))
        if use_pl_pyramid and idx in idx_layer_ref:
            pos_ref, layer_id_ref = idx_layer_ref.pop(idx)
            for layer in pos_ref.layers:
                if int(layer.layer_id) == int(layer_id_ref):
                    layer.exited = True; break
            sym_dir = (pos_ref.symbol, pos_ref.exchange, pos_ref.direction)
            if len(pos_ref.active_layers) == 0:
                active_pyramid_by_sym_dir.pop(sym_dir, None)
        gross_pnl = float(pos['notional']) * float(pos['gross_ret_pct']); fallback_exit_ts = pd.to_datetime(selected.at[idx, 'exit_datetime'], errors='coerce')
        if pd.isna(fallback_exit_ts):
            fallback_exit_ts = pd.Timestamp(pos.get('entry_ts', pd.Timestamp.now()))
        roll_cost = _calc_roll_cost(symbol=pos_sym, notional=float(pos['notional']), entry_ts=pd.Timestamp(pos.get('entry_ts', fallback_exit_ts)), exit_ts=pd.Timestamp(fallback_exit_ts), cfg=cfg); net_pnl = float(pos['notional']) * float(pos['net_ret_pct']) - float(roll_cost); cash = float(cash + net_pnl); selected.at[idx, 'equity_before'] = float(pos['entry_equity']); selected.at[idx, 'equity_after'] = float(cash); selected.at[idx, 'gross_pnl'] = gross_pnl; selected.at[idx, 'net_pnl'] = net_pnl; selected.at[idx, 'roll_cost'] = float(roll_cost); selected.at[idx, 'pnl_amount'] = net_pnl; selected.at[idx, 'entry_amount'] = float(pos['notional']); selected.at[idx, 'exit_amount'] = float(pos['notional'] + net_pnl); selected.at[idx, 'position_notional_after_trade'] = float(open_notional); selected.at[idx, 'execution_status'] = 'executed'
    open_positions.clear()
    use_bar_volume_cap = bool(
        use_intrabar
        and bool(getattr(cfg, 'enforce_intrabar_bar_volume_cap', False))
        and float(getattr(cfg, 'intrabar_max_bar_volume_participation_pct', 0.0)) > 0.0
    )
    if use_bar_volume_cap:
        volume_cap_pct = float(getattr(cfg, 'intrabar_max_bar_volume_participation_pct', 0.01))
        volume_col = str(getattr(cfg, 'intrabar_volume_column', 'volume')).strip() or 'volume'
        adjusted_rows = 0
        exec_idx = selected.index[selected['execution_status'].astype(str) == 'executed'].tolist()
        for idx in exec_idx:
            qty = float(pd.to_numeric(pd.Series([selected.at[idx, 'position_qty']]), errors='coerce').fillna(0.0).iloc[0])
            if not np.isfinite(qty) or qty <= 0.0:
                continue
            entry_fill_dt = pd.to_datetime(selected.at[idx, 'entry_fill_datetime'], errors='coerce')
            if pd.isna(entry_fill_dt):
                entry_fill_dt = pd.to_datetime(selected.at[idx, 'entry_datetime'], errors='coerce')
            final_exit_dt = pd.to_datetime(selected.at[idx, 'final_exit_datetime'], errors='coerce')
            planned_exit_dt = pd.to_datetime(selected.at[idx, 'planned_exit_datetime'], errors='coerce')
            load_end_ts = final_exit_dt
            if pd.notna(planned_exit_dt) and (pd.isna(load_end_ts) or planned_exit_dt > load_end_ts):
                load_end_ts = planned_exit_dt
            bars, _used_interval = _load_intrabar_bars(int(idx), load_end_ts=load_end_ts)
            if bars.empty or 'datetime' not in bars.columns:
                continue
            bars = bars.copy()
            bars['datetime'] = pd.to_datetime(bars['datetime'], errors='coerce')
            bars = bars.dropna(subset=['datetime']).sort_values('datetime').reset_index(drop=True)
            if bars.empty:
                continue

            entry_cap_dt = _first_bar_with_capacity(
                bars,
                start_ts=pd.Timestamp(entry_fill_dt),
                qty=float(qty),
                max_participation_pct=volume_cap_pct,
                volume_column=volume_col,
            )
            if pd.isna(entry_cap_dt):
                continue
            exit_start_dt = final_exit_dt if pd.notna(final_exit_dt) else entry_cap_dt
            if exit_start_dt < entry_cap_dt:
                exit_start_dt = entry_cap_dt
            exit_cap_dt = _first_bar_with_capacity(
                bars,
                start_ts=pd.Timestamp(exit_start_dt),
                qty=float(qty),
                max_participation_pct=volume_cap_pct,
                volume_column=volume_col,
            )
            if pd.isna(exit_cap_dt):
                exit_cap_dt = exit_start_dt
            if exit_cap_dt < entry_cap_dt:
                exit_cap_dt = entry_cap_dt

            entry_old = pd.to_datetime(selected.at[idx, 'entry_fill_datetime'], errors='coerce')
            exit_old = pd.to_datetime(selected.at[idx, 'final_exit_datetime'], errors='coerce')
            changed = bool(pd.isna(entry_old) or pd.Timestamp(entry_cap_dt) != pd.Timestamp(entry_old))
            changed = changed or bool(pd.isna(exit_old) or pd.Timestamp(exit_cap_dt) != pd.Timestamp(exit_old))
            if not changed:
                continue
            adjusted_rows += 1
            selected.at[idx, 'entry_fill_datetime'] = pd.Timestamp(entry_cap_dt)
            selected.at[idx, 'entry_datetime'] = pd.Timestamp(entry_cap_dt)
            selected.at[idx, 'final_exit_datetime'] = pd.Timestamp(exit_cap_dt)
            selected.at[idx, 'exit_datetime'] = pd.Timestamp(exit_cap_dt)
            if int(pd.to_numeric(pd.Series([selected.at[idx, 'stop_triggered']]), errors='coerce').fillna(0).iloc[0]) == 1:
                selected.at[idx, 'stop_hit_datetime'] = pd.Timestamp(exit_cap_dt)
        if adjusted_rows > 0:
            logger.info(
                'intrabar bar-volume cap adjusted %d executed rows (max_participation=%.4f, volume_col=%s)',
                adjusted_rows,
                float(volume_cap_pct),
                volume_col,
            )

    executed = selected.loc[selected['execution_status'] == 'executed'].copy()
    if executed.empty:
        summary = pd.DataFrame([{'oot_rows': oot_rows, 'executed_rows': executed_rows, 'trade_count': 0, 'selected_rows': selected_rows, 'blocked_rows': int((selected['execution_status'] != 'executed').sum()), 'blocked_trade_filter_rows': int((selected['execution_status'] == BR_BLOCKED_TRADE_FILTER).sum()), 'blocked_regime_gate_rows': int((selected['execution_status'] == BR_BLOCKED_REGIME_GATE).sum()), 'blocked_mfe_mae_rows': int((selected['execution_status'] == BR_BLOCKED_MFE_MAE_GATE).sum()), 'blocked_final_decision_rows': int((selected['execution_status'] == BR_BLOCKED_FINAL_DECISION_GATE).sum()), 'blocked_margin_cash_rows': int((selected['execution_status'] == 'blocked_margin_cash').sum()), 'blocked_leverage_rows': int((selected['execution_status'] == 'blocked_leverage').sum()), 'blocked_limit_move_rows': int((selected['execution_status'] == 'blocked_limit_move').sum()), 'blocked_daily_position_rows': int((selected['execution_status'] == 'blocked_daily_position').sum()), 'blocked_weekly_drawdown_rows': int((selected['execution_status'] == 'blocked_weekly_drawdown').sum()), 'blocked_weekly_budget_rows': int((selected['execution_status'] == 'blocked_weekly_budget').sum()), 'blocked_monthly_drawdown_rows': int((selected['execution_status'] == 'blocked_monthly_drawdown').sum()), 'blocked_symbol_cap_rows': int(selected['execution_status'].isin(['blocked_symbol_cap', 'blocked_cluster_cap']).sum()), 'blocked_total_notional_rows': int((selected['execution_status'] == BR_BLOCKED_TOTAL_NOTIONAL).sum()), 'blocked_symbol_concurrent_rows': int((selected['execution_status'] == 'blocked_symbol_concurrent').sum()), 'blocked_total_concurrent_rows': int((selected['execution_status'] == 'blocked_total_concurrent').sum()), 'blocked_htf_rows': int((selected['execution_status'] == 'blocked_htf_gate').sum()), 'blocked_ranker_rows': int((selected['execution_status'] == 'blocked_ranker').sum()), 'blocked_throttle_rows': int((selected['execution_status'] == 'blocked_throttle_halt').sum()), 'blocked_pyramid_rows': int((selected['execution_status'] == 'blocked_pyramid_rule').sum()), 'exit_datetime_fixup_rows': exit_fixup_rows, 'stop_loss_exit_rows': 0, 'trailing_stop_exit_rows': 0, 'horizon_exit_rows': 0, 'monthly_obs': 0, 'gross_pnl': 0.0, 'net_pnl': 0.0, 'roll_cost_total': 0.0, 'total_return_pct': 0.0, 'avg_monthly_return_pct': float('nan'), 'std_monthly_return_pct': float('nan'), 'monthly_excess_return_pct': float('nan'), 'std_monthly_excess_return_pct': float('nan'), 'monthly_sharpe': float('nan'), 'max_drawdown_pct': float('nan'), 'annualized_return_pct': float('nan'), 'calmar_like': float('nan')}])
        trade_df = selected.reindex(columns=trade_cols).copy()
        if extra_outputs is not None:
            extra_outputs['throttle_log'] = pd.DataFrame(throttle_log_rows, columns=throttle_cols); extra_outputs['position_lifetime'] = _build_position_lifetime_table(trade_df)
        _log_block_reason_distribution(trade_df, path_marker='early_return'); return (pd.DataFrame(columns=monthly_cols), summary[summary_cols], trade_df)
    executed['month'] = pd.to_datetime(executed['exit_datetime'], errors='coerce').dt.to_period('M').dt.to_timestamp(); monthly = executed.groupby('month', as_index=False).agg(trade_count=('net_pnl', 'size'), win_count=('net_pnl', lambda s: int((pd.Series(s) > 0.0).sum())), loss_count=('net_pnl', lambda s: int((pd.Series(s) < 0.0).sum())), win_rate=('net_pnl', lambda s: float((pd.Series(s) > 0.0).mean())), gross_pnl=('gross_pnl', 'sum'), net_pnl=('net_pnl', 'sum')).sort_values('month').reset_index(drop=True); start_cap = float(cfg.initial_capital); month_start_arr: list[float] = []; month_end_arr: list[float] = []; cur_eq = start_cap
    for _, r in monthly.iterrows():
        month_start_arr.append(float(cur_eq)); net_v = float(pd.to_numeric(pd.Series([r.get('net_pnl', 0.0)]), errors='coerce').fillna(0.0).iloc[0]); cur_eq = float(cur_eq + net_v); month_end_arr.append(float(cur_eq))
    monthly['month_start_equity'] = month_start_arr; monthly['month_end_equity'] = month_end_arr; monthly['monthly_return_pct'] = pd.to_numeric(monthly['month_end_equity'], errors='coerce') / pd.to_numeric(monthly['month_start_equity'], errors='coerce') - 1.0; benchmark_m = (1.0 + float(cfg.benchmark_annual_return)) ** (1.0 / float(cfg.annualization_factor)) - 1.0; monthly['monthly_excess_return_pct'] = pd.to_numeric(monthly['monthly_return_pct'], errors='coerce') - float(benchmark_m); start_cap = float(cfg.initial_capital); monthly['cum_return_pct'] = pd.to_numeric(monthly['month_end_equity'], errors='coerce') / start_cap - 1.0; gross = float(pd.to_numeric(monthly['gross_pnl'], errors='coerce').fillna(0.0).sum()); net = float(pd.to_numeric(monthly['net_pnl'], errors='coerce').fillna(0.0).sum()); roll_total = float(pd.to_numeric(executed.get('roll_cost', 0.0), errors='coerce').fillna(0.0).sum()); exit_reason_series = executed.get('exit_reason', pd.Series([''] * len(executed), index=executed.index)).astype(str); stop_loss_exit_rows = int(exit_reason_series.isin(['stop_loss', 'hard_stop']).sum()); trailing_stop_exit_rows = int((exit_reason_series == 'trailing_stop').sum()); horizon_exit_rows = int((exit_reason_series == 'horizon_exit').sum()); mean_m = float(pd.to_numeric(monthly['monthly_return_pct'], errors='coerce').mean()) if len(monthly) else float('nan'); std_m = float(pd.to_numeric(monthly['monthly_return_pct'], errors='coerce').std(ddof=1)) if len(monthly) >= 2 else float('nan'); mean_ex = float(pd.to_numeric(monthly['monthly_excess_return_pct'], errors='coerce').mean()) if len(monthly) else float('nan'); std_ex = float(pd.to_numeric(monthly['monthly_excess_return_pct'], errors='coerce').std(ddof=1)) if len(monthly) >= 2 else float('nan')
    if np.isfinite(mean_ex) and np.isfinite(std_ex) and (std_ex > 0):
        sharpe = float(mean_ex / std_ex * np.sqrt(float(cfg.annualization_factor)))
    else:
        sharpe = float('nan')
    eq = pd.to_numeric(monthly['month_end_equity'], errors='coerce').ffill().fillna(start_cap); trade_eq = pd.to_numeric(executed.sort_values('exit_datetime')['equity_after'], errors='coerce').ffill().fillna(start_cap); dd = trade_eq / trade_eq.cummax() - 1.0; max_dd = float(dd.min()) if not dd.empty else float('nan'); ann = float((1.0 + mean_m) ** float(cfg.annualization_factor) - 1.0) if np.isfinite(mean_m) else float('nan')
    if np.isfinite(ann) and np.isfinite(max_dd) and (max_dd < 0):
        calmar_like = float(ann / abs(max_dd))
    else:
        calmar_like = float('nan')
    total_return_pct = float(eq.iloc[-1] / start_cap - 1.0) if not eq.empty else float('nan')
    summary = pd.DataFrame([{'oot_rows': oot_rows, 'executed_rows': executed_rows, 'trade_count': int(len(executed)), 'selected_rows': selected_rows, 'blocked_rows': int((selected['execution_status'] != 'executed').sum()), 'blocked_trade_filter_rows': int((selected['execution_status'] == BR_BLOCKED_TRADE_FILTER).sum()), 'blocked_regime_gate_rows': int((selected['execution_status'] == BR_BLOCKED_REGIME_GATE).sum()), 'blocked_mfe_mae_rows': int((selected['execution_status'] == BR_BLOCKED_MFE_MAE_GATE).sum()), 'blocked_final_decision_rows': int((selected['execution_status'] == BR_BLOCKED_FINAL_DECISION_GATE).sum()), 'blocked_margin_cash_rows': int((selected['execution_status'] == 'blocked_margin_cash').sum()), 'blocked_leverage_rows': int((selected['execution_status'] == 'blocked_leverage').sum()), 'blocked_limit_move_rows': int((selected['execution_status'] == 'blocked_limit_move').sum()), 'blocked_daily_position_rows': int((selected['execution_status'] == 'blocked_daily_position').sum()), 'blocked_weekly_drawdown_rows': int((selected['execution_status'] == 'blocked_weekly_drawdown').sum()), 'blocked_weekly_budget_rows': int((selected['execution_status'] == 'blocked_weekly_budget').sum()), 'blocked_monthly_drawdown_rows': int((selected['execution_status'] == 'blocked_monthly_drawdown').sum()), 'blocked_symbol_cap_rows': int(selected['execution_status'].isin(['blocked_symbol_cap', 'blocked_cluster_cap']).sum()), 'blocked_total_notional_rows': int((selected['execution_status'] == BR_BLOCKED_TOTAL_NOTIONAL).sum()), 'blocked_symbol_concurrent_rows': int((selected['execution_status'] == 'blocked_symbol_concurrent').sum()), 'blocked_total_concurrent_rows': int((selected['execution_status'] == 'blocked_total_concurrent').sum()), 'blocked_htf_rows': int((selected['execution_status'] == 'blocked_htf_gate').sum()), 'blocked_ranker_rows': int((selected['execution_status'] == 'blocked_ranker').sum()), 'blocked_throttle_rows': int((selected['execution_status'] == 'blocked_throttle_halt').sum()), 'blocked_pyramid_rows': int((selected['execution_status'] == 'blocked_pyramid_rule').sum()), 'exit_datetime_fixup_rows': exit_fixup_rows, 'stop_loss_exit_rows': stop_loss_exit_rows, 'trailing_stop_exit_rows': trailing_stop_exit_rows, 'horizon_exit_rows': horizon_exit_rows, 'monthly_obs': int(len(monthly)), 'gross_pnl': gross, 'net_pnl': net, 'roll_cost_total': roll_total, 'total_return_pct': total_return_pct, 'avg_monthly_return_pct': mean_m, 'std_monthly_return_pct': std_m, 'monthly_excess_return_pct': mean_ex, 'std_monthly_excess_return_pct': std_ex, 'monthly_sharpe': sharpe, 'max_drawdown_pct': max_dd, 'annualized_return_pct': ann, 'calmar_like': calmar_like}])
    trade_df = selected.reindex(columns=trade_cols).copy()
    if extra_outputs is not None:
        extra_outputs['throttle_log'] = pd.DataFrame(throttle_log_rows, columns=throttle_cols); extra_outputs['position_lifetime'] = _build_position_lifetime_table(trade_df)
    _log_block_reason_distribution(trade_df, path_marker='main'); return (monthly[monthly_cols], summary[summary_cols], trade_df)


__all__ = ["_evaluate_oot_real_execution", "_build_position_lifetime_table"]
