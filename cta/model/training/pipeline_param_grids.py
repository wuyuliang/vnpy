"""Parameter-grid and model tuning helpers for pipeline_orchestrator."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import (
    LABEL_MAE_PENALTY,
    LABEL_THRESHOLD,
    MFE_MAE_KIND_SKIPPED_NO_EXEC,
    MfeMaeModel,
    OotEvaluationConfig,
    RegimeClassifierModel,
    TradeFilterModel,
    evaluate_mfe_mae_model,
    evaluate_regime_model,
    evaluate_trade_filter_model,
    np,
    pd,
)

def _safe_float(value: Any) -> float:
    try:
        out = float(value)
    except (TypeError, ValueError):
        return float('nan')
    if not np.isfinite(out):
        return float('nan')
    return out

def _auc_gap(train_auc: float, valid_auc: float) -> float:
    t = _safe_float(train_auc)
    v = _safe_float(valid_auc)
    if np.isnan(t) or np.isnan(v):
        return float('inf')
    return abs(t - v)

def _select_best_param_trial(trials: Sequence[dict[str, Any]], *, max_auc_gap: float) -> dict[str, Any]:
    """Pick best trial with strict no-leak policy.

    选参只用 train/valid：
    1. 先过滤 ``abs(train_auc-valid_auc) <= max_auc_gap``；
    2. 在可行集合中按 valid_auc 最大选；
    3. 若无可行集合，回退到 gap 最小，再看 valid_auc。

    注意：``oot_auc`` 仅用于最终报告，不参与排序。
    """
    if not trials:
        raise ValueError('no parameter-search trial provided')
    rows: list[dict[str, Any]] = []
    for idx, row in enumerate(trials):
        train_auc = _safe_float(row.get('train_auc'))
        valid_auc = _safe_float(row.get('valid_auc'))
        gap = _auc_gap(train_auc, valid_auc)
        out = dict(row)
        out['train_auc'] = train_auc
        out['valid_auc'] = valid_auc
        out['auc_gap'] = gap
        out['_idx'] = idx
        rows.append(out)
    feasible = [r for r in rows if np.isfinite(r['valid_auc']) and np.isfinite(r['auc_gap']) and (float(r['auc_gap']) <= float(max_auc_gap) + 1e-12)]
    if feasible:
        feasible.sort(key=lambda r: (-float(r['valid_auc']), float(r['auc_gap']), int(r['_idx'])))
        return feasible[0]
    finite = [r for r in rows if np.isfinite(r['valid_auc'])]
    if finite:
        finite.sort(key=lambda r: (float(r['auc_gap']), -float(r['valid_auc']), int(r['_idx'])))
        return finite[0]
    rows.sort(key=lambda r: int(r['_idx']))
    return rows[0]

def _default_label_stop_loss_pct() -> float:
    import inspect
    from cta.strategy.baseline_skill_suite import generate_candidate_opportunities
    sig = inspect.signature(generate_candidate_opportunities)
    param = sig.parameters.get('label_stop_loss_pct')
    if param is None or param.default is inspect.Parameter.empty:
        raise RuntimeError('generate_candidate_opportunities missing label_stop_loss_pct default')
    return float(param.default)

def _validate_stop_loss_pct_consistency(oot_stop_loss_pct: float | None=None, label_stop_loss_pct: float | None=None, *, tolerance: float=0.005) -> None:
    """Runtime guard: OOT intrabar stop loss should stay aligned with training labels."""
    if oot_stop_loss_pct is None:
        oot_stop_loss_pct = float(OotEvaluationConfig().intrabar_stop_loss_pct)
    if label_stop_loss_pct is None:
        label_stop_loss_pct = _default_label_stop_loss_pct()
    diff = abs(float(oot_stop_loss_pct) - float(label_stop_loss_pct))
    if diff > float(tolerance):
        raise RuntimeError('intrabar_stop_loss_pct ({:.6f}) != label_stop_loss_pct ({:.6f}); diff={:.6f} > tolerance={:.6f}. Please sync OOT vs training stop-loss config.'.format(float(oot_stop_loss_pct), float(label_stop_loss_pct), diff, float(tolerance)))

def _trade_filter_param_grid() -> list[dict[str, Any]]:
    return [{'learning_rate': 0.03, 'max_depth': 3, 'max_iter': 180, 'min_samples_leaf': 20, 'max_leaf_nodes': 31, 'l2_regularization': 1.0}, {'learning_rate': 0.02, 'max_depth': 2, 'max_iter': 240, 'min_samples_leaf': 30, 'max_leaf_nodes': 31, 'l2_regularization': 2.0}, {'learning_rate': 0.05, 'max_depth': 2, 'max_iter': 140, 'min_samples_leaf': 40, 'max_leaf_nodes': 15, 'l2_regularization': 3.0}, {'learning_rate': 0.03, 'max_depth': 4, 'max_iter': 160, 'min_samples_leaf': 25, 'max_leaf_nodes': 31, 'l2_regularization': 1.5}]

def _regime_classifier_param_grid() -> list[dict[str, Any]]:
    return [{'n_estimators': 200, 'max_depth': 5, 'min_samples_leaf': 20, 'max_features': 'sqrt', 'class_weight': 'balanced_subsample'}, {'n_estimators': 180, 'max_depth': 4, 'min_samples_leaf': 24, 'max_features': 'sqrt', 'class_weight': 'balanced_subsample'}, {'n_estimators': 320, 'max_depth': 6, 'min_samples_leaf': 30, 'max_features': 0.6, 'class_weight': 'balanced_subsample'}, {'n_estimators': 280, 'max_depth': 5, 'min_samples_leaf': 40, 'max_features': 0.5, 'class_weight': 'balanced_subsample'}]

def _mfe_mae_param_grid() -> list[dict[str, Any]]:
    return [{'n_estimators': 200, 'max_depth': 5, 'min_samples_leaf': 20, 'min_samples_split': 80, 'max_features': 0.35}, {'n_estimators': 180, 'max_depth': 4, 'min_samples_leaf': 40, 'min_samples_split': 120, 'max_features': 0.3}, {'n_estimators': 260, 'max_depth': 5, 'min_samples_leaf': 28, 'min_samples_split': 90, 'max_features': 0.4}, {'n_estimators': 300, 'max_depth': 7, 'min_samples_leaf': 24, 'min_samples_split': 70, 'max_features': 0.5}]

def _train_mfe_mae_or_skip(train_df: pd.DataFrame, feature_columns: list[str], *, random_state: int=2026, mfe_column: str='future_mfe_atr', mae_column: str='future_mae_atr', model_params: dict[str, Any] | None=None, sample_weight: pd.Series | np.ndarray | None=None) -> tuple[MfeMaeModel | None, str]:
    """B1 fix: 仅当 train 集存在 is_executed=1 样本时才真正训练 MFE/MAE 模型。

    train_exec 为空时返回 ``(None, MFE_MAE_KIND_SKIPPED_NO_EXEC)``，由调用方写
    NaN 指标 + NaN 预测，避免用全 0 标签训出"恒预测 0"的回归器。
    """
    if 'is_executed' in train_df.columns:
        exec_mask = pd.to_numeric(train_df['is_executed'], errors='coerce').fillna(0).astype(int) == 1
    else:
        exec_mask = pd.Series(True, index=train_df.index)
    train_exec = train_df.loc[exec_mask]
    if train_exec.empty:
        return (None, MFE_MAE_KIND_SKIPPED_NO_EXEC)
    if sample_weight is None:
        sw_exec = None
    elif isinstance(sample_weight, pd.Series):
        sw_exec = sample_weight.reindex(train_df.index).loc[train_exec.index].to_numpy(dtype=float)
    else:
        sw_all = np.asarray(sample_weight, dtype=float).reshape(-1)
        if sw_all.shape[0] != len(train_df):
            raise ValueError(f'sample_weight length mismatch: {sw_all.shape[0]} != {len(train_df)}')
        sw_exec = sw_all[exec_mask.to_numpy()]
    model = MfeMaeModel(random_state=random_state, model_params=model_params).fit(train_exec, feature_columns=feature_columns, mfe_column=mfe_column, mae_column=mae_column, sample_weight=sw_exec)
    return (model, model.model_kind)

def _tune_trade_filter_model(train_df: pd.DataFrame, valid_df: pd.DataFrame, feature_columns: list[str], *, random_state: int, max_auc_gap: float, sample_weight: pd.Series | np.ndarray | None=None) -> tuple[TradeFilterModel, dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    for params in _trade_filter_param_grid():
        model = TradeFilterModel(random_state=random_state, model_params=params).fit(train_df, feature_columns=feature_columns, label_column='label_class', sample_weight=sample_weight)
        train_auc = evaluate_trade_filter_model(model, train_df, feature_columns=feature_columns, label_column='label_class').get('auc', float('nan'))
        valid_auc = evaluate_trade_filter_model(model, valid_df, feature_columns=feature_columns, label_column='label_class').get('auc', float('nan'))
        trials.append({'model': model, 'params': dict(params), 'train_auc': train_auc, 'valid_auc': valid_auc})
    best = _select_best_param_trial(trials, max_auc_gap=max_auc_gap)
    return (best['model'], best)

def _tune_regime_classifier_model(train_df: pd.DataFrame, valid_df: pd.DataFrame, feature_columns: list[str], *, random_state: int, max_auc_gap: float, sample_weight: pd.Series | np.ndarray | None=None) -> tuple[RegimeClassifierModel, dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    for params in _regime_classifier_param_grid():
        model = RegimeClassifierModel(random_state=random_state, model_params=params).fit(train_df, feature_columns=feature_columns, label_column='regime_label', sample_weight=sample_weight)
        train_auc = evaluate_regime_model(model, train_df, feature_columns=feature_columns, label_column='regime_label').get('auc', float('nan'))
        valid_auc = evaluate_regime_model(model, valid_df, feature_columns=feature_columns, label_column='regime_label').get('auc', float('nan'))
        trials.append({'model': model, 'params': dict(params), 'train_auc': train_auc, 'valid_auc': valid_auc})
    best = _select_best_param_trial(trials, max_auc_gap=max_auc_gap)
    return (best['model'], best)

def _tune_mfe_mae_model(train_df: pd.DataFrame, valid_df: pd.DataFrame, feature_columns: list[str], *, random_state: int, max_auc_gap: float, sample_weight: pd.Series | np.ndarray | None=None) -> tuple[MfeMaeModel | None, str, dict[str, Any]]:
    trials: list[dict[str, Any]] = []
    for params in _mfe_mae_param_grid():
        model, model_kind = _train_mfe_mae_or_skip(train_df, feature_columns=feature_columns, random_state=random_state, model_params=params, sample_weight=sample_weight)
        if model is None:
            train_auc = float('nan')
            valid_auc = float('nan')
        else:
            train_exec = train_df.loc[pd.to_numeric(train_df['is_executed'], errors='coerce').fillna(0).astype(int) == 1]
            valid_exec = valid_df.loc[pd.to_numeric(valid_df['is_executed'], errors='coerce').fillna(0).astype(int) == 1]
            if train_exec.empty:
                train_auc = float('nan')
            else:
                train_auc = evaluate_mfe_mae_model(model, train_exec, feature_columns=feature_columns, mfe_column='future_mfe_atr', mae_column='future_mae_atr', mae_penalty=LABEL_MAE_PENALTY, direction_threshold=LABEL_THRESHOLD).get('direction_auc', float('nan'))
            if valid_exec.empty:
                valid_auc = float('nan')
            else:
                valid_auc = evaluate_mfe_mae_model(model, valid_exec, feature_columns=feature_columns, mfe_column='future_mfe_atr', mae_column='future_mae_atr', mae_penalty=LABEL_MAE_PENALTY, direction_threshold=LABEL_THRESHOLD).get('direction_auc', float('nan'))
        trials.append({'model': model, 'model_kind': model_kind, 'params': dict(params), 'train_auc': train_auc, 'valid_auc': valid_auc})
    best = _select_best_param_trial(trials, max_auc_gap=max_auc_gap)
    return (best.get('model'), str(best.get('model_kind', MFE_MAE_KIND_SKIPPED_NO_EXEC)), best)
