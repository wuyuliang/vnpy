from __future__ import annotations

from datetime import date
import math

import pandas as pd
import pytest

from cta.strategy.brooks.cycle_v1.backtest.reporter import (
    build_funnel,
    build_group_report,
    build_official_summary,
    compute_performance_metrics,
    write_report_bundle,
)
from cta.strategy.brooks.cycle_v1.research.ablation import ABLATION_STAGES
from cta.strategy.brooks.cycle_v1.research.cycle_validation import validate_cycles
from cta.strategy.brooks.cycle_v1.research.setup_matrix import build_setup_matrix
from cta.strategy.brooks.cycle_v1.research.promotion import assess_dry_run_promotion
from cta.strategy.brooks.cycle_v1.research.stress import STRESS_SCENARIOS
from cta.strategy.brooks.cycle_v1.research.walk_forward import generate_folds


def test_report_funnel_preserves_every_reduction_layer() -> None:
    funnel = build_funnel(
        candidates=pd.DataFrame({"candidate_id": ["c1", "c2", "c3"]}),
        plans=pd.DataFrame({"candidate_id": ["c1", "c2"]}),
        orders=pd.DataFrame({"candidate_id": ["c1", "c2"]}),
        fills=pd.DataFrame({"candidate_id": ["c1"]}),
        trades=pd.DataFrame({"candidate_id": ["c1"]}),
    )

    assert funnel == {
        "candidates": 3,
        "eligible_plans": 2,
        "orders": 2,
        "fills": 1,
        "round_trips": 1,
    }


def test_report_funnel_does_not_count_order_states_or_exit_fills_twice() -> None:
    funnel = build_funnel(
        candidates=pd.DataFrame({"candidate_id": ["c1"]}),
        plans=pd.DataFrame({"order_id": ["o1"]}),
        orders=pd.DataFrame(
            {"order_id": ["o1", "o1"], "status": ["ACTIVE", "FILLED"]}
        ),
        fills=pd.DataFrame(
            {"order_id": ["o1", "o1"], "fill_kind": ["ENTRY", "EXIT"]}
        ),
        trades=pd.DataFrame({"candidate_id": ["c1"]}),
    )

    assert funnel["orders"] == 1
    assert funnel["fills"] == 1


def test_metadata_gap_blocks_official_performance_without_stitching() -> None:
    summary = build_official_summary(
        funnel={"candidates": 3, "eligible_plans": 0, "orders": 0, "fills": 0, "round_trips": 0},
        metadata_gaps=[{"contract": "RB2605.SHF", "field": "fee", "date": "2026-01-05"}],
        performance={"net_return": 0.12},
    )

    assert summary["requested_interval_status"] == "BLOCKED_METADATA"
    assert summary["official_performance"] is None
    assert summary["primary_curve_for_requested_interval"] is None
    assert summary["signal_only_label"] == "SIGNAL_ONLY_DIAGNOSTIC"
    assert summary["assumed_mechanics_appendix"] == "NON_CAUSAL_SCENARIO"


def test_assumed_metadata_keeps_scenario_performance_separate() -> None:
    performance = {"trade_count": 2, "total_return": 0.03}
    curve = [{"date": "2026-01-05", "equity": 1_030_000.0}]
    assumptions = [
        {
            "root_symbol": "JM",
            "contract_code": "JM2605.DCE",
            "field": "roll_fee_reference",
        }
    ]

    summary = build_official_summary(
        funnel={"round_trips": 2},
        metadata_gaps=[],
        performance=performance,
        primary_curve=curve,
        assumed_mechanics=assumptions,
    )

    assert summary["requested_interval_status"] == "NON_CAUSAL_SCENARIO"
    assert summary["official_performance"] is None
    assert summary["primary_curve_for_requested_interval"] is None
    assert summary["scenario_performance"] == performance
    assert summary["scenario_curve"] == curve
    assert summary["assumed_mechanics"] == assumptions


