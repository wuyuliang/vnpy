from __future__ import annotations

from datetime import date
from types import SimpleNamespace

import numpy as np
import pandas as pd

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig
from cta.strategy import multi_timeframe_trend_strategy as strategy
from cta.strategy.multi_timeframe_trend_backtest import engine, report
from cta.strategy.multi_timeframe_trend_backtest.diagnostics import (
    GateFailOpenDiagnostics,
)


def test_missing_gate_inputs_increment_fail_open_counters() -> None:
    diagnostics = GateFailOpenDiagnostics()
    candidate = SimpleNamespace(trigger=100.0, direction=1)

    reason = strategy._entry_range_position_reason(
        candidate=candidate,
        row=pd.Series(
            {"range_window_high": np.nan, "range_window_low": np.nan}
        ),
        config=MultiTimeframeTrendConfig(),
        diagnostics=diagnostics,
    )
    eligible = engine.turnover_eligible_by_date(
        pd.DataFrame(columns=["root_symbol", "trade_date", "turnover"]),
        share=0.8,
        lookback_days=5,
        diagnostics=diagnostics,
    )
    turnover_reason = engine._turnover_share_blocked(
        "AG",
        date(2026, 3, 2),
        {date(2026, 2, 27): frozenset({"AG"})},
        diagnostics=diagnostics,
    )
    gaps = engine._high_gap_flags_by_trade_date(
        None,
        MultiTimeframeTrendConfig(),
        diagnostics=diagnostics,
    )

    assert reason == ""
    assert eligible == {}
    assert turnover_reason == ""
    assert gaps == {}
    assert diagnostics.fail_open_counts() == {
        "entry_range_position_no_window": 1,
        "high_gap_classification": 1,
        "turnover_cohort_missing_date": 1,
        "turnover_table_unavailable": 1,
    }
    assert diagnostics.evaluation_counts() == {
        "entry_range_position_no_window": 1,
        "high_gap_classification": 1,
        "turnover_cohort_missing_date": 1,
        "turnover_table_unavailable": 1,
    }


def test_report_warns_when_gate_fail_open_rate_exceeds_95_percent() -> None:
    rendered = report._render_report(
        {
            "gate_fail_open": {
                "entry_range_position_no_window": 96,
                "turnover_cohort_missing_date": 4,
            },
            "gate_evaluations": {
                "entry_range_position_no_window": 100,
                "turnover_cohort_missing_date": 100,
            },
        }
    )

    assert "## 闸门降级警告" in rendered
    assert "entry_range_position_no_window" in rendered
    assert "96.00%" in rendered
    assert "疑似未生效" in rendered
    assert "turnover_cohort_missing_date" not in rendered
