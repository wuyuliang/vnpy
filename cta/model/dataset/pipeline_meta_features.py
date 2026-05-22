"""Meta-feature helpers for pipeline_orchestrator."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import (
    MfeMaeModel,
    RegimeClassifierModel,
    TimeSeriesSplit,
    TradeFilterModel,
    np,
    pd,
)

def _build_time_series_splits(n_rows: int, *, max_splits: int=3) -> list[tuple[np.ndarray, np.ndarray]]:
    if int(n_rows) <= 4:
        return []
    n_splits = min(int(max_splits), int(n_rows) - 1)
    if n_splits < 2:
        return []
    tscv = TimeSeriesSplit(n_splits=n_splits)
    return [(tr_idx, va_idx) for tr_idx, va_idx in tscv.split(np.arange(int(n_rows)))]

def _build_oof_trade_prob(base_df: pd.DataFrame, *, feature_columns: list[str], random_state: int, model_params: dict[str, Any], sample_weight: pd.Series | np.ndarray | None=None) -> np.ndarray:
    full_model = TradeFilterModel(random_state=random_state, model_params=dict(model_params)).fit(base_df, feature_columns=feature_columns, label_column='label_class', sample_weight=sample_weight)
    full_pred = np.asarray(full_model.predict_proba(base_df, feature_columns=feature_columns), dtype=float)
    out = np.array(full_pred, dtype=float, copy=True)
    splits = _build_time_series_splits(len(base_df), max_splits=3)
    if not splits:
        return out
    out[:] = np.nan
    for tr_idx, va_idx in splits:
        fold_train = base_df.iloc[tr_idx].copy()
        fold_valid = base_df.iloc[va_idx].copy()
        fold_weight: pd.Series | np.ndarray | None = None
        if sample_weight is not None:
            if isinstance(sample_weight, pd.Series):
                fold_weight = sample_weight.reindex(base_df.index).iloc[tr_idx]
            else:
                fold_weight = np.asarray(sample_weight, dtype=float).reshape(-1)[tr_idx]
        fold_model = TradeFilterModel(random_state=random_state, model_params=dict(model_params)).fit(fold_train, feature_columns=feature_columns, label_column='label_class', sample_weight=fold_weight)
        out[va_idx] = np.asarray(fold_model.predict_proba(fold_valid, feature_columns=feature_columns), dtype=float)
    return np.where(np.isfinite(out), out, full_pred)

def _build_oof_regime_pred(base_df: pd.DataFrame, *, feature_columns: list[str], random_state: int, model_params: dict[str, Any], sample_weight: pd.Series | np.ndarray | None=None) -> np.ndarray:
    full_model = RegimeClassifierModel(random_state=random_state, model_params=dict(model_params)).fit(base_df, feature_columns=feature_columns, label_column='regime_label', sample_weight=sample_weight)
    full_pred = np.asarray(full_model.predict(base_df, feature_columns=feature_columns), dtype=object)
    out = np.array(full_pred, dtype=object, copy=True)
    splits = _build_time_series_splits(len(base_df), max_splits=3)
    if not splits:
        return out
    out[:] = ''
    for tr_idx, va_idx in splits:
        fold_train = base_df.iloc[tr_idx].copy()
        fold_valid = base_df.iloc[va_idx].copy()
        fold_weight: pd.Series | np.ndarray | None = None
        if sample_weight is not None:
            if isinstance(sample_weight, pd.Series):
                fold_weight = sample_weight.reindex(base_df.index).iloc[tr_idx]
            else:
                fold_weight = np.asarray(sample_weight, dtype=float).reshape(-1)[tr_idx]
        fold_model = RegimeClassifierModel(random_state=random_state, model_params=dict(model_params)).fit(fold_train, feature_columns=feature_columns, label_column='regime_label', sample_weight=fold_weight)
        out[va_idx] = np.asarray(fold_model.predict(fold_valid, feature_columns=feature_columns), dtype=object)
    mask_empty = pd.Series(out).astype(str).eq('')
    out[mask_empty.to_numpy()] = full_pred[mask_empty.to_numpy()]
    return out

def _build_oof_mfe_mae_pred(base_df: pd.DataFrame, *, feature_columns: list[str], random_state: int, model_params: dict[str, Any], sample_weight: pd.Series | np.ndarray | None=None) -> tuple[np.ndarray, np.ndarray]:
    full_model = MfeMaeModel(random_state=random_state, model_params=dict(model_params))
    full_model.fit(base_df, feature_columns=feature_columns, mfe_column='future_mfe_atr', mae_column='future_mae_atr', sample_weight=sample_weight)
    full_pred = full_model.predict(base_df, feature_columns=feature_columns)
    full_mfe = pd.to_numeric(full_pred['pred_mfe_atr'], errors='coerce').fillna(0.0).to_numpy(dtype=float)
    full_mae = pd.to_numeric(full_pred['pred_mae_atr'], errors='coerce').fillna(0.0).to_numpy(dtype=float)
    out_mfe = np.array(full_mfe, dtype=float, copy=True)
    out_mae = np.array(full_mae, dtype=float, copy=True)
    splits = _build_time_series_splits(len(base_df), max_splits=3)
    if not splits:
        return (out_mfe, out_mae)
    out_mfe[:] = np.nan
    out_mae[:] = np.nan
    for tr_idx, va_idx in splits:
        fold_train = base_df.iloc[tr_idx].copy()
        fold_valid = base_df.iloc[va_idx].copy()
        fold_weight: pd.Series | np.ndarray | None = None
        if sample_weight is not None:
            if isinstance(sample_weight, pd.Series):
                fold_weight = sample_weight.reindex(base_df.index).iloc[tr_idx]
            else:
                fold_weight = np.asarray(sample_weight, dtype=float).reshape(-1)[tr_idx]
        fold_model = MfeMaeModel(random_state=random_state, model_params=dict(model_params))
        fold_model.fit(fold_train, feature_columns=feature_columns, mfe_column='future_mfe_atr', mae_column='future_mae_atr', sample_weight=fold_weight)
        fold_pred = fold_model.predict(fold_valid, feature_columns=feature_columns)
        out_mfe[va_idx] = pd.to_numeric(fold_pred['pred_mfe_atr'], errors='coerce').fillna(0.0).to_numpy(dtype=float)
        out_mae[va_idx] = pd.to_numeric(fold_pred['pred_mae_atr'], errors='coerce').fillna(0.0).to_numpy(dtype=float)
    out_mfe = np.where(np.isfinite(out_mfe), out_mfe, full_mfe)
    out_mae = np.where(np.isfinite(out_mae), out_mae, full_mae)
    return (out_mfe, out_mae)

def _format_ts(v: Any) -> str:
    ts = pd.to_datetime(v, errors='coerce')
    if pd.isna(ts):
        return ''
    return pd.Timestamp(ts).strftime('%Y-%m-%d %H:%M:%S')

def _build_split_span(df: pd.DataFrame) -> tuple[str, str]:
    if df.empty or 'datetime' not in df.columns:
        return ('', '')
    dt = pd.to_datetime(df['datetime'], errors='coerce').dropna()
    if dt.empty:
        return ('', '')
    return (_format_ts(dt.min()), _format_ts(dt.max()))

def _compute_feature_null_stats(df: pd.DataFrame, feature_columns: Sequence[str]) -> dict[str, Any]:
    cols = [c for c in feature_columns if c in df.columns]
    if not cols:
        return {'feature_count': 0, 'feature_null_ratio_mean': float('nan'), 'feature_null_ratio_max': float('nan'), 'feature_null_feature_count': 0, 'feature_all_null_count': 0}
    frame = df[cols]
    null_ratio = frame.isna().mean(axis=0)
    return {'feature_count': int(len(cols)), 'feature_null_ratio_mean': float(null_ratio.mean()) if len(null_ratio) else float('nan'), 'feature_null_ratio_max': float(null_ratio.max()) if len(null_ratio) else float('nan'), 'feature_null_feature_count': int((null_ratio > 0.0).sum()), 'feature_all_null_count': int((null_ratio >= 1.0).sum())}

def _compute_feature_ic_stats(df: pd.DataFrame, feature_columns: Sequence[str], target: pd.Series, *, min_pairs: int=20, max_rows: int=5000) -> dict[str, Any]:
    cols = [c for c in feature_columns if c in df.columns]
    if not cols or df.empty:
        return {'ic_abs_mean': float('nan'), 'ic_abs_median': float('nan'), 'ic_abs_top': float('nan'), 'ic_top_feature': ''}
    y = pd.to_numeric(target, errors='coerce')
    base = pd.DataFrame({'_target': y}, index=df.index)
    if len(base) > int(max_rows):
        idx = np.linspace(0, len(base) - 1, int(max_rows), dtype=int)
        base = base.iloc[idx].copy()
    values: list[tuple[str, float]] = []
    for col in cols:
        x = pd.to_numeric(df[col], errors='coerce')
        if len(x) != len(df):
            continue
        s = pd.DataFrame({'x': x, 'y': base['_target']}, index=df.index).loc[base.index].dropna()
        if len(s) < int(min_pairs):
            continue
        if s['x'].nunique() < 2 or s['y'].nunique() < 2:
            continue
        ic = s['x'].corr(s['y'], method='spearman')
        if pd.isna(ic):
            continue
        values.append((str(col), float(ic)))
    if not values:
        return {'ic_abs_mean': float('nan'), 'ic_abs_median': float('nan'), 'ic_abs_top': float('nan'), 'ic_top_feature': ''}
    values = sorted(values, key=lambda x: abs(x[1]), reverse=True)
    abs_vals = np.asarray([abs(v) for _, v in values], dtype=float)
    return {'ic_abs_mean': float(np.mean(abs_vals)), 'ic_abs_median': float(np.median(abs_vals)), 'ic_abs_top': float(abs_vals[0]), 'ic_top_feature': str(values[0][0])}

def _regime_to_code(labels: pd.Series) -> pd.Series:
    s = labels.astype(str).str.lower().fillna('range')
    out = pd.Series(0.0, index=labels.index, dtype=float)
    out.loc[s == 'trend_up'] = 1.0
    out.loc[s == 'trend_down'] = -1.0
    return out

def _build_final_decision_features(df: pd.DataFrame, *, trade_prob: np.ndarray, regime_label: Sequence[str], pred_mfe: np.ndarray, pred_mae: np.ndarray, mae_penalty: float) -> pd.DataFrame:
    out = pd.DataFrame(index=df.index)
    out['meta_trade_filter_prob'] = pd.to_numeric(pd.Series(trade_prob, index=df.index), errors='coerce').fillna(0.0)
    out['meta_regime_code'] = _regime_to_code(pd.Series(regime_label, index=df.index))
    out['meta_pred_mfe_atr'] = pd.to_numeric(pd.Series(pred_mfe, index=df.index), errors='coerce').fillna(0.0)
    out['meta_pred_mae_atr'] = pd.to_numeric(pd.Series(pred_mae, index=df.index), errors='coerce').fillna(0.0)
    out['meta_pred_edge_atr'] = out['meta_pred_mfe_atr'] - float(mae_penalty) * out['meta_pred_mae_atr']
    side_raw = df.get('side', pd.Series([''] * len(df), index=df.index))
    side_code = np.where(side_raw.astype(str).str.lower() == 'long', 1.0, -1.0)
    out['meta_side_code'] = pd.Series(side_code, index=df.index, dtype=float)
    out['meta_edge_side'] = out['meta_pred_edge_atr'] * out['meta_side_code']
    return out
