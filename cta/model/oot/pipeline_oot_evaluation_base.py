'OOT real-execution evaluation helpers.'

from __future__ import annotations

import dataclasses

import logging

from typing import Any, Callable

import numpy as np

import pandas as pd

from cta.config.model_oot_eval_config import DEFAULT_OOT_EVAL_CONFIG, OotEvaluationConfig

from cta.config.symbol_cluster_config import infer_symbol_cluster

from cta.model.oot.block_reasons import BR_BLOCKED_CLUSTER_CAP, BR_BLOCKED_DAILY_POSITION, BR_BLOCKED_FINAL_DECISION_GATE, BR_BLOCKED_LEVERAGE, BR_BLOCKED_MARGIN_CASH, BR_BLOCKED_MFE_MAE_GATE, BR_BLOCKED_MONTHLY_DRAWDOWN, BR_BLOCKED_PORTFOLIO_CONSTRAINT, BR_BLOCKED_REGIME_GATE, BR_BLOCKED_SIGNAL_TYPE_CONCURRENT, BR_BLOCKED_SYMBOL_CAP, BR_BLOCKED_TOTAL_NOTIONAL, BR_BLOCKED_TRADE_FILTER, BR_BLOCKED_TOTAL_CONCURRENT, BR_BLOCKED_WEEKLY_BUDGET, BR_BLOCKED_WEEKLY_DRAWDOWN, BR_HTF_MISSING, BR_LOG_EXECUTED_SENTINEL, BR_ZERO_NOTIONAL

from cta.portfolio_logic.interval_gate import HtfGate

from cta.portfolio_logic.opportunity_ranker import OpportunityRanker

from cta.portfolio_logic.portfolio_state import PortfolioState

from cta.portfolio_logic.pyramid_manager import PyramidManager, PyramidPosition

from cta.portfolio_logic.risk_throttle import EquityTracker, RiskThrottle

from cta.portfolio_logic.trailing_exit import simulate_trailing_exit

from cta.portfolio_logic.config import normalize_portfolio_interval

from cta.model.oot.oot_intrabar import _IntrabarBarCache, _simulate_intrabar_exit

from cta.model.oot.oot_metrics import _calc_roll_cost, _max_drawdown_from_return_series

from cta.model.oot.oot_position_lifetime import _build_position_lifetime_table

from cta.strategy.skill_tight_range_backtest import normalize_interval

logger = logging.getLogger("cta.model.oot.pipeline_oot_evaluation")

def _log_block_reason_distribution(trade_df: pd.DataFrame, *, path_marker: str) -> None:
    """Fix-C：打印 OOT block_reason 分布到 logger（见 cta/docs/block_reason.md §7）。

    每次 OOT 评估完都至少打印一次 INFO；当 htf_missing 超过半数时额外 WARN
    指向 §4-§6 排查路径。
    """
    if trade_df.empty or 'block_reason' not in trade_df.columns:
        return
    reason_series = trade_df['block_reason'].fillna('').replace('', BR_LOG_EXECUTED_SENTINEL)
    reason_counts = reason_series.value_counts(dropna=False).to_dict()
    total = int(len(trade_df))
    logger.info('OOT block_reason distribution [path=%s] (total=%d): %s', path_marker, total, reason_counts)
    htf_miss = int(reason_counts.get(BR_HTF_MISSING, 0))
    if total > 0 and htf_miss > total * 0.5:
        logger.warning('More than 50%% of candidates blocked by htf_missing (%d/%d). Check htf_intervals vs available data (see cta/docs/block_reason.md §4-§6).', htf_miss, total)

def _resolve_contract_spec(cfg: OotEvaluationConfig, symbol: str) -> tuple[float, float, float]:
    """Resolve per-symbol contract spec (contract_size, lot_size, margin_rate)."""
    spec_map = getattr(cfg, 'symbol_contract_specs', {}) or {}
    key = str(symbol or '').strip().upper()
    raw = spec_map.get(key) or spec_map.get(str(symbol or '').strip()) or {}
    contract_size = float(raw.get('contract_size', 1.0) or 1.0)
    lot_size = float(raw.get('lot_size', 1.0) or 1.0)
    margin_rate = float(raw.get('margin_rate', float(cfg.margin_rate)) or float(cfg.margin_rate))
    contract_size = contract_size if contract_size > 0 else 1.0
    lot_size = lot_size if lot_size > 0 else 1.0
    margin_rate = margin_rate if margin_rate > 0 else float(cfg.margin_rate)
    return (contract_size, lot_size, margin_rate)


__all__ = [
    name
    for name in globals()
    if not (name.startswith("__") and name.endswith("__"))
]
