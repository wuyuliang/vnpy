"""Multi-interval helpers for pipeline_orchestrator."""
from __future__ import annotations

from cta.model.orchestration.pipeline_base import (
    DEFAULT_OOT_EVAL_CONFIG,
    FEATURE_ROOT,
    GenericMode,
    ModelPipelineResult,
    OotEvaluationConfig,
    Path,
    Sequence,
    WindowMode,
    logger,
)
from cta.model.reporting.pipeline_outputs import _recompute_oot_with_shared_htf_reference
from cta.model.orchestration.pipeline_run import run_model_pipeline

def _normalize_intervals(raw: Iterable[str]) -> tuple[str, ...]:
    """Normalize a list of interval tokens into an ordered, deduplicated tuple.

    支持以下输入形态（CLI / 程序调用都可以混用）：
    - 空格分隔：``["day", "60min", "30min"]``
    - 逗号分隔（单 token 内）：``["day,60min", "30min"]``
    - 混合 + 重复：``[" day ", "", "60min,, 30min ", "day"]``

    返回首次出现顺序保留的去重元组。完全空时 raise ``ValueError`` 而不是
    静默返回空，避免 CLI 默认值消失后没人发现。
    """
    seen: set[str] = set()
    out: list[str] = []
    for token in raw:
        if token is None:
            continue
        for piece in str(token).split(','):
            v = piece.strip().lower()
            if not v:
                continue
            if v in seen:
                continue
            seen.add(v)
            out.append(v)
    if not out:
        raise ValueError('no valid interval provided; expected one of day/60min/30min/15min/5min/min')
    return tuple(out)

def run_model_pipeline_multi(symbol: str='RB0', exchange: str | None='SHFE', intervals: Sequence[str]=('60min',), start_date: str='2000-01-01', end_date: str='2019-12-31', trade_side_mode: str='both', train_end: str='2018-12-31', valid_end: str='2019-06-30', output_root: Path | None=None, feature_root: Path=FEATURE_ROOT, synthetic_periods: int=400, by_signal_type: bool=True, max_walk_forward_windows: int=3, window_mode: WindowMode='expanding', rolling_train_years: int=3, rolling_valid_years: int=1, rolling_test_years: int=1, rolling_step_years: int=1, max_auc_gap: float=0.03, max_valid_test_gap: float=0.1, top_feature_importance_alert_pct: float=0.5, min_train_samples: int=50, min_valid_samples: int=20, min_test_samples: int=20, max_unaudited_features: int=500, generic_mode: GenericMode='auto', oot_eval_config: OotEvaluationConfig=DEFAULT_OOT_EVAL_CONFIG, min_used_symbols: int=2, seed: int=2026) -> list[ModelPipelineResult]:
    """Run ``run_model_pipeline`` for each interval and return the result list.

    单一 interval 的失败不会终止整批：每个 interval 独立 run，异常会写日志后
    继续下一个；想要严格"全部成功"语义请自行检查 ``len(results) == len(intervals)``。
    """
    interval_tuple = _normalize_intervals(intervals)
    results: list[ModelPipelineResult] = []
    for idx, interval in enumerate(interval_tuple, start=1):
        logger.info('[%d/%d] running model pipeline for symbol=%s interval=%s', idx, len(interval_tuple), symbol, interval)
        try:
            res = run_model_pipeline(symbol=symbol, exchange=exchange, interval=interval, start_date=start_date, end_date=end_date, trade_side_mode=trade_side_mode, train_end=train_end, valid_end=valid_end, output_root=output_root, feature_root=feature_root, synthetic_periods=synthetic_periods, by_signal_type=by_signal_type, max_walk_forward_windows=max_walk_forward_windows, window_mode=window_mode, rolling_train_years=rolling_train_years, rolling_valid_years=rolling_valid_years, rolling_test_years=rolling_test_years, rolling_step_years=rolling_step_years, max_auc_gap=max_auc_gap, max_valid_test_gap=max_valid_test_gap, top_feature_importance_alert_pct=top_feature_importance_alert_pct, min_train_samples=min_train_samples, min_valid_samples=min_valid_samples, min_test_samples=min_test_samples, max_unaudited_features=max_unaudited_features, generic_mode=generic_mode, oot_eval_config=oot_eval_config, min_used_symbols=min_used_symbols, seed=seed)
        except Exception:
            logger.exception('model pipeline failed for interval=%s', interval)
            continue
        results.append(res)
    _recompute_oot_with_shared_htf_reference(results, oot_eval_config)
    return results
