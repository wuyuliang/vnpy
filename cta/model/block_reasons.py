"""Canonical block-reason literals used across OOT and candidate pipelines."""
from __future__ import annotations

from typing import Literal


# OOT execution-time reasons.
BR_INVALID_TIME = "invalid_time"
BR_BLOCKED_THROTTLE_HALT = "blocked_throttle_halt"
BR_HTF_MISSING = "htf_missing"
BR_HTF_CONFLICT = "htf_conflict"
BR_HTF_OPPOSITE = "htf_opposite"
BR_HTF_UNKNOWN = "htf_unknown"
BR_RANKER_DROPPED = "ranker_dropped"
BR_BLOCKED_LIMIT_MOVE = "blocked_limit_move"
BR_BLOCKED_PYRAMID_RULE = "blocked_pyramid_rule"
BR_BLOCKED_MONTHLY_DRAWDOWN = "blocked_monthly_drawdown"
BR_BLOCKED_WEEKLY_DRAWDOWN = "blocked_weekly_drawdown"
BR_BLOCKED_TOTAL_CONCURRENT = "blocked_total_concurrent"
BR_BLOCKED_SYMBOL_CONCURRENT = "blocked_symbol_concurrent"
BR_BLOCKED_SYMBOL_CAP = "blocked_symbol_cap"
BR_BLOCKED_CLUSTER_CAP = "blocked_cluster_cap"
BR_BLOCKED_WEEKLY_BUDGET = "blocked_weekly_budget"
BR_BLOCKED_DAILY_POSITION = "blocked_daily_position"
BR_BLOCKED_MARGIN_CASH = "blocked_margin_cash"
BR_BLOCKED_LEVERAGE = "blocked_leverage"
BR_BLOCKED_PORTFOLIO_CONSTRAINT = "blocked_portfolio_constraint"
BR_ZERO_NOTIONAL = "zero_notional"

# Candidate fallback reasons.
BR_FILTERED_BY_RULE = "filtered_by_rule"
BR_RISK_RULE_BLOCKED = "risk_rule_blocked"
BR_CAPACITY_BLOCKED = "capacity_blocked"
BR_EXECUTION_RULE_BLOCKED = "execution_rule_blocked"
BR_NEXT_BAR_NOT_TRIGGERED = "next_bar_not_triggered"

# Logger-only sentinel (not a canonical block_reason).
BR_LOG_EXECUTED_SENTINEL = "__executed__"


BlockReason = Literal[
    "invalid_time",
    "blocked_throttle_halt",
    "htf_missing",
    "htf_conflict",
    "htf_opposite",
    "htf_unknown",
    "ranker_dropped",
    "blocked_limit_move",
    "blocked_pyramid_rule",
    "blocked_monthly_drawdown",
    "blocked_weekly_drawdown",
    "blocked_total_concurrent",
    "blocked_symbol_concurrent",
    "blocked_symbol_cap",
    "blocked_cluster_cap",
    "blocked_weekly_budget",
    "blocked_daily_position",
    "blocked_margin_cash",
    "blocked_leverage",
    "blocked_portfolio_constraint",
    "zero_notional",
    "filtered_by_rule",
    "risk_rule_blocked",
    "capacity_blocked",
    "execution_rule_blocked",
    "next_bar_not_triggered",
]


CANONICAL_BLOCK_REASONS: tuple[BlockReason, ...] = (
    BR_INVALID_TIME,
    BR_BLOCKED_THROTTLE_HALT,
    BR_HTF_MISSING,
    BR_HTF_CONFLICT,
    BR_HTF_OPPOSITE,
    BR_HTF_UNKNOWN,
    BR_RANKER_DROPPED,
    BR_BLOCKED_LIMIT_MOVE,
    BR_BLOCKED_PYRAMID_RULE,
    BR_BLOCKED_MONTHLY_DRAWDOWN,
    BR_BLOCKED_WEEKLY_DRAWDOWN,
    BR_BLOCKED_TOTAL_CONCURRENT,
    BR_BLOCKED_SYMBOL_CONCURRENT,
    BR_BLOCKED_SYMBOL_CAP,
    BR_BLOCKED_CLUSTER_CAP,
    BR_BLOCKED_WEEKLY_BUDGET,
    BR_BLOCKED_DAILY_POSITION,
    BR_BLOCKED_MARGIN_CASH,
    BR_BLOCKED_LEVERAGE,
    BR_BLOCKED_PORTFOLIO_CONSTRAINT,
    BR_ZERO_NOTIONAL,
    BR_FILTERED_BY_RULE,
    BR_RISK_RULE_BLOCKED,
    BR_CAPACITY_BLOCKED,
    BR_EXECUTION_RULE_BLOCKED,
    BR_NEXT_BAR_NOT_TRIGGERED,
)


__all__ = [
    "BR_BLOCKED_CLUSTER_CAP",
    "BR_BLOCKED_DAILY_POSITION",
    "BR_BLOCKED_LEVERAGE",
    "BR_BLOCKED_LIMIT_MOVE",
    "BR_BLOCKED_MARGIN_CASH",
    "BR_BLOCKED_MONTHLY_DRAWDOWN",
    "BR_BLOCKED_PORTFOLIO_CONSTRAINT",
    "BR_BLOCKED_PYRAMID_RULE",
    "BR_BLOCKED_SYMBOL_CAP",
    "BR_BLOCKED_SYMBOL_CONCURRENT",
    "BR_BLOCKED_THROTTLE_HALT",
    "BR_BLOCKED_TOTAL_CONCURRENT",
    "BR_BLOCKED_WEEKLY_BUDGET",
    "BR_BLOCKED_WEEKLY_DRAWDOWN",
    "BR_CAPACITY_BLOCKED",
    "BR_EXECUTION_RULE_BLOCKED",
    "BR_FILTERED_BY_RULE",
    "BR_HTF_CONFLICT",
    "BR_HTF_MISSING",
    "BR_HTF_OPPOSITE",
    "BR_HTF_UNKNOWN",
    "BR_INVALID_TIME",
    "BR_LOG_EXECUTED_SENTINEL",
    "BR_NEXT_BAR_NOT_TRIGGERED",
    "BR_RANKER_DROPPED",
    "BR_RISK_RULE_BLOCKED",
    "BR_ZERO_NOTIONAL",
    "BlockReason",
    "CANONICAL_BLOCK_REASONS",
]
