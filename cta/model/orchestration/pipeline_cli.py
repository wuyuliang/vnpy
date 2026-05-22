"""CLI helpers for pipeline_orchestrator."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import (
    DEFAULT_OOT_EVAL_CONFIG,
    DEFAULT_REPORT_ROOT,
    ModelPipelineResult,
    OotEvaluationConfig,
    Path,
    Sequence,
    SYMBOLS_RANKING_PATH,
    _safe_name,
    argparse,
    dc_replace,
    json,
    logger,
    logging,
    pd,
)
from cta.model.orchestration.pipeline_multi import _normalize_intervals, run_model_pipeline_multi
from cta.model.reporting.pipeline_outputs import (
    _recompute_oot_with_shared_htf_reference,
    _write_group_pool_runtime_bundle,
)
from cta.model.training.pipeline_param_grids import _validate_stop_loss_pct_consistency
from cta.model.orchestration.pipeline_run import run_model_pipeline
from cta.model.dataset.pipeline_symbol_ranking import (
    _load_symbol_groups_from_ranking,
    _load_top_n_symbols_from_ranking,
    _resolve_run_exchange,
)


def _build_effective_oot_config(
    *,
    use_portfolio_logic_runtime: bool,
) -> OotEvaluationConfig:
    """Build CLI OOT config without mutating repo defaults."""
    if not bool(use_portfolio_logic_runtime):
        return DEFAULT_OOT_EVAL_CONFIG
    return dc_replace(DEFAULT_OOT_EVAL_CONFIG, use_portfolio_logic_runtime=True)


def _parse_args(argv: Sequence[str] | None=None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description='Run CTA model pipeline')
    parser.add_argument('--symbol', default='RB0')
    parser.add_argument('--exchange', default='SHFE')
    parser.add_argument('--top-n-symbols', type=int, default=0, help='if > 0, ignore --symbol and load top-N symbols from --symbols-ranking-path ordered by research_rank')
    parser.add_argument('--symbols-ranking-path', default=str(SYMBOLS_RANKING_PATH), help='csv path of symbol research ranking (default cta/feature/symbols_research_ranking.csv)')
    parser.add_argument('--interval', nargs='+', default=['60min'], help='one or more intervals (day/60min/30min/15min/5min/min). Accepts space-separated and comma-separated tokens; duplicates are deduped.')
    parser.add_argument('--start', default='2000-01-01')
    parser.add_argument('--end', default='2019-12-31')
    parser.add_argument('--trade-side-mode', default='both')
    parser.add_argument('--train-end', default='2018-12-31')
    parser.add_argument('--valid-end', default='2019-06-30')
    parser.add_argument('--output-root', default=None)
    parser.add_argument('--synthetic-periods', type=int, default=400)
    parser.add_argument('--seed', type=int, default=2026, help='global random seed for reproducibility')
    parser.add_argument('--max-walk-forward-windows', type=int, default=3)
    parser.add_argument('--max-auc-gap', type=float, default=0.03, help='max allowed abs(train_auc-valid_auc) during parameter selection (default 0.03)')
    parser.add_argument('--max-valid-test-gap', type=float, default=0.1, help='valid 与 test AUC 差异告警阈值（默认 0.10）。test 不参与选参，仅在事后把每个 (signal_type, window_id, model) 的 valid/test gap > 阈值的窗口写到 *_auc_gap_alerts.csv 便于人工审计过拟合 / 时间漂移。')
    parser.add_argument('--top-feature-alert-pct', type=float, default=0.5, help='单一特征 importance 占比超过该值时写到 *_suspect_features.csv（默认 0.50）。经验上 top-1 特征占比过高通常对应命名未触发现有 leakage filter 的穿越特征。')
    parser.add_argument('--min-train-samples', type=int, default=50)
    parser.add_argument('--min-valid-samples', type=int, default=20)
    parser.add_argument('--min-test-samples', type=int, default=20)
    parser.add_argument('--max-unaudited-features', type=int, default=500, help='max allowed unaudited feature count from causality_manifest check. Set -1 to disable hard-fail.')
    parser.add_argument('--window-mode', default='expanding', choices=('expanding', 'sliding', 'rolling'), help='walk-forward window mode (default expanding train; sliding/rolling supported)')
    parser.add_argument('--rolling-train-years', type=int, default=3, help='rolling mode train window years')
    parser.add_argument('--rolling-valid-years', type=int, default=1, help='rolling mode valid window years')
    parser.add_argument('--rolling-test-years', type=int, default=1, help='rolling mode test window years')
    parser.add_argument('--rolling-step-years', type=int, default=1, help='rolling mode step years')
    parser.add_argument('--by-signal-type', dest='by_signal_type', action='store_true')
    parser.add_argument('--no-by-signal-type', dest='by_signal_type', action='store_false')
    parser.set_defaults(by_signal_type=True)
    parser.add_argument('--generic-mode', default='auto', choices=('auto', 'whitelist'), help="generic 特征拼接策略：'auto' (默认) 自动取磁盘 parquet 上所有数值列；'whitelist' 仅 18 列 DEFAULT_GENERIC_COLUMNS。auto 模式下模型可用上 cta/data/feature 里预先算好的全部 ~400 个通用特征。")
    parser.add_argument('--pool', action='store_true', help='把 --top-n-symbols / --symbol 给定的多个品种**池化**成一个共享样本，训出一个跨品种的模型（输出目录 ..._POOL_..._model_pipeline）。适合 day 等单品种样本不足的 interval。')
    parser.add_argument('--group-pool', action='store_true', help='把 ranking 里的 symbols 先按 group 切分（例如 tier/cluster），然后每个 group 各自做一套 pool 训练与预测（组 × interval）。')
    parser.add_argument('--group-by', default='tier', help="group-pool 模式分组键：'cluster' 使用内置 symbol cluster；其它值按 ranking csv 的同名列分组（默认 tier）。")
    parser.add_argument('--group-min-size', type=int, default=2, help='group-pool 模式保留的最小组大小（默认 2）。')
    parser.add_argument('--only-clusters', nargs='+', default=None, help="group-pool 模式下仅训练指定 cluster（基础名，不带 'cluster_' 前缀）。例: --only-clusters index → 仅训练 cluster_index（IF0/IH0/IC0/IM0）；    --only-clusters index bond → 训练股指+国债两个 cluster。未指定（默认 None）= 训练 ranking 中全部 cluster。")
    parser.add_argument('--use-portfolio-logic-runtime', action='store_true', default=False, help='OOT 评估按线上 portfolio_logic 真实逻辑走（HTF gate + ranker + trailing + pyramid + score_calibration + risk_throttle）。默认 False = 用旧 FCFS 路径。等价于 OotEvaluationConfig(use_portfolio_logic_runtime=True)。依赖 cluster_registry.json 与 *_calibration.joblib 已生成。')
    parser.add_argument('--include-disabled-symbols', action='store_true', help='不应用 symbol_disable_manifest 过滤。默认会过滤掉被标记禁用的品种；若要覆盖 70+ 全量品种可打开该开关。')
    parser.add_argument('--min-used-symbols', type=int, default=2, help='POOL 模式最少实际参与训练的品种数（默认 2）。若低于该阈值则报错，避免把单品种误当池化模型。')
    return parser.parse_args(argv)

def main(argv: Sequence[str] | None=None) -> None:
    logging.basicConfig(level=logging.INFO, format='%(asctime)s [%(levelname)s] %(name)s: %(message)s')
    _validate_stop_loss_pct_consistency()
    from cta.utils.random_seed import seed_all_from_env
    used_seed = seed_all_from_env('CTA_GLOBAL_SEED')
    if used_seed is not None:
        logger.info('model_pipeline: seeded global RNG from CTA_GLOBAL_SEED=%s', used_seed)
    args = _parse_args(argv)
    intervals = _normalize_intervals(args.interval)
    output_root = Path(args.output_root).resolve() if args.output_root else None
    top_n = int(getattr(args, 'top_n_symbols', 0))
    respect_disabled_manifest = not bool(getattr(args, 'include_disabled_symbols', False))
    effective_oot_cfg = _build_effective_oot_config(
        use_portfolio_logic_runtime=bool(getattr(args, 'use_portfolio_logic_runtime', False)),
    )
    if bool(effective_oot_cfg.use_portfolio_logic_runtime):
        logger.info('OOT eval will use portfolio_logic runtime (htf=%s ranker=%s trail=%s pyramid=%s calib=%s throttle=%s)', effective_oot_cfg.portfolio_logic.enable_htf_gate, effective_oot_cfg.portfolio_logic.enable_ranker, effective_oot_cfg.portfolio_logic.enable_trailing, effective_oot_cfg.portfolio_logic.enable_pyramid, effective_oot_cfg.portfolio_logic.enable_score_calibration, effective_oot_cfg.portfolio_logic.enable_risk_throttle)
    if bool(getattr(args, 'group_pool', False)):
        run_date_tag = pd.Timestamp.now().strftime('%Y%m%d')
        group_specs = _load_symbol_groups_from_ranking(Path(args.symbols_ranking_path), top_n=top_n, group_by=str(args.group_by), min_symbols_per_group=int(args.group_min_size), respect_disabled_manifest=respect_disabled_manifest)
        only_clusters = getattr(args, 'only_clusters', None)
        if only_clusters:
            group_by_key = _safe_name(str(args.group_by) or 'tier')
            wanted = {f'{group_by_key}_{_safe_name(str(c))}' for c in only_clusters}
            before = [g for g, _ in group_specs]
            group_specs = [(g, m) for g, m in group_specs if g in wanted]
            if not group_specs:
                raise ValueError(f'--only-clusters {only_clusters} 过滤后无可训练组；原可用组={before}; 期望前缀={group_by_key}_*')
        logger.info('GROUP-POOL mode enabled: group_by=%s groups=%s intervals=%s', str(args.group_by), [g for g, _ in group_specs], list(intervals))
        registry_records: list[dict[str, Any]] = []
        runtime_bundle_records: list[dict[str, Any]] = []
        for interval in intervals:
            interval_records: list[dict[str, Any]] = []
            for group_name, members in group_specs:
                pool_name = f'GRP_{str(group_name).strip().upper()}'
                try:
                    res = run_model_pipeline(symbol=pool_name, exchange=None, interval=interval, start_date=args.start, end_date=args.end, trade_side_mode=args.trade_side_mode, train_end=args.train_end, valid_end=args.valid_end, output_root=output_root, synthetic_periods=args.synthetic_periods, by_signal_type=bool(args.by_signal_type), max_walk_forward_windows=int(args.max_walk_forward_windows), window_mode=str(args.window_mode), rolling_train_years=int(args.rolling_train_years), rolling_valid_years=int(args.rolling_valid_years), rolling_test_years=int(args.rolling_test_years), rolling_step_years=int(args.rolling_step_years), max_auc_gap=float(args.max_auc_gap), max_valid_test_gap=float(args.max_valid_test_gap), top_feature_importance_alert_pct=float(args.top_feature_alert_pct), min_train_samples=int(args.min_train_samples), min_valid_samples=int(args.min_valid_samples), min_test_samples=int(args.min_test_samples), max_unaudited_features=int(args.max_unaudited_features), generic_mode=str(args.generic_mode), pool_symbols=members, pool_name=pool_name, min_used_symbols=int(args.min_used_symbols), seed=int(args.seed), oot_eval_config=effective_oot_cfg)
                except Exception:
                    logger.exception('GROUP-POOL pipeline failed for group=%s interval=%s', group_name, interval)
                    continue
                logger.info('[%s][%s] report=%s predictions=%s metrics=%s top10=%s', pool_name, interval, res.report_path, res.prediction_path, res.metrics_path, res.top_feature_importance_path)
                interval_records.append({'interval': str(interval), 'group_name': str(group_name), 'pool_name': pool_name, 'model_dir': str(Path(res.output_dir).resolve()), 'members': [{'symbol': str(s).upper(), 'exchange': str(e or '')} for s, e in members]})
                runtime_bundle_records.append({'interval': str(interval), 'group_name': str(group_name), 'pool_name': pool_name, 'members': list(members), 'result': res})
            if interval_records:
                registry_records.extend(interval_records)
            else:
                logger.warning('group-pool interval=%s has no successful group training; registry interval entry is empty', interval)
        if registry_records:
            registry_root = (output_root or DEFAULT_REPORT_ROOT).resolve()
            registry_root.mkdir(parents=True, exist_ok=True)
            registry_path = registry_root / f'{run_date_tag}_cluster_registry_{str(args.group_by).strip().lower()}_{args.trade_side_mode}.json'
            registry_payload = {'run_tag': run_date_tag, 'group_by': str(args.group_by), 'trade_side_mode': str(args.trade_side_mode), 'intervals': sorted({r['interval'] for r in registry_records}), 'entries': registry_records}
            registry_path.write_text(json.dumps(registry_payload, ensure_ascii=False, indent=2), encoding='utf-8')
            logger.info('cluster registry written: %s (%d entries)', registry_path, len(registry_records))
        if runtime_bundle_records:
            by_pool: dict[str, list[ModelPipelineResult]] = {}
            for rec in runtime_bundle_records:
                pool_name = str(rec.get('pool_name', ''))
                res = rec.get('result')
                if not pool_name or not isinstance(res, ModelPipelineResult):
                    continue
                by_pool.setdefault(pool_name, []).append(res)
            rewritten_total = 0
            for pool_name, group_results in by_pool.items():
                rewritten = _recompute_oot_with_shared_htf_reference(group_results, effective_oot_cfg)
                rewritten_total += int(rewritten)
                if rewritten:
                    logger.info('[%s] shared-htf OOT recompute rewritten=%d', pool_name, rewritten)
            if rewritten_total:
                logger.info('group-pool shared-htf OOT recompute total rewritten=%d', rewritten_total)
        if bool(effective_oot_cfg.use_portfolio_logic_runtime) and runtime_bundle_records:
            bundle_dir = _write_group_pool_runtime_bundle(root=output_root or DEFAULT_REPORT_ROOT, run_date_tag=run_date_tag, group_by=str(args.group_by), trade_side_mode=str(args.trade_side_mode), run_records=runtime_bundle_records)
            if bundle_dir is not None:
                logger.info('group runtime bundle written: %s', bundle_dir)
        return
    if top_n > 0:
        symbols_to_run = _load_top_n_symbols_from_ranking(Path(args.symbols_ranking_path), top_n=top_n, respect_disabled_manifest=respect_disabled_manifest)
        logger.info('top-n symbol mode enabled: top_n=%s ranking_path=%s loaded=%s', top_n, args.symbols_ranking_path, [s for s, _ in symbols_to_run])
    else:
        symbols_to_run = [(str(args.symbol).upper(), str(args.exchange).upper() if args.exchange else None)]
    if bool(getattr(args, 'pool', False)):
        logger.info('POOL mode: training a single shared model across %d symbols: %s', len(symbols_to_run), [s for s, _ in symbols_to_run])
        pool_results: list[ModelPipelineResult] = []
        for interval in intervals:
            try:
                res = run_model_pipeline(symbol='POOL', exchange=None, interval=interval, start_date=args.start, end_date=args.end, trade_side_mode=args.trade_side_mode, train_end=args.train_end, valid_end=args.valid_end, output_root=output_root, synthetic_periods=args.synthetic_periods, by_signal_type=bool(args.by_signal_type), max_walk_forward_windows=int(args.max_walk_forward_windows), window_mode=str(args.window_mode), rolling_train_years=int(args.rolling_train_years), rolling_valid_years=int(args.rolling_valid_years), rolling_test_years=int(args.rolling_test_years), rolling_step_years=int(args.rolling_step_years), max_auc_gap=float(args.max_auc_gap), max_valid_test_gap=float(args.max_valid_test_gap), top_feature_importance_alert_pct=float(args.top_feature_alert_pct), min_train_samples=int(args.min_train_samples), min_valid_samples=int(args.min_valid_samples), min_test_samples=int(args.min_test_samples), max_unaudited_features=int(args.max_unaudited_features), generic_mode=str(args.generic_mode), pool_symbols=symbols_to_run, min_used_symbols=int(args.min_used_symbols), seed=int(args.seed), oot_eval_config=effective_oot_cfg)
            except Exception:
                logger.exception('POOL pipeline failed for interval=%s', interval)
                continue
            logger.info('[POOL][%s] report: %s', interval, res.report_path)
            logger.info('[POOL][%s] predictions: %s', interval, res.prediction_path)
            logger.info('[POOL][%s] metrics: %s', interval, res.metrics_path)
            logger.info('[POOL][%s] top10 feature importance: %s', interval, res.top_feature_importance_path)
            pool_results.append(res)
        _recompute_oot_with_shared_htf_reference(pool_results, effective_oot_cfg)
        return
    for sidx, (symbol, exchange_from_rank) in enumerate(symbols_to_run, start=1):
        run_exchange = _resolve_run_exchange(exchange_from_rank, args.exchange)
        logger.info('[%d/%d] run symbol=%s exchange=%s intervals=%s', sidx, len(symbols_to_run), symbol, run_exchange, list(intervals))
        results = run_model_pipeline_multi(symbol=symbol, exchange=run_exchange, intervals=intervals, start_date=args.start, end_date=args.end, trade_side_mode=args.trade_side_mode, train_end=args.train_end, valid_end=args.valid_end, output_root=output_root, synthetic_periods=args.synthetic_periods, by_signal_type=bool(args.by_signal_type), max_walk_forward_windows=int(args.max_walk_forward_windows), window_mode=str(args.window_mode), rolling_train_years=int(args.rolling_train_years), rolling_valid_years=int(args.rolling_valid_years), rolling_test_years=int(args.rolling_test_years), rolling_step_years=int(args.rolling_step_years), max_auc_gap=float(args.max_auc_gap), max_valid_test_gap=float(args.max_valid_test_gap), top_feature_importance_alert_pct=float(args.top_feature_alert_pct), min_train_samples=int(args.min_train_samples), min_valid_samples=int(args.min_valid_samples), min_test_samples=int(args.min_test_samples), max_unaudited_features=int(args.max_unaudited_features), generic_mode=str(args.generic_mode), min_used_symbols=int(args.min_used_symbols), seed=int(args.seed), oot_eval_config=effective_oot_cfg)
        for interval, result in zip(intervals, results):
            logger.info('[%s][%s] report: %s', symbol, interval, result.report_path)
            logger.info('[%s][%s] predictions: %s', symbol, interval, result.prediction_path)
            logger.info('[%s][%s] metrics: %s', symbol, interval, result.metrics_path)
            logger.info('[%s][%s] top10 feature importance: %s', symbol, interval, result.top_feature_importance_path)
        if len(results) < len(intervals):
            logger.warning('[%s] only %d/%d intervals succeeded; see logs for failures', symbol, len(results), len(intervals))
