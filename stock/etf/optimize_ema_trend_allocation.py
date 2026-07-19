from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, replace
from itertools import product

import pandas as pd

from .ema_trend_allocation_strategy import (
    TrendAllocationConfig,
    calculate_period_metrics,
    run_trend_allocation_backtest,
)

TRAIN_START = "2017-08-14"
TRAIN_END = "2022-12-30"
VALIDATION_START = "2023-01-01"
VALIDATION_END = "2024-12-31"
SELECTION_CUTOFF = VALIDATION_END
OOS_START = "2025-01-01"
OOS_END = "2026-07-17"


@dataclass
class OptimizationResult:
    """Candidate audit table and the uniquely selected configuration."""

    candidate_results: pd.DataFrame
    selected_config: TrendAllocationConfig | None


def preregistered_configs(
    base_config: TrendAllocationConfig | None = None,
) -> list[TrendAllocationConfig]:
    """Return the eight preregistered configurations in deterministic order."""
    base = base_config or TrendAllocationConfig()
    return [
        replace(
            base,
            slow_period=slow,
            confirmation_days=confirmation,
            slope_lookback=slope,
        )
        for slow, confirmation, slope in product((20, 30), (1, 2), (3, 5))
    ]


def _feasible_candidates(frame: pd.DataFrame) -> pd.Series:
    feasible = (
        frame["train_max_drawdown"].ge(-0.35)
        & frame["train_annual_one_way_turnover"].le(8.0)
        & frame["validation_max_drawdown"].ge(-0.35)
        & frame["validation_annual_one_way_turnover"].le(8.0)
    )
    if "train_feasible" in frame:
        feasible &= frame["train_feasible"].eq(True)
    if "validation_feasible" in frame:
        feasible &= frame["validation_feasible"].eq(True)
    return feasible


def _ordered_feasible_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    feasible = frame.loc[_feasible_candidates(frame)]
    return feasible.sort_values(
        by=[
            "validation_total_return",
            "validation_max_drawdown",
            "validation_annual_one_way_turnover",
            "slow",
            "confirmation",
            "slope",
        ],
        ascending=[False, False, True, True, True, True],
        kind="stable",
    )


def select_candidate(frame: pd.DataFrame) -> pd.Series | None:
    """Select the unique feasible candidate using preregistered tie-breakers."""
    ordered = _ordered_feasible_candidates(frame)
    if ordered.empty:
        return None
    return ordered.iloc[0].copy()


def optimize_on_train_validation(
    selection_bars: pd.DataFrame,
    base_config: TrendAllocationConfig | None = None,
) -> OptimizationResult:
    """Evaluate and select candidates using train and validation data only."""
    bars = selection_bars.copy()
    bars["datetime"] = pd.to_datetime(bars["datetime"], errors="raise")
    if bars["datetime"].dt.normalize().gt(pd.Timestamp(SELECTION_CUTOFF)).any():
        raise ValueError("selection data ends after 2024-12-31")

    configs = preregistered_configs(base_config)
    rows: list[dict[str, object]] = []
    for config in configs:
        backtest = run_trend_allocation_backtest(bars, config)
        train_metrics = calculate_period_metrics(
            backtest.equity_curve,
            backtest.trades,
            TRAIN_START,
            TRAIN_END,
        )
        validation_metrics = calculate_period_metrics(
            backtest.equity_curve,
            backtest.trades,
            VALIDATION_START,
            VALIDATION_END,
        )
        row: dict[str, object] = {
            "slow": config.slow_period,
            "confirmation": config.confirmation_days,
            "slope": config.slope_lookback,
        }
        row.update({f"train_{key}": value for key, value in train_metrics.items()})
        row.update(
            {f"validation_{key}": value for key, value in validation_metrics.items()}
        )
        row["train_feasible"] = bool(
            train_metrics["max_drawdown"] >= -0.35
            and train_metrics["annual_one_way_turnover"] <= 8.0
        )
        row["validation_feasible"] = bool(
            validation_metrics["max_drawdown"] >= -0.35
            and validation_metrics["annual_one_way_turnover"] <= 8.0
        )
        rows.append(row)

    candidates = pd.DataFrame(rows)
    candidates["rank"] = pd.Series(pd.NA, index=candidates.index, dtype="Int64")
    for rank, index in enumerate(
        _ordered_feasible_candidates(candidates).index,
        start=1,
    ):
        candidates.at[index, "rank"] = rank

    candidates["selected"] = False
    selected = select_candidate(candidates)
    if selected is None:
        return OptimizationResult(candidates, None)

    selected_index = selected.name
    candidates.at[selected_index, "selected"] = True
    return OptimizationResult(candidates, configs[int(selected_index)])


def evaluate_release(
    candidate_full: Mapping[str, float],
    candidate_oos: Mapping[str, float],
    baseline_full: Mapping[str, float],
    baseline_oos: Mapping[str, float],
) -> dict[str, object]:
    """Evaluate the six fixed return, drawdown, and turnover release gates."""
    checks = {
        "full_return_improved": (
            candidate_full["total_return"] > baseline_full["total_return"]
        ),
        "full_drawdown_within_limit": candidate_full["max_drawdown"] >= -0.35,
        "full_turnover_within_limit": (
            candidate_full["annual_one_way_turnover"] <= 8.0
        ),
        "oos_return_improved": (
            candidate_oos["total_return"] > baseline_oos["total_return"]
        ),
        "oos_drawdown_within_limit": candidate_oos["max_drawdown"] >= -0.35,
        "oos_turnover_within_limit": (candidate_oos["annual_one_way_turnover"] <= 8.0),
    }
    failed_checks = [name for name, passed in checks.items() if not passed]
    return {
        "status": "accepted" if not failed_checks else "rejected",
        "checks": checks,
        "failed_checks": failed_checks,
    }