def test_missing_locked_oos_result_never_reports_complete() -> None:
    summary = build_official_summary(
        funnel={"candidates": 0, "eligible_plans": 0, "orders": 0, "fills": 0, "round_trips": 0},
        metadata_gaps=[],
        performance=None,
        primary_curve=None,
        promotion_blockers=["real_contract_backtest"],
    )

    assert summary["requested_interval_status"] == "BLOCKED_RESEARCH"
    assert summary["official_performance"] is None
    assert "real_contract_backtest" in summary["promotion_blockers"]


def test_empty_performance_and_curve_never_report_complete() -> None:
    summary = build_official_summary(
        funnel={"candidates": 0, "eligible_plans": 0, "orders": 0, "fills": 0, "round_trips": 0},
        metadata_gaps=[],
        performance={},
        primary_curve=[],
    )

    assert summary["requested_interval_status"] == "BLOCKED_RESEARCH"
    assert summary["official_performance"] is None
    assert {"performance_missing", "primary_curve_missing"}.issubset(
        summary["promotion_blockers"]
    )


def test_official_summary_serializes_undefined_metrics_as_null() -> None:
    summary = build_official_summary(
        funnel={"round_trips": 0},
        metadata_gaps=[],
        performance={"trade_count": 0, "win_rate": math.nan},
        primary_curve=[{"date": "2026-01-05", "equity": 200_000.0}],
    )

    assert summary["requested_interval_status"] == "COMPLETE"
    assert summary["official_performance"]["win_rate"] is None


def test_dry_run_promotion_requires_every_pre_registered_gate() -> None:
    blocked = assess_dry_run_promotion({"metadata_auditable": True})
    eligible = assess_dry_run_promotion(
        {
            "metadata_auditable": True,
            "prefix_invariant": True,
            "offline_online_parity": True,
            "real_contract_backtest": True,
            "cross_instrument_locked_oos": True,
            "cost_delay_stress": True,
            "portfolio_fail_closed": True,
            "reproducible_bundle": True,
        }
    )

    assert blocked.status == "RESEARCH_ONLY"
    assert "real_contract_backtest" in blocked.blockers
    assert eligible.status == "DRY_RUN_ELIGIBLE"
    assert eligible.blockers == ()


def test_cycle_validation_setup_matrix_ablation_and_walk_forward_contracts() -> None:
    cycles = pd.DataFrame(
        {
            "cycle": ["TRADING_RANGE", "TRADING_RANGE", "BULL_TIGHT_CHANNEL"],
            "direction": [0, 0, 1],
            "feature_asof": pd.date_range("2026-01-01", periods=3, tz="Asia/Shanghai"),
        }
    )
    validation = validate_cycles(cycles)
    assert validation["unavailable_ratio"] == 0.0
    assert validation["average_state_duration"] == 1.5

    candidates = pd.DataFrame(
        {
            "candidate_id": ["c1", "c2"],
            "setup": ["H2", "H2"],
            "cycle": ["BULL_BROAD_CHANNEL", "BULL_BROAD_CHANNEL"],
            "direction": [1, 1],
            "symbol": ["RB", "CU"],
            "sector": ["BLACK", "METAL"],
            "timeframe": ["5m", "5m"],
        }
    )
    matrix = build_setup_matrix(
        candidates,
        pd.DataFrame(
            {
                "candidate_id": ["c1"],
                "net_r": [1.0],
                "mfe_r": [1.5],
                "mae_r": [-0.2],
                "holding_bars": [8],
                "fees": [2.0],
                "slippage": [1.0],
            }
        ),
        plans=pd.DataFrame({"candidate_id": ["c1", "c2"]}),
        fills=pd.DataFrame({"candidate_id": ["c1"]}),
    )
    assert matrix["candidate_count"].sum() == 2
    assert matrix["plan_count"].sum() == 2
    assert matrix["fill_count"].sum() == 1
    assert matrix["trade_count"].sum() == 1
    assert matrix["fees"].sum() == 2.0
    assert [stage.stage for stage in ABLATION_STAGES] == [f"A{i}" for i in range(10)]
    assert {scenario.name for scenario in STRESS_SCENARIOS} == {
        "BASE_COST", "SLIPPAGE_1_5X", "TOTAL_COST_2X", "ONE_BAR_DELAY",
        "ADVERSE_OHLC", "LIMIT_LOCK_EXIT",
    }

    folds = generate_folds(
        date(2015, 1, 1),
        date(2024, 12, 31),
        train_years=5,
        validation_years=1,
        test_years=1,
        step_years=1,
        embargo_days=30,
    )
    assert folds
    assert all(fold.validation_end < fold.test_start for fold in folds)


