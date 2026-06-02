"""Output and provenance helpers for pipeline_orchestrator."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import (
    FEATURES_DOC_PATH,
    LABEL_MAE_PENALTY,
    Path,
    _evaluate_oot_real_execution,
    _safe_name,
    hashlib,
    json,
    logger,
    np,
    pd,
    platform,
    shutil,
    subprocess,
    sys,
)

def _build_last_oot_decile_table(prediction_df: pd.DataFrame, bins: int=10) -> pd.DataFrame:
    """Build decile return table on the final OOT set.

    OOT 定义：
    1. 优先使用 `pred_split == "test"`；
    2. 若存在 `window_id`，只取最大 window_id（最后一个 walk-forward test 窗口）。

    收益评估口径：
    - 仅对 `is_executed == 1` 的样本计算十档收益（按用户要求）。
    """
    out_cols = ['window_id', 'pred_split', 'decile', 'sample_count', 'score_min', 'score_max', 'score_mean', 'avg_return_atr', 'median_return_atr', 'total_return_atr', 'win_rate', 'executed_rate', 'executed_avg_return_atr']
    if prediction_df.empty or 'trade_filter_prob' not in prediction_df.columns:
        return pd.DataFrame(columns=out_cols)
    oot = prediction_df.copy()
    if 'pred_split' in oot.columns:
        split = oot['pred_split'].astype(str).str.lower()
        test_mask = split == 'test'
        if test_mask.any():
            oot = oot.loc[test_mask].copy()
    if oot.empty:
        return pd.DataFrame(columns=out_cols)
    if 'window_id' in oot.columns:
        w = pd.to_numeric(oot['window_id'], errors='coerce')
        if w.notna().any():
            last_wid = int(w.max())
            oot = oot.loc[w == last_wid].copy()
        else:
            last_wid = -1
    else:
        last_wid = -1
    if oot.empty:
        return pd.DataFrame(columns=out_cols)
    score = pd.to_numeric(oot['trade_filter_prob'], errors='coerce')
    oot = oot.loc[score.notna()].copy()
    if oot.empty:
        return pd.DataFrame(columns=out_cols)
    oot['trade_filter_prob'] = score.loc[oot.index]
    exec_mask = pd.to_numeric(oot.get('is_executed', pd.Series(0, index=oot.index)), errors='coerce').fillna(0).astype(int) == 1
    oot = oot.loc[exec_mask].copy()
    if oot.empty:
        return pd.DataFrame(columns=out_cols)
    mfe = pd.to_numeric(oot.get('future_mfe_atr', pd.Series(0.0, index=oot.index)), errors='coerce').fillna(0.0)
    mae = pd.to_numeric(oot.get('future_mae_atr', pd.Series(0.0, index=oot.index)), errors='coerce').fillna(0.0)
    oot['future_return_atr'] = mfe - LABEL_MAE_PENALTY * mae
    oot['is_executed'] = 1
    n = len(oot)
    n_bins = min(max(1, int(bins)), n)
    rank = oot['trade_filter_prob'].rank(method='first')
    oot['decile'] = pd.qcut(rank, q=n_bins, labels=False, duplicates='drop').astype(int) + 1
    parts: list[dict[str, Any]] = []
    for decile, g in oot.groupby('decile', sort=True):
        ret = pd.to_numeric(g['future_return_atr'], errors='coerce').fillna(0.0)
        parts.append({'window_id': last_wid if last_wid >= 0 else np.nan, 'pred_split': 'test', 'decile': int(decile), 'sample_count': int(len(g)), 'score_min': float(g['trade_filter_prob'].min()), 'score_max': float(g['trade_filter_prob'].max()), 'score_mean': float(g['trade_filter_prob'].mean()), 'avg_return_atr': float(ret.mean()), 'median_return_atr': float(ret.median()), 'total_return_atr': float(ret.sum()), 'win_rate': float((ret > 0.0).mean()), 'executed_rate': 1.0, 'executed_avg_return_atr': float(ret.mean())})
    out = pd.DataFrame(parts).sort_values('decile').reset_index(drop=True)
    return out[out_cols]

def _sha256_of_file(path: Path) -> str:
    if not Path(path).exists():
        return ''
    h = hashlib.sha256()
    with Path(path).open('rb') as f:
        while True:
            buf = f.read(1024 * 1024)
            if not buf:
                break
            h.update(buf)
    return f'sha256:{h.hexdigest()}'

def _sha256_of_text(text: str) -> str:
    return f"sha256:{hashlib.sha256(text.encode('utf-8')).hexdigest()}"

def _git_commit_short() -> str:
    try:
        out = subprocess.run(['git', 'rev-parse', '--short', 'HEAD'], check=False, capture_output=True, text=True)
        if out.returncode == 0:
            return out.stdout.strip()
    except Exception:
        return ''
    return ''

def _write_provenance(*, out_dir: Path, run_tag: str, candidate_path: Path, feature_table_path: Path, prediction_path: Path, metrics_path: Path, top_feature_importance_path: Path) -> Path:
    payload = {'run_tag': str(run_tag), 'generated_at': pd.Timestamp.now().isoformat(), 'python_version': sys.version.replace('\n', ' '), 'platform': platform.platform(), 'git_commit': _git_commit_short(), 'candidate_path': str(candidate_path), 'feature_table_path': str(feature_table_path), 'prediction_path': str(prediction_path), 'metrics_path': str(metrics_path), 'top_feature_importance_path': str(top_feature_importance_path), 'data_snapshot_hash': _sha256_of_file(candidate_path), 'feature_manifest_hash': _sha256_of_file(feature_table_path), 'prediction_hash': _sha256_of_file(prediction_path), 'metrics_hash': _sha256_of_file(metrics_path)}
    try:
        feat_doc_text = FEATURES_DOC_PATH.read_text(encoding='utf-8-sig')
    except Exception:
        feat_doc_text = ''
    payload['features_doc_hash'] = _sha256_of_text(feat_doc_text)
    path = Path(out_dir) / 'provenance.json'
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding='utf-8')
    return path

def _write_group_pool_runtime_bundle(*, root: Path, run_date_tag: str, group_by: str, trade_side_mode: str, run_records: Sequence[dict[str, Any]]) -> Path | None:
    """Write a top-level runtime bundle for group-pool OOT artifacts.

    目录目标（use_portfolio_logic_runtime=True 时）：
    1) 上层聚合：所有 symbol group 的 OOT 逐笔明细（含每笔交易后仓位）。
    2) 组内细节：每个 group/interval 的文件索引与源路径，便于后续 drill-down。
    """
    if not run_records:
        return None
    root = Path(root).resolve()
    root.mkdir(parents=True, exist_ok=True)
    group_key = _safe_name(group_by)
    side_key = _safe_name(trade_side_mode)
    bundle_dir = root / f'{run_date_tag}_GROUP_POOL_{group_key.upper()}_{side_key}_portfolio_logic_runtime'
    bundle_dir.mkdir(parents=True, exist_ok=True)
    detail_root = bundle_dir / 'symbol_group_details'
    detail_root.mkdir(parents=True, exist_ok=True)

    def _safe_copy(src: Path, dst_dir: Path) -> str:
        if not src.exists() or not src.is_file():
            return ''
        dst = dst_dir / src.name
        try:
            shutil.copy2(src, dst)
            return str(dst.resolve())
        except Exception as exc:
            logger.warning('copy detail artifact failed: %s -> %s (%s)', src, dst, exc)
            return ''
    trade_frames: list[pd.DataFrame] = []
    manifest_rows: list[dict[str, Any]] = []
    for rec in run_records:
        res = rec.get('result')
        if res is None:
            continue
        group_name = str(rec.get('group_name', ''))
        pool_name = str(rec.get('pool_name', ''))
        interval = str(rec.get('interval', ''))
        members = list(rec.get('members') or [])
        output_dir = Path(getattr(res, 'output_dir', ''))
        oot_trades_path = Path(getattr(res, 'oot_trades_path', ''))
        detail_dir = detail_root / f'{_safe_name(pool_name)}_{_safe_name(interval)}'
        detail_dir.mkdir(parents=True, exist_ok=True)
        copied_paths = {'candidate_path': _safe_copy(Path(getattr(res, 'candidate_path', '')), detail_dir), 'feature_table_path': _safe_copy(Path(getattr(res, 'feature_table_path', '')), detail_dir), 'prediction_path': _safe_copy(Path(getattr(res, 'prediction_path', '')), detail_dir), 'metrics_path': _safe_copy(Path(getattr(res, 'metrics_path', '')), detail_dir), 'oot_monthly_path': _safe_copy(Path(getattr(res, 'oot_monthly_path', '')), detail_dir), 'oot_summary_path': _safe_copy(Path(getattr(res, 'oot_summary_path', '')), detail_dir), 'oot_trades_path': _safe_copy(oot_trades_path, detail_dir), 'html_report_path': _safe_copy(Path(getattr(res, 'html_report_path', '')), detail_dir), 'top_feature_importance_path': _safe_copy(Path(getattr(res, 'top_feature_importance_path', '')), detail_dir), 'report_path': _safe_copy(Path(getattr(res, 'report_path', '')), detail_dir)}
        model_dir_path_file = detail_dir / 'model_dir_path.txt'
        model_dir_path_file.write_text(str(output_dir.resolve()) + '\n', encoding='utf-8')
        detail_manifest_path = detail_dir / 'group_detail_manifest.json'
        detail_manifest = {'run_date_tag': str(run_date_tag), 'group_by': str(group_by), 'trade_side_mode': str(trade_side_mode), 'group_name': group_name, 'pool_name': pool_name, 'interval': interval, 'members': [{'symbol': str(s).upper(), 'exchange': str(e or '')} for s, e in members], 'source_output_dir': str(output_dir.resolve()) if str(output_dir) else '', 'source_paths': {'candidate_path': str(getattr(res, 'candidate_path', '')), 'feature_table_path': str(getattr(res, 'feature_table_path', '')), 'prediction_path': str(getattr(res, 'prediction_path', '')), 'metrics_path': str(getattr(res, 'metrics_path', '')), 'oot_monthly_path': str(getattr(res, 'oot_monthly_path', '')), 'oot_summary_path': str(getattr(res, 'oot_summary_path', '')), 'oot_trades_path': str(getattr(res, 'oot_trades_path', '')), 'html_report_path': str(getattr(res, 'html_report_path', '')), 'top_feature_importance_path': str(getattr(res, 'top_feature_importance_path', '')), 'report_path': str(getattr(res, 'report_path', ''))}, 'copied_paths': copied_paths, 'model_dir_path_file': str(model_dir_path_file.resolve())}
        detail_manifest_path.write_text(json.dumps(detail_manifest, ensure_ascii=False, indent=2), encoding='utf-8')
        manifest_rows.append({'run_date_tag': str(run_date_tag), 'group_by': str(group_by), 'trade_side_mode': str(trade_side_mode), 'group_name': group_name, 'pool_name': pool_name, 'interval': interval, 'member_count': int(len(members)), 'model_dir': str(output_dir.resolve()) if str(output_dir) else '', 'oot_trades_path': str(oot_trades_path.resolve()) if str(oot_trades_path) else '', 'group_detail_manifest': str(detail_manifest_path.resolve())})
        if oot_trades_path.exists() and oot_trades_path.is_file():
            try:
                trades_df = pd.read_csv(oot_trades_path, encoding='utf-8-sig')
            except Exception as exc:
                logger.warning('read oot trades failed: %s (%s)', oot_trades_path, exc)
            else:
                if 'position_notional_after_trade' not in trades_df.columns:
                    if 'open_notional_after_exit' in trades_df.columns:
                        trades_df['position_notional_after_trade'] = pd.to_numeric(trades_df['open_notional_after_exit'], errors='coerce')
                    else:
                        trades_df['position_notional_after_trade'] = np.nan
                trades_df['group_name'] = group_name
                trades_df['pool_name'] = pool_name
                trades_df['group_interval'] = interval
                trades_df['model_dir'] = str(output_dir.resolve()) if str(output_dir) else ''
                trade_frames.append(trades_df)
    agg_trade_path = bundle_dir / f'{run_date_tag}_group_pool_{group_key}_{side_key}_all_symbol_group_oot_trade_details.csv'
    manifest_path = bundle_dir / f'{run_date_tag}_group_pool_{group_key}_{side_key}_symbol_group_run_manifest.csv'
    if trade_frames:
        agg_df = pd.concat(trade_frames, axis=0, ignore_index=True)
    else:
        agg_df = pd.DataFrame(columns=['group_name', 'pool_name', 'group_interval', 'model_dir', 'position_notional_after_trade'])
    agg_df.to_csv(agg_trade_path, index=False, encoding='utf-8-sig')
    pd.DataFrame(manifest_rows).to_csv(manifest_path, index=False, encoding='utf-8-sig')
    try:
        from cta.model.reporting.group_pool_aggregate import AggregateConfig, write_aggregate_reports
        from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG
        write_aggregate_reports(bundle_dir, agg_df, run_date_tag=str(run_date_tag), group_key=group_key, side_key=side_key, cfg=AggregateConfig.from_oot_config(DEFAULT_OOT_EVAL_CONFIG))
    except Exception as exc:
        logger.warning('group-pool aggregate report failed: %s', exc)
    try:
        from cta.model.reporting.oot_report_writer import write_oot_evaluation_report
        from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG
        write_oot_evaluation_report(bundle_dir=bundle_dir, run_records=run_records, run_tag=f'{group_key}_{side_key}', cfg=AggregateConfig.from_oot_config(DEFAULT_OOT_EVAL_CONFIG))
    except Exception as exc:
        logger.warning('write_oot_evaluation_report failed: %s', exc)
    return bundle_dir

def _infer_oot_extra_output_paths(result: ModelPipelineResult) -> tuple[Path, Path]:
    """Infer throttle/position-lifetime csv paths from oot_monthly path naming."""
    monthly_path = Path(result.oot_monthly_path)
    name = monthly_path.name
    suffix = '_oot_monthly_returns.csv'
    if name.endswith(suffix):
        prefix = name[:-len(suffix)]
    else:
        prefix = monthly_path.stem
    throttle_path = monthly_path.parent / f'{prefix}_throttle_log.csv'
    position_lifetime_path = monthly_path.parent / f'{prefix}_oot_position_lifetime.csv'
    return (throttle_path, position_lifetime_path)

def _should_recompute_with_shared_htf_reference(cfg: OotEvaluationConfig) -> bool:
    if not bool(getattr(cfg, 'use_portfolio_logic_runtime', False)):
        return False
    pl = getattr(cfg, 'portfolio_logic', None)
    return bool(pl is not None and bool(getattr(pl, 'enable_htf_gate', False)))

def _recompute_oot_with_shared_htf_reference(results: Sequence[ModelPipelineResult], oot_eval_config: OotEvaluationConfig) -> int:
    """Re-evaluate OOT per result using a shared cross-interval HTF reference table.

    Returns number of result folders rewritten.
    """
    if not _should_recompute_with_shared_htf_reference(oot_eval_config):
        return 0
    if len(results) < 2:
        return 0
    loaded: list[tuple[ModelPipelineResult, pd.DataFrame]] = []
    for res in results:
        pred_path = Path(res.prediction_path)
        if not pred_path.exists():
            logger.debug('skip shared-htf recompute: missing prediction_path=%s', pred_path)
            continue
        try:
            pred_df = pd.read_csv(pred_path, encoding='utf-8-sig')
        except Exception:
            logger.exception('failed to read prediction csv for shared-htf recompute: %s', pred_path)
            continue
        if pred_df.empty:
            continue
        loaded.append((res, pred_df))
    if len(loaded) < 2:
        return 0
    shared_ref_df = pd.concat([df for _, df in loaded], axis=0, ignore_index=True)
    rewritten = 0
    for res, pred_df in loaded:
        try:
            oot_extra: dict[str, pd.DataFrame] = {}
            oot_monthly_df, oot_summary_df, oot_trade_df = _evaluate_oot_real_execution(pred_df, cfg=oot_eval_config, extra_outputs=oot_extra, htf_reference_df=shared_ref_df)
            oot_monthly_df.to_csv(res.oot_monthly_path, index=False, encoding='utf-8-sig')
            oot_summary_df.to_csv(res.oot_summary_path, index=False, encoding='utf-8-sig')
            oot_trade_df.to_csv(res.oot_trades_path, index=False, encoding='utf-8-sig')
            throttle_path, pos_lifetime_path = _infer_oot_extra_output_paths(res)
            oot_extra.get('throttle_log', pd.DataFrame()).to_csv(throttle_path, index=False, encoding='utf-8-sig')
            oot_extra.get('position_lifetime', pd.DataFrame()).to_csv(pos_lifetime_path, index=False, encoding='utf-8-sig')
            rewritten += 1
        except Exception:
            logger.exception('shared-htf OOT recompute failed for output_dir=%s', res.output_dir)
    if rewritten > 0:
        logger.info('shared-htf OOT recompute finished: rewritten=%d/%d', rewritten, len(loaded))
    return int(rewritten)
