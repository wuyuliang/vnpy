"""Compatibility shim for baseline skill suite modules."""
from __future__ import annotations

import logging
from pathlib import Path
from typing import Sequence

from cta.config.baseline_skill_suite_config import BASELINE_SIGNAL_TYPES
from cta.strategy.baseline_backtest_cli import (
    _compute_metrics,
    _parse_args,
    main,
    run_baseline_suite as _run_baseline_suite_impl,
)
from cta.strategy.baseline_candidate_gen import (
    _build_raw_setup_candidates,
    _infer_regime_label,
    _resolve_candidate_entry,
    _simulate_candidate_execution_path,
    build_training_samples_from_trade_log,
    generate_candidate_opportunities,
)
from cta.strategy.baseline_feature_frame import prepare_master_feature_frame
from cta.strategy.baseline_helpers import (
    SYMBOLS_RANKING_PATH,
    BaselineSuiteRunResult,
    _compute_atr14,
    _entry_order,
    _load_top_n_symbols_from_ranking,
    _normalize_intervals,
    _resolve_run_exchange,
    _safe_bool,
    _safe_float,
    _side_allowed,
)
from cta.strategy.baseline_strategies import (
    ATRBreakoutBaselineStrategy,
    BreakoutPullbackBaselineStrategy,
    DonchianBaselineStrategy,
    create_baseline_strategy,
)

logger = logging.getLogger(__name__)

run_baseline_suite = _run_baseline_suite_impl


def run_baseline_suite_multi(
    symbol: str,
    exchange: str | None,
    intervals: Sequence[str] | str,
    start_date: str,
    end_date: str,
    signal_types: tuple[str, ...] = BASELINE_SIGNAL_TYPES,
    trade_side_mode: str = "both",
    initial_capital: float = 1_000_000.0,
    periods_per_year: int | None = None,
    output_root: Path | None = None,
) -> list[BaselineSuiteRunResult]:
    """Run baseline suite across multiple intervals.

    Keep this thin wrapper in shim module so legacy tests/users that monkeypatch
    ``cta.strategy.baseline_skill_suite.run_baseline_suite`` still behave.
    """
    interval_tuple = _normalize_intervals(intervals)
    results: list[BaselineSuiteRunResult] = []
    for idx, interval in enumerate(interval_tuple, start=1):
        logger.info(
            "[%d/%d] baseline suite symbol=%s interval=%s",
            idx,
            len(interval_tuple),
            str(symbol).upper(),
            interval,
        )
        try:
            res = run_baseline_suite(
                symbol=symbol,
                exchange=exchange,
                interval=interval,
                start_date=start_date,
                end_date=end_date,
                signal_types=signal_types,
                trade_side_mode=trade_side_mode,
                initial_capital=initial_capital,
                periods_per_year=periods_per_year,
                output_root=output_root,
            )
        except Exception:
            logger.exception("baseline suite failed for symbol=%s interval=%s", symbol, interval)
            continue
        results.append(res)
    return results

__all__ = [
    "BASELINE_SIGNAL_TYPES",
    "SYMBOLS_RANKING_PATH",
    "BaselineSuiteRunResult",
    "prepare_master_feature_frame",
    "create_baseline_strategy",
    "generate_candidate_opportunities",
    "build_training_samples_from_trade_log",
    "run_baseline_suite",
    "run_baseline_suite_multi",
    "_normalize_intervals",
    "_load_top_n_symbols_from_ranking",
    "_resolve_run_exchange",
    "_safe_float",
    "_safe_bool",
    "_compute_atr14",
    "_side_allowed",
    "_entry_order",
    "_infer_regime_label",
    "_resolve_candidate_entry",
    "_simulate_candidate_execution_path",
    "_build_raw_setup_candidates",
    "_compute_metrics",
    "_parse_args",
    "DonchianBaselineStrategy",
    "ATRBreakoutBaselineStrategy",
    "BreakoutPullbackBaselineStrategy",
    "main",
]


if __name__ == "__main__":
    main()