def test_net_performance_and_group_reports_include_zero_trade_days_and_costs() -> None:
    trades = pd.DataFrame(
        {
            "symbol": ["RB", "CU"],
            "sector": ["BLACK", "METAL"],
            "setup": ["BREAKOUT", "H2"],
            "cycle": ["STRONG_BULL_BREAKOUT", "BULL_BROAD_CHANNEL"],
            "direction": [1, 1],
            "gross_pnl": [20.0, -10.0],
            "net_pnl": [15.0, -12.0],
            "net_r": [1.0, -0.8],
            "mfe_r": [1.5, 0.2],
            "mae_r": [-0.2, -1.0],
            "holding_bars": [10, 5],
            "turnover": [1_000.0, 900.0],
            "fees": [3.0, 1.0],
            "slippage": [2.0, 1.0],
        }
    )
    daily = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=4),
            "equity": [100.0, 110.0, 110.0, 105.0],
            "margin_utilization": [0.0, 0.2, 0.0, 0.1],
            "open_risk": [0.0, 2.0, 0.0, 1.0],
        }
    )

    metrics = compute_performance_metrics(trades, daily, initial_equity=100.0)
    groups = build_group_report(trades)

    assert metrics["trade_count"] == 2
    assert metrics["profit_factor"] == 1.25
    assert metrics["zero_return_days"] == 2
    assert metrics["fees"] == 4.0
    assert metrics["total_net_pnl"] == pytest.approx(3.0)
    assert metrics["total_return"] == pytest.approx(0.05)
    assert metrics["final_equity"] == pytest.approx(105.0)
    assert len(groups) == 2
    assert {"average_win", "average_loss", "profit_factor", "average_mfe_R"}.issubset(
        groups.columns
    )


def test_performance_drawdown_includes_initial_equity_high_watermark() -> None:
    daily = pd.DataFrame(
        {
            "date": pd.date_range("2026-01-01", periods=2),
            "equity": [90.0, 95.0],
        }
    )

    metrics = compute_performance_metrics(
        pd.DataFrame(),
        daily,
        initial_equity=100.0,
    )

    assert metrics["max_drawdown"] == pytest.approx(0.10)


def test_markdown_report_contains_concrete_official_performance(tmp_path) -> None:
    output = write_report_bundle(
        tmp_path / "run",
        tables={"trades.csv": pd.DataFrame()},
        summary={
            "requested_interval_status": "COMPLETE",
            "funnel": {"round_trips": 2},
            "official_performance": {
                "trade_count": 2,
                "total_net_pnl": 3_602.55,
                "total_return": 0.01801275,
                "annualized_return": 0.033886,
                "win_rate": 1.0,
                "max_drawdown": 0.000995725,
                "profit_factor": None,
                "expectancy_R": 1.0597908,
                "fees": 197.45,
                "slippage": 200.0,
                "final_equity": 203_602.55,
            },
        },
    )

    report = (output / "report.md").read_text(encoding="utf-8")
    assert report.index("## Trading Summary") < report.index("## Funnel")
    assert "- trade_count: 2" in report
    assert "- total_net_pnl: 3,602.55" in report
    assert "- total_return: 1.80%" in report
    assert "- win_rate: 100.00%" in report
    assert "- profit_factor: N/A" in report
    assert "- final_equity: 203,602.55" in report
    assert "## Official Performance" in report
    assert "annualized_return: 0.033886" in report
    assert "max_drawdown: 0.000995725" in report


