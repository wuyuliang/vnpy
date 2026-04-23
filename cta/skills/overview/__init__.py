"""00 总纲与研究方法论 / Overview & Methodology.

对应 cta/cta_skills/00_overview_methodology/ 目录下的 5 个 md。
每个子模块只做一件事，全部 interval-agnostic，可被 day 与 minute/minute5/
minute15/minute30/minute60 共用。

子模块
------
- acceptance            : §01 目标门槛 A/B
- research_pool         : §02 研究池解析
- backtest_principles   : §03 回测配置 + lookahead 检测
- live_principles       : §04 实盘熔断 + 对账
- iteration             : §05 想法/迭代记录
"""
from __future__ import annotations

from cta.skills.overview.acceptance import (
    REQUIRED_COLUMNS,
    GateDecision,
    assess_against_gates,
    assert_passes_gate_a,
    assert_passes_gate_b,
    load_acceptance_config,
)
from cta.skills.overview.research_pool import (
    ResearchPool,
    in_research_pool,
    resolve_research_symbols,
)
from cta.skills.overview.backtest_principles import (
    BacktestConfig,
    LookaheadViolation,
    assert_no_lookahead,
    detect_lookahead,
)
from cta.skills.overview.live_principles import (
    CircuitAction,
    LiveGateState,
    check_circuit,
    reconcile_positions,
)
from cta.skills.overview.iteration import (
    IdeaRecord,
    list_open_ideas,
    record_idea,
)

__all__ = [
    # acceptance
    "REQUIRED_COLUMNS", "GateDecision", "assess_against_gates",
    "assert_passes_gate_a", "assert_passes_gate_b", "load_acceptance_config",
    # research_pool
    "ResearchPool", "in_research_pool", "resolve_research_symbols",
    # backtest_principles
    "BacktestConfig", "LookaheadViolation",
    "assert_no_lookahead", "detect_lookahead",
    # live_principles
    "CircuitAction", "LiveGateState", "check_circuit", "reconcile_positions",
    # iteration
    "IdeaRecord", "list_open_ideas", "record_idea",
]
