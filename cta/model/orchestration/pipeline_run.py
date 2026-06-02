"""Primary run_model_pipeline implementation."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import DEFAULT_REPORT_ROOT, DEFAULT_OOT_EVAL_CONFIG, FEATURE_ROOT, FinalDecisionModel, GenericMode, LABEL_MAE_PENALTY, LABEL_THRESHOLD, ModelPipelineResult, OotEvaluationConfig, Path, ScoreCalibrator, Sequence, WindowMode, _build_training_feature_table_with_auto_fallback, _evaluate_oot_real_execution, _safe_name, evaluate_final_decision_model, evaluate_mfe_mae_model, evaluate_regime_model, evaluate_trade_filter_model, infer_symbol_cluster, json, logger, normalize_interval, np, pd, seed_all, write_pipeline_oot_html_report
from cta.model.dataset.pipeline_dataset_prep import _build_candidate_table, _build_pooled_feature_df, _build_walk_forward_windows, _ensure_binary_label_diversity, _ensure_training_columns, _resolve_generic_columns, _select_feature_columns
from cta.model.reporting.pipeline_diagnostics import _build_symbol_cluster_sample_weight, _build_top_feature_concentration_alerts, _build_valid_test_gap_alerts, _dump_feature_manifest, _empty_top_feature_importance_frame, _final_model_importance_df, _log_top_feature_importance, _tag_top_feature_importance
from cta.model.dataset.pipeline_feature_curation import _filter_model_leakage_features, _list_unaudited_features
from cta.model.dataset.pipeline_meta_features import _build_final_decision_features, _build_oof_mfe_mae_pred, _build_oof_regime_pred, _build_oof_trade_prob, _build_split_span, _compute_feature_ic_stats, _compute_feature_null_stats
from cta.model.orchestration.pipeline_run_final_models import fit_side_final_model, predict_dual_side_final_scores
from cta.model.orchestration.pipeline_run_predictions import (
    build_test_prediction_frame,
    write_score_distribution_baseline_from_scored_rows,
    write_score_quantile_manifest_from_scored_rows,
)
from cta.model.reporting.pipeline_outputs import _build_last_oot_decile_table, _write_provenance
from cta.model.training.pipeline_param_grids import _safe_float, _tune_mfe_mae_model, _tune_regime_classifier_model, _tune_trade_filter_model


def run_model_pipeline(symbol: str='RB0', exchange: str | None='SHFE', interval: str='60min', start_date: str='2000-01-01', end_date: str='2019-12-31', trade_side_mode: str='both', train_end: str='2018-12-31', valid_end: str='2019-06-30', output_root: Path | None=None, feature_root: Path=FEATURE_ROOT, synthetic_periods: int=400, by_signal_type: bool=True, max_walk_forward_windows: int=3, window_mode: WindowMode='expanding', rolling_train_years: int=3, rolling_valid_years: int=1, rolling_test_years: int=1, rolling_step_years: int=1, max_auc_gap: float=0.03, max_valid_test_gap: float=0.1, top_feature_importance_alert_pct: float=0.5, min_train_samples: int=50, min_valid_samples: int=20, min_test_samples: int=20, max_unaudited_features: int=500, generic_mode: GenericMode='auto', oot_eval_config: OotEvaluationConfig=DEFAULT_OOT_EVAL_CONFIG, pool_symbols: Sequence[tuple[str, str | None]] | None=None, pool_name: str | None=None, min_used_symbols: int=2, seed: int=2026, rotation_cfg: 'object | None'=None) -> ModelPipelineResult:
    """Run full candidate->feature->model pipeline.

    G1: ``generic_mode`` 控制 generic 特征拼接策略：
      - ``"auto"`` (默认): 拼上磁盘 parquet 上**所有数值列**（~400 个特征）; - ``"whitelist"``: 仅 18 列 ``DEFAULT_GENERIC_COLUMNS`` 历史白名单

    Pool mode：传入 ``pool_symbols=[(sym, ex), ...]`` 时，把多 symbol 的样本拼成; 一个共享训练集训出**单个跨品种模型**（输出目录用 ``POOL`` 替代单品种代码）。; 适合 day 等单品种样本不足的 interval。``symbol`` / ``exchange`` 参数在 pool; 模式下被忽略。

    过拟合控制：; - 三类模型都做参数网格搜索；; - 选参仅基于 train/valid AUC，并约束 ``abs(train-valid) <= max_auc_gap``；; - OOT(test) 严格不参与选参，只用于最终效果评估。
    """
    run_date = pd.Timestamp.now().strftime('%Y%m%d')
    rng_seed = int(seed_all(seed))
    is_pool = bool(pool_symbols)
    if is_pool:
        pool_label = str(pool_name).strip().upper() if pool_name else 'POOL'
        sym = pool_label or 'POOL'
    else:
        sym = str(symbol).upper()
    interval_norm = normalize_interval(interval)
    root = output_root or DEFAULT_REPORT_ROOT
    out_dir = root / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_model_pipeline'
    out_dir.mkdir(parents=True, exist_ok=True)
    process_steps: list[str] = []
    pool_meta_path: Path | None = None
    process_steps.append('1) 生成候选样本（candidate events）')
    if is_pool:
        from cta.config.symbol_disable import filter_out_disabled_pairs
        pool_symbols_filtered = filter_out_disabled_pairs(list(pool_symbols))
        candidate_df, feature_df = _build_pooled_feature_df(pool_symbols=pool_symbols_filtered, interval=interval_norm, start_date=start_date, end_date=end_date, trade_side_mode=trade_side_mode, synthetic_periods=synthetic_periods, feature_root=feature_root, generic_columns=_resolve_generic_columns(generic_mode), rotation_cfg=rotation_cfg)
        if feature_df.empty:
            raise ValueError('POOL mode produced empty real-data feature table. No pool symbol has usable local bars/features under current interval.')
        ex = ''
        used_symbols = set(feature_df.get('symbol', pd.Series(dtype=str)).astype(str).str.upper().tolist())
        pool_meta = pd.DataFrame([{'symbol': s, 'exchange': e or '', 'used_in_training': int(str(s).upper() in used_symbols)} for s, e in pool_symbols_filtered])
        used_count = int(pool_meta['used_in_training'].sum()) if not pool_meta.empty else 0
        logger.info('POOL mode real-data symbols used: %d/%d (after symbol_disable_manifest filter)', used_count, len(pool_symbols_filtered or []))
        pool_meta_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_pool_members.csv'
        pool_meta.to_csv(pool_meta_path, index=False, encoding='utf-8-sig')
        min_required = max(1, int(min_used_symbols))
        if used_count < min_required:
            raise ValueError(f'POOL mode used symbol coverage too low: used={used_count}, required>={min_required}, pool_members_csv={pool_meta_path}')
        candidate_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_candidates.csv'
        candidate_df.to_csv(candidate_path, index=False, encoding='utf-8-sig')
    else:
        candidate_df, ex = _build_candidate_table(symbol=sym, exchange=exchange, interval=interval_norm, start_date=start_date, end_date=end_date, trade_side_mode=trade_side_mode, synthetic_periods=synthetic_periods)
        candidate_df = _ensure_training_columns(candidate_df)
        candidate_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_candidates.csv'
        candidate_df.to_csv(candidate_path, index=False, encoding='utf-8-sig')
    cand_start, cand_end = _build_split_span(candidate_df)
    process_steps.append(f"- candidate_count={len(candidate_df)}, period={cand_start or 'NA'} -> {cand_end or 'NA'}, file={candidate_path.name}")
    process_steps.append('2) 拼接候选样本对应的通用特征 + 模型训练特征')
    if is_pool:
        feature_df = _ensure_training_columns(feature_df)
    else:
        feature_df = _build_training_feature_table_with_auto_fallback(candidate_df=candidate_df, symbol=sym, interval=interval_norm, feature_root=feature_root, generic_columns=_resolve_generic_columns(generic_mode))
        feature_df = _ensure_training_columns(feature_df)
    warmed_mask = feature_df['atr_warmed'].astype(int) == 1
    n_total = int(len(feature_df))
    n_dropped = int((~warmed_mask).sum())
    if n_dropped > 0:
        logger.warning('drop ATR warmup samples: dropped=%s total=%s ratio=%.4f', n_dropped, n_total, n_dropped / n_total if n_total else 0.0)
    feature_df = feature_df.loc[warmed_mask].reset_index(drop=True)
    feature_table_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_feature_table.csv'
    feature_df.to_csv(feature_table_path, index=False, encoding='utf-8-sig')
    model_feature_columns = [c for c in feature_df.columns if c.startswith('feature_') or c.startswith('generic_')]
    all_feat_null_stats = _compute_feature_null_stats(feature_df, model_feature_columns)
    feat_start, feat_end = _build_split_span(feature_df)
    process_steps.append(f"- feature_sample_count={len(feature_df)}, period={feat_start or 'NA'} -> {feat_end or 'NA'}, feature_cols={all_feat_null_stats['feature_count']}, null_ratio_mean={_safe_float(all_feat_null_stats['feature_null_ratio_mean']):.6f}, null_ratio_max={_safe_float(all_feat_null_stats['feature_null_ratio_max']):.6f}, file={feature_table_path.name}")
    process_steps.append('3) 按 signal_type / walk-forward 训练三类模型并做参数搜索（仅 train/valid）')
    if by_signal_type:
        signal_frames = [(str(st), g.copy()) for st, g in feature_df.groupby('signal_type') if not g.empty]
    else:
        signal_frames = [('all_signal_types', feature_df.copy())]
    metrics_parts: list[pd.DataFrame] = []
    prediction_parts: list[pd.DataFrame] = []
    top_feature_parts: list[pd.DataFrame] = []
    risk_manifest_parts: list[pd.DataFrame] = []
    model_root = out_dir / 'models'

    def _append_risk_manifest_rows(
        frame: pd.DataFrame,
        probs: np.ndarray,
        *,
        split_name: str,
        signal_type_key: str,
        window_id: int,
    ) -> None:
        if frame.empty:
            return
        part = pd.DataFrame(
            {
                "symbol": frame.get("symbol", pd.Series([""] * len(frame), index=frame.index)).astype(str).str.upper(),
                "interval": frame.get("interval", pd.Series([""] * len(frame), index=frame.index)).astype(str),
                "trade_filter_prob": pd.to_numeric(pd.Series(probs, index=frame.index), errors="coerce"),
                "split": split_name,
                "signal_type": signal_type_key,
                "window_id": int(window_id),
            },
            index=frame.index,
        )
        part["cluster_name"] = part["symbol"].map(lambda s: infer_symbol_cluster(str(s).upper()))
        part = part.dropna(subset=["trade_filter_prob"]).copy()
        if not part.empty:
            risk_manifest_parts.append(part.reset_index(drop=True))
    for signal_type_key, sig_df in signal_frames:
        sig_df = sig_df.sort_values('datetime').reset_index(drop=True)
        sig_df, feature_columns = _select_feature_columns(sig_df)
        if by_signal_type and 'feature_signal_code' in feature_columns:
            feature_columns = [c for c in feature_columns if c != 'feature_signal_code']
            if not feature_columns:
                sig_df['feature_fallback'] = 0.0
                feature_columns = ['feature_fallback']
        trade_feature_columns = _filter_model_leakage_features(feature_columns, model_name='trade_filter')
        regime_feature_columns = _filter_model_leakage_features(feature_columns, model_name='regime_classifier')
        mfe_feature_columns = _filter_model_leakage_features(feature_columns, model_name='mfe_mae')
        if not trade_feature_columns:
            sig_df['feature_fallback'] = 0.0
            trade_feature_columns = ['feature_fallback']
        if not regime_feature_columns:
            sig_df['feature_fallback'] = 0.0
            regime_feature_columns = ['feature_fallback']
        if not mfe_feature_columns:
            sig_df['feature_fallback'] = 0.0
            mfe_feature_columns = ['feature_fallback']
        windows = _build_walk_forward_windows(sig_df, train_end=train_end, valid_end=valid_end, max_windows=max_walk_forward_windows, window_mode=window_mode, rolling_train_years=rolling_train_years, rolling_valid_years=rolling_valid_years, rolling_test_years=rolling_test_years, rolling_step_years=rolling_step_years)
        if int(max_walk_forward_windows) > 1 and len(windows) <= 1:
            logger.warning('walk-forward produced only %d window for signal=%s (requested=%d). Consider shrinking train/valid span to increase OOT windows.', len(windows), signal_type_key, int(max_walk_forward_windows))
        for win in windows:
            train_df = _ensure_binary_label_diversity(win.train)
            valid_df = win.valid
            test_df = win.test
            if train_df.empty:
                continue
            train_n = int(len(train_df))
            valid_n = int(len(valid_df))
            test_n = int(len(test_df))
            if train_n < int(min_train_samples) or valid_n < int(min_valid_samples) or test_n < int(min_test_samples):
                logger.warning('skip window due insufficient_sample | signal=%s window=%s train=%d valid=%d test=%d (min_train=%d min_valid=%d min_test=%d)', signal_type_key, win.window_id, train_n, valid_n, test_n, int(min_train_samples), int(min_valid_samples), int(min_test_samples))
                metrics_parts.append(pd.DataFrame([{'signal_type': signal_type_key, 'window_id': int(win.window_id), 'model': 'window_guard', 'split': 'window_guard', 'split_sample_count': int(train_n + valid_n + test_n), 'insufficient_sample': 1, 'train_sample_count': train_n, 'valid_sample_count': valid_n, 'test_sample_count': test_n}]))
                continue
            train_exec_mask = pd.to_numeric(train_df.get('is_executed', 0), errors='coerce').fillna(0).astype(int) == 1
            train_executed_count = int(train_exec_mask.sum())
            train_non_executed_count = int(len(train_df) - train_executed_count)
            logger.debug('train mix signal=%s window=%s total=%s executed=%s non_executed=%s', signal_type_key, win.window_id, len(train_df), train_executed_count, train_non_executed_count)
            cluster_sample_weight = _build_symbol_cluster_sample_weight(train_df)
            trade_model, trade_selected = _tune_trade_filter_model(train_df, valid_df, feature_columns=trade_feature_columns, random_state=rng_seed, max_auc_gap=max_auc_gap, sample_weight=cluster_sample_weight)
            regime_model, regime_selected = _tune_regime_classifier_model(train_df, valid_df, feature_columns=regime_feature_columns, random_state=rng_seed, max_auc_gap=max_auc_gap, sample_weight=cluster_sample_weight)
            mfe_mae_model, mfe_mae_kind, mfe_selected = _tune_mfe_mae_model(train_df, valid_df, feature_columns=mfe_feature_columns, random_state=rng_seed, max_auc_gap=max_auc_gap, sample_weight=cluster_sample_weight)
            train_trade_prob = _build_oof_trade_prob(train_df, feature_columns=trade_feature_columns, random_state=rng_seed, model_params=dict(trade_selected.get('params', {})), sample_weight=cluster_sample_weight)
            valid_trade_prob = trade_model.predict_proba(valid_df, feature_columns=trade_feature_columns)
            test_trade_prob = trade_model.predict_proba(test_df, feature_columns=trade_feature_columns)
            _append_risk_manifest_rows(
                train_df,
                np.asarray(train_trade_prob, dtype=float),
                split_name="train",
                signal_type_key=signal_type_key,
                window_id=int(win.window_id),
            )
            _append_risk_manifest_rows(
                valid_df,
                np.asarray(valid_trade_prob, dtype=float),
                split_name="valid",
                signal_type_key=signal_type_key,
                window_id=int(win.window_id),
            )
            train_regime_pred = _build_oof_regime_pred(train_df, feature_columns=regime_feature_columns, random_state=rng_seed, model_params=dict(regime_selected.get('params', {})), sample_weight=cluster_sample_weight)
            valid_regime_pred = regime_model.predict(valid_df, feature_columns=regime_feature_columns)
            test_regime_pred = regime_model.predict(test_df, feature_columns=regime_feature_columns)
            if mfe_mae_model is None:
                train_pred_mfe = np.zeros(len(train_df), dtype=float)
                train_pred_mae = np.zeros(len(train_df), dtype=float)
                valid_pred_mfe = np.zeros(len(valid_df), dtype=float)
                valid_pred_mae = np.zeros(len(valid_df), dtype=float)
                test_pred_mfe = np.zeros(len(test_df), dtype=float)
                test_pred_mae = np.zeros(len(test_df), dtype=float)
            else:
                train_pred_mfe, train_pred_mae = _build_oof_mfe_mae_pred(train_df, feature_columns=mfe_feature_columns, random_state=rng_seed, model_params=dict(mfe_selected.get('params', {})), sample_weight=cluster_sample_weight)
                p_valid = mfe_mae_model.predict(valid_df, feature_columns=mfe_feature_columns)
                p_test = mfe_mae_model.predict(test_df, feature_columns=mfe_feature_columns)
                valid_pred_mfe = pd.to_numeric(p_valid['pred_mfe_atr'], errors='coerce').fillna(0.0).to_numpy()
                valid_pred_mae = pd.to_numeric(p_valid['pred_mae_atr'], errors='coerce').fillna(0.0).to_numpy()
                test_pred_mfe = pd.to_numeric(p_test['pred_mfe_atr'], errors='coerce').fillna(0.0).to_numpy()
                test_pred_mae = pd.to_numeric(p_test['pred_mae_atr'], errors='coerce').fillna(0.0).to_numpy()
            meta_train = _build_final_decision_features(train_df, trade_prob=train_trade_prob, regime_label=train_regime_pred, pred_mfe=train_pred_mfe, pred_mae=train_pred_mae, mae_penalty=LABEL_MAE_PENALTY)
            meta_valid = _build_final_decision_features(valid_df, trade_prob=valid_trade_prob, regime_label=valid_regime_pred, pred_mfe=valid_pred_mfe, pred_mae=valid_pred_mae, mae_penalty=LABEL_MAE_PENALTY)
            meta_test = _build_final_decision_features(test_df, trade_prob=test_trade_prob, regime_label=test_regime_pred, pred_mfe=test_pred_mfe, pred_mae=test_pred_mae, mae_penalty=LABEL_MAE_PENALTY)
            final_feature_columns = list(meta_train.columns)
            final_train_df = train_df.copy()
            final_valid_df = valid_df.copy()
            final_test_df = test_df.copy()
            for c in final_feature_columns:
                final_train_df[c] = meta_train[c].to_numpy()
                final_valid_df[c] = meta_valid[c].to_numpy()
                final_test_df[c] = meta_test[c].to_numpy()
            final_model = FinalDecisionModel(random_state=rng_seed).fit(final_train_df, feature_columns=final_feature_columns, label_column='label_class', sample_weight=cluster_sample_weight, ensemble=True)
            final_model_long = fit_side_final_model(final_train_df, feature_columns=final_feature_columns, random_state=rng_seed, sample_weight=cluster_sample_weight, side='long')
            final_model_short = fit_side_final_model(final_train_df, feature_columns=final_feature_columns, random_state=rng_seed, sample_weight=cluster_sample_weight, side='short')
            long_kind = final_model_long.model_kind if final_model_long is not None else f'{final_model.model_kind}_global_fallback'
            short_kind = final_model_short.model_kind if final_model_short is not None else f'{final_model.model_kind}_global_fallback'
            final_dual_model_kind = f'dual_side|long={long_kind}|short={short_kind}|global={final_model.model_kind}'
            from cta.model.training.bull_regime_strength_model import BullRegimeStrengthModel
            from cta.model.training.pyramid_eligibility_model import PyramidEligibilityModel
            from cta.model.training.trend_persistence_model import TrendPersistenceModel
            bull_strength_model = BullRegimeStrengthModel(random_state=rng_seed).fit(train_df, feature_columns=trade_feature_columns)
            trend_persistence_model = TrendPersistenceModel(random_state=rng_seed).fit(train_df, feature_columns=mfe_feature_columns)
            pyramid_model = PyramidEligibilityModel(random_state=rng_seed).fit(train_df, feature_columns=mfe_feature_columns)
            final_train_eval = evaluate_final_decision_model(final_model, final_train_df, feature_columns=final_feature_columns, label_column='label_class')
            final_valid_eval = evaluate_final_decision_model(final_model, final_valid_df, feature_columns=final_feature_columns, label_column='label_class')
            final_selected = {'params': {'meta_features': final_feature_columns}, 'train_auc': _safe_float(final_train_eval.get('auc')), 'valid_auc': _safe_float(final_valid_eval.get('auc')), 'auc_gap': abs(_safe_float(final_train_eval.get('auc')) - _safe_float(final_valid_eval.get('auc')))}
            final_selected['params']['dual_side'] = {'long': long_kind, 'short': short_kind, 'global': final_model.model_kind}
            logger.info('param-search selected | signal=%s window=%s | trade(valid_auc=%.4f gap=%.4f) regime(valid_auc=%.4f gap=%.4f) mfe(valid_auc=%.4f gap=%.4f) final(valid_auc=%.4f gap=%.4f)', signal_type_key, win.window_id, _safe_float(trade_selected.get('valid_auc')), _safe_float(trade_selected.get('auc_gap')), _safe_float(regime_selected.get('valid_auc')), _safe_float(regime_selected.get('auc_gap')), _safe_float(mfe_selected.get('valid_auc')), _safe_float(mfe_selected.get('auc_gap')), _safe_float(final_selected.get('valid_auc')), _safe_float(final_selected.get('auc_gap')))
            trade_params_json = json.dumps(trade_selected.get('params', {}), ensure_ascii=False, sort_keys=True)
            regime_params_json = json.dumps(regime_selected.get('params', {}), ensure_ascii=False, sort_keys=True)
            mfe_params_json = json.dumps(mfe_selected.get('params', {}), ensure_ascii=False, sort_keys=True)
            final_params_json = json.dumps(final_selected.get('params', {}), ensure_ascii=False, sort_keys=True)
            trade_top = _tag_top_feature_importance(trade_model.get_top_feature_importance(train_df, feature_columns=trade_feature_columns, label_column='label_class', top_k=10), signal_type=signal_type_key, window_id=win.window_id, model='trade_filter', model_kind=trade_model.model_kind)
            _log_top_feature_importance(trade_top)
            if not trade_top.empty:
                top_feature_parts.append(trade_top)
            regime_top = _tag_top_feature_importance(regime_model.get_top_feature_importance(feature_columns=regime_feature_columns, top_k=10), signal_type=signal_type_key, window_id=win.window_id, model='regime_classifier', model_kind=regime_model.model_kind)
            _log_top_feature_importance(regime_top)
            if not regime_top.empty:
                top_feature_parts.append(regime_top)
            if mfe_mae_model is None:
                mfe_base = pd.DataFrame({'feature': mfe_feature_columns[:10], 'importance': [0.0] * min(10, len(mfe_feature_columns))})
            else:
                mfe_base = mfe_mae_model.get_top_feature_importance(feature_columns=mfe_feature_columns, top_k=10)
            mfe_top = _tag_top_feature_importance(mfe_base, signal_type=signal_type_key, window_id=win.window_id, model='mfe_mae', model_kind=mfe_mae_kind)
            _log_top_feature_importance(mfe_top)
            if not mfe_top.empty:
                top_feature_parts.append(mfe_top)
            final_base = _final_model_importance_df(final_model, feature_columns=final_feature_columns, top_k=10)
            final_top = _tag_top_feature_importance(final_base, signal_type=signal_type_key, window_id=win.window_id, model='final_decision_stack', model_kind=final_dual_model_kind)
            _log_top_feature_importance(final_top)
            if not final_top.empty:
                top_feature_parts.append(final_top)
            signal_dir = model_root / _safe_name(signal_type_key) / f'window_{win.window_id:02d}'
            trade_joblib = signal_dir / 'trade_filter.joblib'
            regime_joblib = signal_dir / 'regime_classifier.joblib'
            mfe_mae_joblib = signal_dir / 'mfe_mae.joblib'
            final_joblib = signal_dir / 'final_decision_stack.joblib'
            final_long_joblib = signal_dir / 'final_decision_stack_long.joblib'
            final_short_joblib = signal_dir / 'final_decision_stack_short.joblib'
            trade_cal_joblib = signal_dir / 'trade_filter_calibration.joblib'
            regime_cal_joblib = signal_dir / 'regime_classifier_calibration.joblib'
            mfe_mae_cal_joblib = signal_dir / 'mfe_mae_calibration.joblib'
            final_cal_joblib = signal_dir / 'final_decision_stack_calibration.joblib'
            cal_ref_parts = [x for x in (train_df, valid_df) if not x.empty]
            cal_ref_df = pd.concat(cal_ref_parts, axis=0, ignore_index=True) if cal_ref_parts else train_df.copy()
            cal_cluster = cal_ref_df.get('cluster_name') if 'cluster_name' in cal_ref_df.columns else cal_ref_df.get('cluster')
            if cal_cluster is None:
                cal_cluster = cal_ref_df.get('symbol', pd.Series([''] * len(cal_ref_df), index=cal_ref_df.index)).astype(str).map(infer_symbol_cluster)
            else:
                cal_cluster = cal_cluster.astype(str)
            cal_interval = cal_ref_df.get('interval', pd.Series([interval] * len(cal_ref_df), index=cal_ref_df.index)).astype(str)
            cal_datetime = pd.to_datetime(cal_ref_df.get('datetime', pd.Series([pd.NaT] * len(cal_ref_df), index=cal_ref_df.index)), errors='coerce')
            trade_prob_ref = np.asarray(trade_model.predict_proba(cal_ref_df, feature_columns=trade_feature_columns), dtype=float).reshape(-1)
            cal_trade = pd.DataFrame({'cluster_name': np.asarray(cal_cluster, dtype=object), 'interval': np.asarray(cal_interval, dtype=object), 'datetime': np.asarray(cal_datetime, dtype='datetime64[ns]'), 'trade_filter_prob': trade_prob_ref})
            trade_calibrator = ScoreCalibrator.fit_for_holdout(cal_trade, score_column='trade_filter_prob', model_kind='trade_filter')
            if trade_calibrator.calibrations:
                trade_calibrator.save(trade_cal_joblib)
            regime_probs = regime_model.predict_proba(cal_ref_df, feature_columns=regime_feature_columns)
            if regime_probs.ndim == 2 and regime_probs.shape[0] == len(cal_ref_df):
                regime_score = np.nanmax(np.asarray(regime_probs, dtype=float), axis=1)
                cal_regime = pd.DataFrame({'cluster_name': cal_cluster, 'interval': cal_interval, 'datetime': cal_datetime, 'regime_confidence': regime_score})
                regime_calibrator = ScoreCalibrator.fit_for_holdout(cal_regime, score_column='regime_confidence', model_kind='regime_classifier')
                if regime_calibrator.calibrations:
                    regime_calibrator.save(regime_cal_joblib)
            if mfe_mae_model is not None:
                mfe_pred_ref = mfe_mae_model.predict(cal_ref_df, feature_columns=mfe_feature_columns)
                edge_ref = pd.to_numeric(mfe_pred_ref['pred_mfe_atr'], errors='coerce').fillna(0.0) - float(LABEL_MAE_PENALTY) * pd.to_numeric(mfe_pred_ref['pred_mae_atr'], errors='coerce').fillna(0.0)
                cal_mfe = pd.DataFrame({'cluster_name': cal_cluster, 'interval': cal_interval, 'datetime': cal_datetime, 'pred_edge_atr': edge_ref.to_numpy(dtype=float)})
                mfe_calibrator = ScoreCalibrator.fit_for_holdout(cal_mfe, score_column='pred_edge_atr', model_kind='mfe_mae_edge')
                if mfe_calibrator.calibrations:
                    mfe_calibrator.save(mfe_mae_cal_joblib)
            final_ref_parts = [x for x in (final_train_df, final_valid_df) if not x.empty]
            final_ref_df = pd.concat(final_ref_parts, axis=0, ignore_index=True) if final_ref_parts else final_train_df.copy()
            final_cluster = final_ref_df.get('cluster_name') if 'cluster_name' in final_ref_df.columns else final_ref_df.get('cluster')
            if final_cluster is None:
                final_cluster = final_ref_df.get('symbol', pd.Series([''] * len(final_ref_df), index=final_ref_df.index)).astype(str).map(infer_symbol_cluster)
            else:
                final_cluster = final_cluster.astype(str)
            final_interval = final_ref_df.get('interval', pd.Series([interval] * len(final_ref_df), index=final_ref_df.index)).astype(str)
            final_datetime = pd.to_datetime(final_ref_df.get('datetime', pd.Series([pd.NaT] * len(final_ref_df), index=final_ref_df.index)), errors='coerce')
            final_prob_ref, _, _, _ = predict_dual_side_final_scores(final_ref_df, feature_columns=final_feature_columns, global_model=final_model, long_model=final_model_long, short_model=final_model_short)
            cal_final = pd.DataFrame({'cluster_name': np.asarray(final_cluster, dtype=object), 'interval': np.asarray(final_interval, dtype=object), 'datetime': np.asarray(final_datetime, dtype='datetime64[ns]'), 'final_decision_score': np.asarray(final_prob_ref, dtype=float).reshape(-1)})
            final_calibrator = ScoreCalibrator.fit_for_holdout(cal_final, score_column='final_decision_score', model_kind='final_decision_stack')
            if final_calibrator.calibrations:
                final_calibrator.save(final_cal_joblib)
            trade_model.save(trade_joblib)
            regime_model.save(regime_joblib)
            if mfe_mae_model is not None:
                mfe_mae_model.save(mfe_mae_joblib)
            final_model.save(final_joblib)
            if final_model_long is not None:
                final_model_long.save(final_long_joblib)
            if final_model_short is not None:
                final_model_short.save(final_short_joblib)
            n_feat_trade = len(trade_feature_columns)
            try:
                trade_full = trade_model.get_top_feature_importance(train_df, feature_columns=trade_feature_columns, label_column='label_class', top_k=n_feat_trade)
            except Exception as exc:
                logger.warning('trade_filter full importance failed: %s', exc)
                trade_full = None
            _dump_feature_manifest(joblib_path=trade_joblib, feature_columns=trade_feature_columns, importance_df=trade_full, model_kind=trade_model.model_kind)
            n_feat_regime = len(regime_feature_columns)
            try:
                regime_full = regime_model.get_top_feature_importance(feature_columns=regime_feature_columns, top_k=n_feat_regime)
            except Exception as exc:
                logger.warning('regime_classifier full importance failed: %s', exc)
                regime_full = None
            _dump_feature_manifest(joblib_path=regime_joblib, feature_columns=regime_feature_columns, importance_df=regime_full, model_kind=regime_model.model_kind)
            if mfe_mae_model is not None:
                n_feat_mfe = len(mfe_feature_columns)
                try:
                    mfe_full = mfe_mae_model.get_top_feature_importance(feature_columns=mfe_feature_columns, top_k=n_feat_mfe)
                except Exception as exc:
                    logger.warning('mfe_mae full importance failed: %s', exc)
                    mfe_full = None
                _dump_feature_manifest(joblib_path=mfe_mae_joblib, feature_columns=mfe_feature_columns, importance_df=mfe_full, model_kind=mfe_mae_kind)
            n_feat_final = len(final_feature_columns)
            final_full = _final_model_importance_df(final_model, feature_columns=final_feature_columns, top_k=n_feat_final)
            _dump_feature_manifest(joblib_path=final_joblib, feature_columns=final_feature_columns, importance_df=final_full, model_kind=final_dual_model_kind)
            # codex P0-B 修复：long/short 双 final 模型也需要各自的 *_features.csv manifest，
            # 否则部署侧加载这两个 joblib 时无法识别 feature schema。
            if final_model_long is not None and final_long_joblib.exists():
                try:
                    long_full = _final_model_importance_df(final_model_long, feature_columns=final_feature_columns, top_k=n_feat_final)
                except Exception as exc:
                    logger.warning('final_long importance failed: %s', exc)
                    long_full = None
                _dump_feature_manifest(joblib_path=final_long_joblib, feature_columns=final_feature_columns, importance_df=long_full, model_kind=f'{final_dual_model_kind}_long')
            if final_model_short is not None and final_short_joblib.exists():
                try:
                    short_full = _final_model_importance_df(final_model_short, feature_columns=final_feature_columns, top_k=n_feat_final)
                except Exception as exc:
                    logger.warning('final_short importance failed: %s', exc)
                    short_full = None
                _dump_feature_manifest(joblib_path=final_short_joblib, feature_columns=final_feature_columns, importance_df=short_full, model_kind=f'{final_dual_model_kind}_short')
            if trade_cal_joblib.exists():
                _dump_feature_manifest(joblib_path=trade_cal_joblib, feature_columns=['trade_filter_prob'], importance_df=None, model_kind='score_calibration')
            if regime_cal_joblib.exists():
                _dump_feature_manifest(joblib_path=regime_cal_joblib, feature_columns=['regime_confidence'], importance_df=None, model_kind='score_calibration')
            if mfe_mae_cal_joblib.exists():
                _dump_feature_manifest(joblib_path=mfe_mae_cal_joblib, feature_columns=['pred_edge_atr'], importance_df=None, model_kind='score_calibration')
            if final_cal_joblib.exists():
                _dump_feature_manifest(joblib_path=final_cal_joblib, feature_columns=['final_decision_score'], importance_df=None, model_kind='score_calibration')
            for split_name, split_df in (('train', train_df), ('valid', valid_df), ('test', test_df)):
                if split_df.empty:
                    continue
                split_start, split_end = _build_split_span(split_df)
                split_exec_mask = pd.to_numeric(split_df.get('is_executed', 0), errors='coerce').fillna(0).astype(int) == 1
                split_executed_count = int(split_exec_mask.sum())
                split_non_executed_count = int(len(split_df) - split_executed_count)
                split_null_stats = _compute_feature_null_stats(split_df, feature_columns)
                label_target = pd.to_numeric(split_df.get('label_class', 0), errors='coerce')
                label_ic = _compute_feature_ic_stats(split_df, feature_columns=feature_columns, target=label_target)
                if 'regime_label' in split_df.columns:
                    regime_target = pd.Series(pd.Categorical(split_df['regime_label'].astype(str)).codes.astype(float), index=split_df.index)
                else:
                    regime_target = pd.Series([np.nan] * len(split_df), index=split_df.index, dtype=float)
                regime_ic = _compute_feature_ic_stats(split_df, feature_columns=feature_columns, target=regime_target)
                split_exec_df = split_df.loc[split_exec_mask].copy()
                if split_exec_df.empty:
                    return_ic = {'ic_abs_mean': float('nan'), 'ic_abs_median': float('nan'), 'ic_abs_top': float('nan'), 'ic_top_feature': ''}
                else:
                    edge = pd.to_numeric(split_exec_df.get('future_mfe_atr', 0.0), errors='coerce').fillna(0.0) - LABEL_MAE_PENALTY * pd.to_numeric(split_exec_df.get('future_mae_atr', 0.0), errors='coerce').fillna(0.0)
                    return_ic = _compute_feature_ic_stats(split_exec_df, feature_columns=feature_columns, target=edge)
                trade_metrics = evaluate_trade_filter_model(trade_model, split_df, feature_columns=trade_feature_columns, label_column='label_class')
                regime_metrics = evaluate_regime_model(regime_model, split_df, feature_columns=regime_feature_columns, label_column='regime_label')
                nan_mfe_metrics: dict[str, float] = {'direction_auc': float('nan'), 'mfe_mae_mae': float('nan'), 'mae_mae': float('nan'), 'mfe_rmse': float('nan'), 'mae_rmse': float('nan'), 'mfe_r2': float('nan'), 'mae_r2': float('nan')}
                if mfe_mae_model is None:
                    mfe_metrics: dict[str, float] = nan_mfe_metrics
                else:
                    split_exec = split_df.loc[pd.to_numeric(split_df['is_executed'], errors='coerce').fillna(0).astype(int) == 1]
                    if split_exec.empty:
                        mfe_metrics = nan_mfe_metrics
                    else:
                        mfe_metrics = evaluate_mfe_mae_model(mfe_mae_model, split_exec, feature_columns=mfe_feature_columns, mfe_column='future_mfe_atr', mae_column='future_mae_atr', mae_penalty=LABEL_MAE_PENALTY, direction_threshold=LABEL_THRESHOLD)
                final_split_df = {'train': final_train_df, 'valid': final_valid_df, 'test': final_test_df}.get(split_name, final_test_df)
                final_metrics = evaluate_final_decision_model(final_model, final_split_df, feature_columns=final_feature_columns, label_column='label_class')
                metrics_parts.append(pd.DataFrame([{'signal_type': signal_type_key, 'window_id': win.window_id, 'split': split_name, 'model': 'trade_filter', 'model_kind': trade_model.model_kind, 'train_executed_count': train_executed_count, 'train_non_executed_count': train_non_executed_count, 'selected_params': trade_params_json, 'selection_train_auc': _safe_float(trade_selected.get('train_auc')), 'selection_valid_auc': _safe_float(trade_selected.get('valid_auc')), 'selection_auc_gap': _safe_float(trade_selected.get('auc_gap')), 'split_start': split_start, 'split_end': split_end, 'split_sample_count': int(len(split_df)), 'split_executed_count': split_executed_count, 'split_non_executed_count': split_non_executed_count, 'feature_count': int(split_null_stats['feature_count']), 'feature_null_ratio_mean': _safe_float(split_null_stats['feature_null_ratio_mean']), 'feature_null_ratio_max': _safe_float(split_null_stats['feature_null_ratio_max']), 'feature_null_feature_count': int(split_null_stats['feature_null_feature_count']), 'feature_all_null_count': int(split_null_stats['feature_all_null_count']), 'label_ic_abs_mean': _safe_float(label_ic['ic_abs_mean']), 'label_ic_abs_top': _safe_float(label_ic['ic_abs_top']), 'label_ic_top_feature': str(label_ic['ic_top_feature']), 'regime_ic_abs_mean': _safe_float(regime_ic['ic_abs_mean']), 'regime_ic_abs_top': _safe_float(regime_ic['ic_abs_top']), 'regime_ic_top_feature': str(regime_ic['ic_top_feature']), 'return_ic_abs_mean': _safe_float(return_ic['ic_abs_mean']), 'return_ic_abs_top': _safe_float(return_ic['ic_abs_top']), 'return_ic_top_feature': str(return_ic['ic_top_feature']), **trade_metrics}, {'signal_type': signal_type_key, 'window_id': win.window_id, 'split': split_name, 'model': 'regime_classifier', 'model_kind': regime_model.model_kind, 'train_executed_count': train_executed_count, 'train_non_executed_count': train_non_executed_count, 'selected_params': regime_params_json, 'selection_train_auc': _safe_float(regime_selected.get('train_auc')), 'selection_valid_auc': _safe_float(regime_selected.get('valid_auc')), 'selection_auc_gap': _safe_float(regime_selected.get('auc_gap')), 'split_start': split_start, 'split_end': split_end, 'split_sample_count': int(len(split_df)), 'split_executed_count': split_executed_count, 'split_non_executed_count': split_non_executed_count, 'feature_count': int(split_null_stats['feature_count']), 'feature_null_ratio_mean': _safe_float(split_null_stats['feature_null_ratio_mean']), 'feature_null_ratio_max': _safe_float(split_null_stats['feature_null_ratio_max']), 'feature_null_feature_count': int(split_null_stats['feature_null_feature_count']), 'feature_all_null_count': int(split_null_stats['feature_all_null_count']), 'label_ic_abs_mean': _safe_float(label_ic['ic_abs_mean']), 'label_ic_abs_top': _safe_float(label_ic['ic_abs_top']), 'label_ic_top_feature': str(label_ic['ic_top_feature']), 'regime_ic_abs_mean': _safe_float(regime_ic['ic_abs_mean']), 'regime_ic_abs_top': _safe_float(regime_ic['ic_abs_top']), 'regime_ic_top_feature': str(regime_ic['ic_top_feature']), 'return_ic_abs_mean': _safe_float(return_ic['ic_abs_mean']), 'return_ic_abs_top': _safe_float(return_ic['ic_abs_top']), 'return_ic_top_feature': str(return_ic['ic_top_feature']), **regime_metrics}, {'signal_type': signal_type_key, 'window_id': win.window_id, 'split': split_name, 'model': 'mfe_mae', 'model_kind': mfe_mae_kind, 'train_executed_count': train_executed_count, 'train_non_executed_count': train_non_executed_count, 'selected_params': mfe_params_json, 'selection_train_auc': _safe_float(mfe_selected.get('train_auc')), 'selection_valid_auc': _safe_float(mfe_selected.get('valid_auc')), 'selection_auc_gap': _safe_float(mfe_selected.get('auc_gap')), 'split_start': split_start, 'split_end': split_end, 'split_sample_count': int(len(split_df)), 'split_executed_count': split_executed_count, 'split_non_executed_count': split_non_executed_count, 'feature_count': int(split_null_stats['feature_count']), 'feature_null_ratio_mean': _safe_float(split_null_stats['feature_null_ratio_mean']), 'feature_null_ratio_max': _safe_float(split_null_stats['feature_null_ratio_max']), 'feature_null_feature_count': int(split_null_stats['feature_null_feature_count']), 'feature_all_null_count': int(split_null_stats['feature_all_null_count']), 'label_ic_abs_mean': _safe_float(label_ic['ic_abs_mean']), 'label_ic_abs_top': _safe_float(label_ic['ic_abs_top']), 'label_ic_top_feature': str(label_ic['ic_top_feature']), 'regime_ic_abs_mean': _safe_float(regime_ic['ic_abs_mean']), 'regime_ic_abs_top': _safe_float(regime_ic['ic_abs_top']), 'regime_ic_top_feature': str(regime_ic['ic_top_feature']), 'return_ic_abs_mean': _safe_float(return_ic['ic_abs_mean']), 'return_ic_abs_top': _safe_float(return_ic['ic_abs_top']), 'return_ic_top_feature': str(return_ic['ic_top_feature']), **mfe_metrics}, {'signal_type': signal_type_key, 'window_id': win.window_id, 'split': split_name, 'model': 'final_decision_stack', 'model_kind': final_dual_model_kind, 'train_executed_count': train_executed_count, 'train_non_executed_count': train_non_executed_count, 'selected_params': final_params_json, 'selection_train_auc': _safe_float(final_selected.get('train_auc')), 'selection_valid_auc': _safe_float(final_selected.get('valid_auc')), 'selection_auc_gap': _safe_float(final_selected.get('auc_gap')), 'split_start': split_start, 'split_end': split_end, 'split_sample_count': int(len(split_df)), 'split_executed_count': split_executed_count, 'split_non_executed_count': split_non_executed_count, 'feature_count': int(split_null_stats['feature_count']), 'feature_null_ratio_mean': _safe_float(split_null_stats['feature_null_ratio_mean']), 'feature_null_ratio_max': _safe_float(split_null_stats['feature_null_ratio_max']), 'feature_null_feature_count': int(split_null_stats['feature_null_feature_count']), 'feature_all_null_count': int(split_null_stats['feature_all_null_count']), 'label_ic_abs_mean': _safe_float(label_ic['ic_abs_mean']), 'label_ic_abs_top': _safe_float(label_ic['ic_abs_top']), 'label_ic_top_feature': str(label_ic['ic_top_feature']), 'regime_ic_abs_mean': _safe_float(regime_ic['ic_abs_mean']), 'regime_ic_abs_top': _safe_float(regime_ic['ic_abs_top']), 'regime_ic_top_feature': str(regime_ic['ic_top_feature']), 'return_ic_abs_mean': _safe_float(return_ic['ic_abs_mean']), 'return_ic_abs_top': _safe_float(return_ic['ic_abs_top']), 'return_ic_top_feature': str(return_ic['ic_top_feature']), **final_metrics}]))
            if test_df.empty:
                continue
            pred_base = test_df.copy()
            pred_df = build_test_prediction_frame(
                pred_base=pred_base,
                signal_type_key=signal_type_key,
                window_id=win.window_id,
                trade_model=trade_model,
                trade_feature_columns=trade_feature_columns,
                trade_calibrator=trade_calibrator,
                regime_model=regime_model,
                regime_feature_columns=regime_feature_columns,
                mfe_mae_model=mfe_mae_model,
                mfe_feature_columns=mfe_feature_columns,
                final_feature_columns=final_feature_columns,
                final_model=final_model,
                final_model_long=final_model_long,
                final_model_short=final_model_short,
                bull_strength_model=bull_strength_model,
                trend_persistence_model=trend_persistence_model,
                pyramid_model=pyramid_model,
            )
            prediction_parts.append(pred_df)
    if metrics_parts:
        metrics_df = pd.concat(metrics_parts, axis=0, ignore_index=True)
        if 'insufficient_sample' not in metrics_df.columns:
            metrics_df['insufficient_sample'] = 0
        metrics_df['insufficient_sample'] = pd.to_numeric(metrics_df['insufficient_sample'], errors='coerce').fillna(0).astype(int)
    else:
        metrics_df = pd.DataFrame(columns=['signal_type', 'window_id', 'split', 'model', 'model_kind', 'train_executed_count', 'train_non_executed_count', 'selected_params', 'selection_train_auc', 'selection_valid_auc', 'selection_auc_gap', 'split_start', 'split_end', 'split_sample_count', 'split_executed_count', 'split_non_executed_count', 'feature_count', 'feature_null_ratio_mean', 'feature_null_ratio_max', 'feature_null_feature_count', 'feature_all_null_count', 'label_ic_abs_mean', 'label_ic_abs_top', 'label_ic_top_feature', 'regime_ic_abs_mean', 'regime_ic_abs_top', 'regime_ic_top_feature', 'return_ic_abs_mean', 'return_ic_abs_top', 'return_ic_top_feature', 'auc', 'accuracy', 'precision', 'recall', 'f1', 'macro_f1', 'weighted_f1', 'direction_auc', 'mfe_mae_mae', 'mae_mae', 'mfe_rmse', 'mae_rmse', 'mfe_r2', 'mae_r2', 'insufficient_sample'])
    if prediction_parts:
        prediction_df = pd.concat(prediction_parts, axis=0, ignore_index=True)
    else:
        prediction_df = pd.DataFrame()
    if top_feature_parts:
        top_feature_df = pd.concat(top_feature_parts, axis=0, ignore_index=True)
    else:
        top_feature_df = _empty_top_feature_importance_frame()
    metrics_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_metrics.csv'
    prediction_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_predictions.csv'
    top_feature_importance_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_top10_feature_importance.csv'
    decile_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_last_oot_decile_returns.csv'
    oot_monthly_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_oot_monthly_returns.csv'
    oot_summary_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_oot_summary.csv'
    oot_trades_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_oot_trade_details.csv'
    oot_throttle_log_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_throttle_log.csv'
    oot_position_lifetime_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_oot_position_lifetime.csv'
    metrics_df.to_csv(metrics_path, index=False, encoding='utf-8-sig')
    prediction_df.to_csv(prediction_path, index=False, encoding='utf-8-sig')
    top_feature_df.to_csv(top_feature_importance_path, index=False, encoding='utf-8-sig')
    risk_manifest_path: Path | None = None
    score_distribution_path: Path | None = None
    if risk_manifest_parts:
        risk_scored_df = pd.concat(risk_manifest_parts, axis=0, ignore_index=True)
        cta_root = Path(__file__).resolve().parents[2]
        manifests_dir = cta_root / "model" / "manifests"
        risk_manifest_path = write_score_quantile_manifest_from_scored_rows(
            scored_rows=risk_scored_df,
            manifests_dir=manifests_dir,
            run_tag=f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}",
            meta={
                "run_tag": run_date,
                "symbol": sym,
                "interval": interval_norm,
                "trade_side_mode": trade_side_mode,
            },
        )
        score_distribution_path = write_score_distribution_baseline_from_scored_rows(
            scored_rows=risk_scored_df,
            manifests_dir=manifests_dir,
            run_tag=f"{run_date}_{sym}_{interval_norm}_{trade_side_mode}",
            score_column="trade_filter_prob",
        )
        process_steps.append(
            f"- risk_quantile_manifest={risk_manifest_path.name}, scored_rows={len(risk_scored_df)}"
        )
        process_steps.append(
            f"- score_distribution_train={score_distribution_path.name}, scored_rows={len(risk_scored_df)}"
        )
    _write_provenance(out_dir=out_dir, run_tag=run_date, candidate_path=candidate_path, feature_table_path=feature_table_path, prediction_path=prediction_path, metrics_path=metrics_path, top_feature_importance_path=top_feature_importance_path)
    auc_gap_alerts_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_auc_gap_alerts.csv'
    auc_gap_alert_df = _build_valid_test_gap_alerts(metrics_df, max_gap=float(max_valid_test_gap))
    auc_gap_alert_df.to_csv(auc_gap_alerts_path, index=False, encoding='utf-8-sig')
    if not auc_gap_alert_df.empty:
        logger.warning('valid-test AUC gap alerts: %d rows (max_gap=%.4f) -> %s', len(auc_gap_alert_df), float(max_valid_test_gap), auc_gap_alerts_path.name)
    suspect_features_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_suspect_features.csv'
    suspect_features_df = _build_top_feature_concentration_alerts(top_feature_df, top1_thresh=float(top_feature_importance_alert_pct))
    suspect_features_df.to_csv(suspect_features_path, index=False, encoding='utf-8-sig')
    if not suspect_features_df.empty:
        logger.warning('top-1 feature concentration alerts: %d rows (top1_pct_in_top10>=%.2f) -> %s', len(suspect_features_df), float(top_feature_importance_alert_pct), suspect_features_path.name)
    unaudited_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_unaudited_features.csv'
    try:
        all_feats: list[str] = []
        seen_feat: set[str] = set()
        for col in feature_df.columns:
            n = str(col)
            if (n.startswith('feature_') or n.startswith('generic_')) and n.lower() not in seen_feat:
                seen_feat.add(n.lower())
                all_feats.append(n)
        unaudited = _list_unaudited_features(all_feats)
        pd.DataFrame({'feature': unaudited}).to_csv(unaudited_path, index=False, encoding='utf-8-sig')
        if unaudited:
            logger.warning('unaudited features (not in causality_manifest.csv): %d -> %s', len(unaudited), unaudited_path.name)
        if int(max_unaudited_features) >= 0 and len(unaudited) > int(max_unaudited_features):
            raise ValueError(f'unaudited feature count exceeded threshold: {len(unaudited)} > max_unaudited_features={int(max_unaudited_features)}; see {unaudited_path}')
    except Exception as exc:
        if isinstance(exc, ValueError):
            raise
        logger.warning('write unaudited_features failed: %s', exc)
    process_steps.append('4) 使用 OOT(test) 进行最终评估并生成报告')
    decile_df = _build_last_oot_decile_table(prediction_df, bins=10)
    decile_df.to_csv(decile_path, index=False, encoding='utf-8-sig')
    oot_extra: dict[str, pd.DataFrame] = {}
    oot_monthly_df, oot_summary_df, oot_trade_df = _evaluate_oot_real_execution(prediction_df, cfg=oot_eval_config, extra_outputs=oot_extra)
    oot_monthly_df.to_csv(oot_monthly_path, index=False, encoding='utf-8-sig')
    oot_summary_df.to_csv(oot_summary_path, index=False, encoding='utf-8-sig')
    oot_trade_df.to_csv(oot_trades_path, index=False, encoding='utf-8-sig')
    oot_extra.get('throttle_log', pd.DataFrame()).to_csv(oot_throttle_log_path, index=False, encoding='utf-8-sig')
    oot_extra.get('position_lifetime', pd.DataFrame()).to_csv(oot_position_lifetime_path, index=False, encoding='utf-8-sig')
    html_report_path = write_pipeline_oot_html_report(oot_trade_df=oot_trade_df, out_dir=out_dir, title=f'CTA Model OOT / {sym}.{ex} / {interval_norm} / {trade_side_mode}', periods_per_year=int(max(1.0, float(oot_eval_config.annualization_factor))), initial_capital=float(oot_eval_config.initial_capital))
    if html_report_path is None:
        html_report_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_oot_report.html'
        html_report_path.write_text("<!DOCTYPE html><html><head><meta charset='utf-8'><title>OOT Report</title></head><body><h1>OOT Report</h1><p>No valid OOT executed trades.</p></body></html>", encoding='utf-8')
    process_steps.append(f'- oot_prediction_rows={len(prediction_df)}, last_oot_decile_rows={len(decile_df)}, metrics_rows={len(metrics_df)}')
    if not oot_summary_df.empty:
        row = oot_summary_df.iloc[0]
        sel_val = _safe_float(row.get('selected_rows', 0))
        sel_int = int(sel_val) if np.isfinite(sel_val) else 0
        process_steps.append(f"- oot_real_exec selected={sel_int}, gross_pnl={_safe_float(row.get('gross_pnl')):.6f}, total_return_pct={_safe_float(row.get('total_return_pct')):.6f}, monthly_sharpe={_safe_float(row.get('monthly_sharpe')):.6f}")
    neg_count = int((pd.to_numeric(candidate_df['is_executed'], errors='coerce').fillna(0).astype(int) == 0).sum())
    total_count = len(candidate_df)
    ratio = neg_count / total_count if total_count else 0.0
    if metrics_df.empty:
        metrics_block = 'no metrics'
    else:
        try:
            metrics_block = metrics_df.to_markdown(index=False, floatfmt='.6f')
        except (ImportError, ValueError) as exc:
            logger.warning('metrics_df.to_markdown failed (%s), fallback to to_string', exc)
            metrics_block = '```\n' + metrics_df.to_string(index=False) + '\n```'
    if decile_df.empty:
        decile_block = 'no last-oot decile rows'
    else:
        try:
            decile_block = decile_df.to_markdown(index=False, floatfmt='.6f')
        except (ImportError, ValueError) as exc:
            logger.warning('decile_df.to_markdown failed (%s), fallback to to_string', exc)
            decile_block = '```\n' + decile_df.to_string(index=False) + '\n```'
    if oot_monthly_df.empty:
        oot_monthly_block = 'no oot monthly rows'
    else:
        try:
            oot_monthly_block = oot_monthly_df.to_markdown(index=False, floatfmt='.6f')
        except (ImportError, ValueError) as exc:
            logger.warning('oot_monthly_df.to_markdown failed (%s), fallback to to_string', exc)
            oot_monthly_block = '```\n' + oot_monthly_df.to_string(index=False) + '\n```'
    if oot_summary_df.empty:
        oot_summary_block = 'no oot summary rows'
    else:
        try:
            oot_summary_block = oot_summary_df.to_markdown(index=False, floatfmt='.6f')
        except (ImportError, ValueError) as exc:
            logger.warning('oot_summary_df.to_markdown failed (%s), fallback to to_string', exc)
            oot_summary_block = '```\n' + oot_summary_df.to_string(index=False) + '\n```'
    if metrics_df.empty:
        split_diag_block = 'no split diagnostics'
    else:
        split_diag_cols = ['signal_type', 'window_id', 'split', 'split_start', 'split_end', 'split_sample_count', 'split_executed_count', 'split_non_executed_count', 'feature_count', 'feature_null_ratio_mean', 'feature_null_ratio_max', 'feature_null_feature_count', 'feature_all_null_count', 'label_ic_abs_mean', 'regime_ic_abs_mean', 'return_ic_abs_mean']
        split_diag_df = metrics_df.loc[:, [c for c in split_diag_cols if c in metrics_df.columns]].drop_duplicates(subset=['signal_type', 'window_id', 'split'], keep='first')
        split_diag_df = split_diag_df.sort_values(['signal_type', 'window_id', 'split']).reset_index(drop=True)
        try:
            split_diag_block = split_diag_df.to_markdown(index=False, floatfmt='.6f')
        except (ImportError, ValueError) as exc:
            logger.warning('split_diag_df.to_markdown failed (%s), fallback to to_string', exc)
            split_diag_block = '```\n' + split_diag_df.to_string(index=False) + '\n```'
    process_steps_block = '\n'.join((f'- {line}' for line in process_steps))
    report_path = out_dir / f'{run_date}_{sym}_{interval_norm}_{trade_side_mode}_model_report.md'
    report_lines = ['# CTA Model Pipeline Report', '', f'- symbol: `{sym}.{ex}`', f'- interval: `{interval_norm}`', f'- side mode: `{trade_side_mode}`', f'- date range: `{start_date}` -> `{end_date}`', f'- by_signal_type: `{by_signal_type}`', f'- max_walk_forward_windows: `{max_walk_forward_windows}`', f'- window_mode: `{window_mode}`', f'- rolling_train_years: `{rolling_train_years}`', f'- rolling_valid_years: `{rolling_valid_years}`', f'- rolling_test_years: `{rolling_test_years}`', f'- rolling_step_years: `{rolling_step_years}`', f'- max_auc_gap: `{max_auc_gap}`', f'- candidate_count: `{total_count}`', f'- negative_candidate_count: `{neg_count}`', f'- negative_ratio: `{ratio:.4f}`', f'- signal_frames_count: `{len(signal_frames)}`', '', '## Process Steps', process_steps_block, '', '## Split Diagnostics', split_diag_block, '', '## Metrics', metrics_block, '', '## Last OOT Decile Returns', decile_block, '', '## OOT Real Execution Evaluation', f'- config: `{oot_eval_config}`', '', '### OOT Monthly Returns', oot_monthly_block, '', '### OOT Summary', oot_summary_block, '', f'- candidates_csv: `{candidate_path}`', f'- feature_table_csv: `{feature_table_path}`', f'- predictions_csv: `{prediction_path}`', f'- metrics_csv: `{metrics_path}`', f'- top10_feature_importance_csv: `{top_feature_importance_path}`', f'- last_oot_decile_csv: `{decile_path}`', f'- oot_monthly_returns_csv: `{oot_monthly_path}`', f'- oot_summary_csv: `{oot_summary_path}`', f'- oot_trade_details_csv: `{oot_trades_path}`', f'- oot_throttle_log_csv: `{oot_throttle_log_path}`', f'- oot_position_lifetime_csv: `{oot_position_lifetime_path}`', f'- html_report: `{html_report_path}`']
    if pool_meta_path is not None:
        report_lines.append(f'- pool_members_csv: `{pool_meta_path}`')
    report_path.write_text('\n'.join(report_lines) + '\n', encoding='utf-8')
    logger.info('model pipeline finished: %s', out_dir)
    return ModelPipelineResult(output_dir=out_dir, candidate_path=candidate_path, feature_table_path=feature_table_path, prediction_path=prediction_path, metrics_path=metrics_path, oot_monthly_path=oot_monthly_path, oot_summary_path=oot_summary_path, oot_trades_path=oot_trades_path, html_report_path=html_report_path, top_feature_importance_path=top_feature_importance_path, report_path=report_path)