def test_markdown_report_renders_compact_execution_metadata_update(tmp_path) -> None:
    output = write_report_bundle(
        tmp_path / "run",
        tables={"trades.csv": pd.DataFrame()},
        summary={
            "requested_interval_status": "BLOCKED_METADATA",
            "funnel": {"round_trips": 0},
            "official_performance": None,
            "execution_metadata_update": {
                "enabled": True,
                "status": "BLOCKED_METADATA",
                "cache_key": "abc123",
                "contract_date_pairs": 141,
                "generated_daily_rows": 140,
                "reason_code": "MARGIN_MISMATCH",
                "reason": "I2609.DCE margin differs",
            },
        },
    )

    report = (output / "report.md").read_text(encoding="utf-8")
    assert "## Execution Metadata Update" in report
    assert "- status: BLOCKED_METADATA" in report
    assert "- contract_date_pairs: 141" in report
    assert "- reason_code: MARGIN_MISMATCH" in report


def test_markdown_report_includes_reproduction_command(tmp_path) -> None:
    output = write_report_bundle(
        tmp_path / "run",
        tables={"trades.csv": pd.DataFrame()},
        summary={
            "requested_interval_status": "COMPLETE",
            "funnel": {"round_trips": 0},
            "official_performance": None,
            "reproduction_command": {
                "working_directory": "/repo/root",
                "shell_command": (
                    "cd /repo/root && python3 -m "
                    "cta.strategy.brooks.cycle_v1.backtest.runner "
                    "--symbols LC --top-n 2 --include-ema-eligible"
                ),
            },
        },
    )

    report = (output / "report.md").read_text(encoding="utf-8")
    assert "## Reproduction Command" in report
    assert "- working_directory: /repo/root" in report
    assert "cd /repo/root && python3 -m" in report


def test_markdown_trading_summary_handles_zero_trades(tmp_path) -> None:
    output = write_report_bundle(
        tmp_path / "run",
        tables={"trades.csv": pd.DataFrame()},
        summary={
            "requested_interval_status": "COMPLETE",
            "funnel": {"round_trips": 0},
            "official_performance": {
                "trade_count": 0,
                "total_net_pnl": 0.0,
                "total_return": 0.0,
                "annualized_return": 0.0,
                "win_rate": None,
                "max_drawdown": 0.0,
                "profit_factor": None,
                "expectancy_R": None,
                "fees": 0.0,
                "slippage": 0.0,
                "final_equity": 200_000.0,
            },
        },
    )

    report = (output / "report.md").read_text(encoding="utf-8")
    assert "- trade_count: 0" in report
    assert "- total_net_pnl: 0.00" in report
    assert "- total_return: 0.00%" in report
    assert "- win_rate: N/A" in report
    assert "- final_equity: 200,000.00" in report


def test_markdown_report_explains_candidate_risk_rejections(tmp_path) -> None:
    rejections = pd.DataFrame(
        {
            "reason_code": ["ONE_LOT_EXCEEDS_RISK_BUDGET"],
            "candidate_id": ["candidate-cu"],
            "risk_budget": [400.0],
            "loss_per_lot": [2_554.1],
            "detail": ["entry=104990.0;stop=104540.0"],
        }
    )

    output = write_report_bundle(
        tmp_path / "run",
        tables={"rejections.csv": rejections},
        summary={
            "requested_interval_status": "COMPLETE",
            "funnel": {"candidates": 1, "orders": 0},
            "official_performance": {"trade_count": 0},
        },
    )

    report = (output / "report.md").read_text(encoding="utf-8")
    assert "## Rejection Summary" in report
    assert "ONE_LOT_EXCEEDS_RISK_BUDGET: 1" in report
    assert "## Candidate Rejections" in report
    assert "candidate-cu ONE_LOT_EXCEEDS_RISK_BUDGET" in report
    assert "risk_budget=400.0 loss_per_lot=2554.1" in report
