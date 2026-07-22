from itertools import product
from types import SimpleNamespace

import numpy as np
import pandas as pd
import pytest

from stock.etf import optimize_ema_trend_allocation as optimization
from stock.etf.ema_trend_allocation_strategy import TrendAllocationConfig
from stock.etf.optimize_ema_trend_allocation import (
    OOS_END,
    OOS_START,
    SELECTION_CUTOFF,
    TRAIN_END,
    TRAIN_START,
    VALIDATION_END,
    VALIDATION_START,
    OptimizationResult,
    evaluate_release,
    optimize_on_train_validation,
    preregistered_configs,
    select_candidate,
)


def test_period_constants_match_preregistered_dates() -> None:
    assert (
        TRAIN_START,
        TRAIN_END,
        VALIDATION_START,
        VALIDATION_END,
        SELECTION_CUTOFF,
        OOS_START,
        OOS_END,
    ) == (
        "2017-08-14",
        "2022-12-30",
        "2023-01-01",
        "2024-12-31",
        "2024-12-31",
        "2025-01-01",
        "2026-07-17",
    )


def test_optimization_result_holds_candidates_and_optional_selection() -> None:
    frame = pd.DataFrame({"slow": [20]})

    result = OptimizationResult(frame, None)

    assert result.candidate_results is frame
    assert result.selected_config is None


def test_preregistered_configs_are_exact_ordered_product_and_preserve_base() -> None:
    base = TrendAllocationConfig(
        symbol="TEST.SZ",
        initial_capital=2_000_000.0,
        commission_rate=0.001,
        min_commission=10.0,
        slippage_rate=0.002,
        slow_period=30,
        confirmation_days=1,
        slope_lookback=5,
        risk_increase_cooldown_days=7,
    )

    configs = preregistered_configs(base)

    expected_parameters = list(product((20, 30), (1, 2), (3, 5)))
    assert [
        (config.slow_period, config.confirmation_days, config.slope_lookback)
        for config in configs
    ] == expected_parameters
    assert len(configs) == 8
    assert all(
        (
            config.symbol,
            config.initial_capital,
            config.lot_size,
            config.commission_rate,
            config.min_commission,
            config.slippage_rate,
            config.risk_increase_cooldown_days,
        )
        == (
            base.symbol,
            base.initial_capital,
            base.lot_size,
            base.commission_rate,
            base.min_commission,
            base.slippage_rate,
            base.risk_increase_cooldown_days,
        )
        for config in configs
    )


def _candidate(**overrides: object) -> dict[str, object]:
    row: dict[str, object] = {
        "slow": 20,
        "confirmation": 1,
        "slope": 3,
        "risk_increase_cooldown_days": 10,
        "train_max_drawdown": -0.20,
        "train_annual_one_way_turnover": 4.0,
        "validation_total_return": 0.10,
        "validation_max_drawdown": -0.20,
        "validation_annual_one_way_turnover": 4.0,
    }
    row.update(overrides)
    return row


def test_selection_rejects_oos_rows_before_calling_backtest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    called = False

    def forbidden_backtest(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        optimization,
        "run_trend_allocation_backtest",
        forbidden_backtest,
    )
    bars = pd.DataFrame({"datetime": ["2024-12-31", "2025-01-02"]})

    with pytest.raises(ValueError) as exc_info:
        optimize_on_train_validation(bars)

    assert str(exc_info.value) == "selection data ends after 2024-12-31"
    assert called is False


def _assert_datetime_rejected_before_backtest(
    monkeypatch: pytest.MonkeyPatch,
    datetimes: object,
    message: str,
) -> None:
    called = False

    def forbidden_backtest(*args: object, **kwargs: object) -> None:
        nonlocal called
        called = True

    monkeypatch.setattr(
        optimization,
        "run_trend_allocation_backtest",
        forbidden_backtest,
    )

    with pytest.raises(ValueError) as exc_info:
        optimize_on_train_validation(pd.DataFrame({"datetime": datetimes}))

    assert str(exc_info.value) == message
    assert called is False


def test_selection_rejects_nat_before_calling_backtest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_datetime_rejected_before_backtest(
        monkeypatch,
        [TRAIN_START, pd.NaT],
        "selection datetime contains NaT",
    )


def test_selection_rejects_uniform_timezone_before_calling_backtest(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    _assert_datetime_rejected_before_backtest(
        monkeypatch,
        pd.date_range("2024-12-30", periods=2, tz="Asia/Shanghai"),
        "selection datetime must be timezone-naive",
    )


@pytest.mark.parametrize(
    "datetimes",
    [
        [
            pd.Timestamp("2024-12-30", tz="UTC"),
            pd.Timestamp("2024-12-31", tz="Asia/Shanghai"),
        ],
        [
            pd.Timestamp("2024-12-30"),
            pd.Timestamp("2024-12-31", tz="UTC"),
        ],
    ],
)
def test_selection_rejects_mixed_timezones_before_calling_backtest(
    monkeypatch: pytest.MonkeyPatch,
    datetimes: list[pd.Timestamp],
) -> None:
    _assert_datetime_rejected_before_backtest(
        monkeypatch,
        datetimes,
        "selection datetime must be timezone-naive",
    )


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("train_max_drawdown", -0.350_001),
        ("train_annual_one_way_turnover", 8.000_001),
        ("validation_max_drawdown", -0.350_001),
        ("validation_annual_one_way_turnover", 8.000_001),
    ],
)
def test_select_candidate_filters_each_train_and_validation_constraint(
    field: str,
    value: float,
) -> None:
    invalid_high_return = _candidate(
        slow=30,
        confirmation=2,
        slope=5,
        validation_total_return=0.90,
        **{field: value},
    )
    valid_lower_return = _candidate(validation_total_return=0.20)

    selected = select_candidate(pd.DataFrame([invalid_high_return, valid_lower_return]))

    assert selected is not None
    assert selected["validation_total_return"] == 0.20


def test_select_candidate_keeps_exact_constraint_boundaries() -> None:
    boundary = _candidate(
        slow=30,
        confirmation=2,
        slope=5,
        train_max_drawdown=-0.35,
        train_annual_one_way_turnover=8.0,
        validation_total_return=0.30,
        validation_max_drawdown=-0.35,
        validation_annual_one_way_turnover=8.0,
    )

    selected = select_candidate(pd.DataFrame([_candidate(), boundary]))

    assert selected is not None
    assert selected["validation_total_return"] == 0.30


@pytest.mark.parametrize(
    ("first", "second", "expected"),
    [
        (
            {"validation_total_return": 0.11},
            {"validation_total_return": 0.12},
            0.12,
        ),
        (
            {"validation_max_drawdown": -0.20},
            {"validation_max_drawdown": -0.10},
            -0.10,
        ),
        (
            {"validation_annual_one_way_turnover": 4.0},
            {"validation_annual_one_way_turnover": 3.0},
            3.0,
        ),
        ({"slow": 30}, {"slow": 20}, 20),
        ({"confirmation": 2}, {"confirmation": 1}, 1),
        ({"slope": 5}, {"slope": 3}, 3),
    ],
)
def test_select_candidate_uses_each_deterministic_tie_breaker(
    first: dict[str, object],
    second: dict[str, object],
    expected: float,
) -> None:
    first_row = _candidate(**first)
    second_row = _candidate(**second)

    selected = select_candidate(pd.DataFrame([first_row, second_row]))

    assert selected is not None
    changed_field = next(iter(first))
    assert selected[changed_field] == expected


def test_select_candidate_does_not_use_fixed_cooldown_as_tie_breaker() -> None:
    first = _candidate(risk_increase_cooldown_days=20)
    second = _candidate(risk_increase_cooldown_days=1)

    selected = select_candidate(pd.DataFrame([first, second]))

    assert selected is not None
    assert selected["risk_increase_cooldown_days"] == 20


def test_select_candidate_returns_none_without_feasible_candidate() -> None:
    frame = pd.DataFrame(
        [
            _candidate(train_max_drawdown=-0.36),
            _candidate(validation_annual_one_way_turnover=8.01),
        ]
    )

    assert select_candidate(frame) is None


@pytest.mark.parametrize(
    "field",
    [
        "validation_total_return",
        "train_max_drawdown",
        "train_annual_one_way_turnover",
        "validation_max_drawdown",
        "validation_annual_one_way_turnover",
    ],
)
@pytest.mark.parametrize("invalid_value", [np.nan, np.inf, -np.inf, "0.10"])
def test_select_candidate_fail_closes_non_numeric_or_non_finite_metrics(
    field: str,
    invalid_value: object,
) -> None:
    invalid_values = {
        "slow": 30,
        "confirmation": 2,
        "slope": 5,
        "validation_total_return": 0.90,
        field: invalid_value,
    }
    invalid = _candidate(**invalid_values)
    valid = _candidate(validation_total_return=0.20)

    selected = select_candidate(pd.DataFrame([invalid, valid]))

    assert selected is not None
    assert selected["validation_total_return"] == 0.20


def test_optimizer_marks_non_finite_metrics_infeasible_and_unranked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    invalid_metrics = {
        (20, 1, 3): (TRAIN_START, "max_drawdown", np.nan),
        (20, 1, 5): (TRAIN_START, "annual_one_way_turnover", np.inf),
        (20, 2, 3): (VALIDATION_START, "total_return", np.inf),
        (20, 2, 5): (VALIDATION_START, "max_drawdown", np.inf),
        (30, 1, 3): (VALIDATION_START, "annual_one_way_turnover", -np.inf),
    }

    def fake_backtest(
        bars: pd.DataFrame,
        config: TrendAllocationConfig,
    ) -> SimpleNamespace:
        del bars
        return SimpleNamespace(
            equity_curve=pd.DataFrame(
                {
                    "slow": [config.slow_period],
                    "confirmation": [config.confirmation_days],
                    "slope": [config.slope_lookback],
                }
            ),
            trades=pd.DataFrame(),
        )

    def fake_metrics(
        equity_curve: pd.DataFrame,
        trades: pd.DataFrame,
        start: object,
        end: object,
    ) -> dict[str, float]:
        del trades, end
        key = tuple(int(value) for value in equity_curve.iloc[0])
        metrics = {
            "total_return": sum(key) / 100,
            "max_drawdown": -0.20,
            "annual_one_way_turnover": 4.0,
        }
        invalid_period, invalid_field, invalid_value = invalid_metrics.get(
            key,
            (None, None, None),
        )
        if str(start) == invalid_period:
            metrics[invalid_field] = invalid_value
        return metrics

    monkeypatch.setattr(optimization, "run_trend_allocation_backtest", fake_backtest)
    monkeypatch.setattr(optimization, "calculate_period_metrics", fake_metrics)
    bars = pd.DataFrame(
        {"datetime": [TRAIN_START, TRAIN_END, VALIDATION_START, VALIDATION_END]}
    )

    result = optimize_on_train_validation(bars)

    invalid = result.candidate_results.iloc[:5]
    assert invalid["selected"].eq(False).all()
    assert invalid["rank"].isna().all()
    assert invalid.iloc[:2]["train_feasible"].eq(False).all()
    assert invalid.iloc[2:]["validation_feasible"].eq(False).all()


def test_optimizer_evaluates_candidates_and_marks_one_deterministic_selection(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    backtest_calls: list[TrendAllocationConfig] = []
    metric_calls: list[tuple[str, str]] = []

    def fake_backtest(
        bars: pd.DataFrame,
        config: TrendAllocationConfig,
    ) -> SimpleNamespace:
        assert pd.api.types.is_datetime64_any_dtype(bars["datetime"])
        backtest_calls.append(config)
        identifying_curve = pd.DataFrame(
            {
                "slow": [config.slow_period],
                "confirmation": [config.confirmation_days],
                "slope": [config.slope_lookback],
            }
        )
        return SimpleNamespace(equity_curve=identifying_curve, trades=pd.DataFrame())

    def fake_metrics(
        equity_curve: pd.DataFrame,
        trades: pd.DataFrame,
        start: object,
        end: object,
    ) -> dict[str, float]:
        del trades
        start_text = str(start)
        end_text = str(end)
        metric_calls.append((start_text, end_text))
        slow = int(equity_curve.iloc[0]["slow"])
        confirmation = int(equity_curve.iloc[0]["confirmation"])
        slope = int(equity_curve.iloc[0]["slope"])
        key = (slow, confirmation, slope)
        metrics = {
            "total_return": slow / 100 + confirmation / 100 + slope / 1_000,
            "max_drawdown": -0.20,
            "annual_one_way_turnover": 4.0,
        }
        if start_text == TRAIN_START and key == (20, 1, 3):
            metrics["max_drawdown"] = -0.36
        if start_text == VALIDATION_START and key == (20, 1, 5):
            metrics["annual_one_way_turnover"] = 8.1
        return metrics

    monkeypatch.setattr(optimization, "run_trend_allocation_backtest", fake_backtest)
    monkeypatch.setattr(optimization, "calculate_period_metrics", fake_metrics)
    base = TrendAllocationConfig(
        symbol="TEST.SZ",
        initial_capital=2_000_000.0,
        commission_rate=0.001,
        min_commission=10.0,
        slippage_rate=0.002,
    )
    bars = pd.DataFrame(
        {"datetime": [TRAIN_START, TRAIN_END, VALIDATION_START, VALIDATION_END]}
    )

    result = optimize_on_train_validation(bars, base)

    candidates = result.candidate_results
    assert len(backtest_calls) == 8
    assert len(metric_calls) == 16
    assert metric_calls.count((TRAIN_START, TRAIN_END)) == 8
    assert metric_calls.count((VALIDATION_START, VALIDATION_END)) == 8
    assert list(
        candidates[
            ["slow", "confirmation", "slope", "risk_increase_cooldown_days"]
        ].itertuples(index=False, name=None)
    ) == [(*parameters, 10) for parameters in product((20, 30), (1, 2), (3, 5))]
    assert candidates["selected"].dtype == bool
    assert candidates["selected"].sum() == 1
    assert candidates["rank"].dtype == "Int64"
    infeasible = ~(candidates["train_feasible"] & candidates["validation_feasible"])
    assert candidates.loc[infeasible, "rank"].isna().all()
    assert sorted(candidates.loc[~infeasible, "rank"].tolist()) == list(range(1, 7))
    selected_row = candidates.loc[candidates["selected"]].iloc[0]
    assert selected_row["rank"] == 1
    assert result.selected_config == TrendAllocationConfig(
        symbol=base.symbol,
        initial_capital=base.initial_capital,
        lot_size=base.lot_size,
        commission_rate=base.commission_rate,
        min_commission=base.min_commission,
        slippage_rate=base.slippage_rate,
        slow_period=30,
        confirmation_days=2,
        slope_lookback=5,
    )


def test_optimizer_integrates_real_backtest_and_metrics_on_selection_history(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    configs = [
        TrendAllocationConfig(confirmation_days=1, slow_period=20),
        TrendAllocationConfig(confirmation_days=1, slow_period=30),
    ]
    monkeypatch.setattr(
        optimization,
        "preregistered_configs",
        lambda base_config=None: configs,
    )
    dates = pd.bdate_range(TRAIN_START, VALIDATION_END)
    closes = [10.0 + index * 0.01 for index in range(len(dates))]
    bars = pd.DataFrame(
        {
            "symbol": ["159915.SZ"] * len(dates),
            "datetime": dates,
            "open": closes,
            "high": [close + 0.1 for close in closes],
            "low": [close - 0.1 for close in closes],
            "close": closes,
            "volume": [1_000_000.0] * len(dates),
        }
    )

    result = optimize_on_train_validation(bars)

    assert len(result.candidate_results) == 2
    assert result.candidate_results["selected"].sum() == 1
    assert result.candidate_results["rank"].notna().all()
    assert result.selected_config in configs


RELEASE_CHECKS = [
    "full_return_improved",
    "full_drawdown_within_limit",
    "full_turnover_within_limit",
    "oos_return_improved",
    "oos_drawdown_within_limit",
    "oos_turnover_within_limit",
]


def _passing_release_metrics() -> tuple[
    dict[str, float],
    dict[str, float],
    dict[str, float],
    dict[str, float],
]:
    candidate_full = {
        "total_return": 0.20,
        "max_drawdown": -0.35,
        "annual_one_way_turnover": 8.0,
    }
    candidate_oos = {
        "total_return": 0.10,
        "max_drawdown": -0.35,
        "annual_one_way_turnover": 8.0,
    }
    baseline_full = {"total_return": 0.15}
    baseline_oos = {"total_return": 0.05}
    return candidate_full, candidate_oos, baseline_full, baseline_oos


def test_evaluate_release_accepts_only_when_all_six_checks_pass() -> None:
    result = evaluate_release(*_passing_release_metrics())

    assert result == {
        "status": "accepted",
        "checks": {check: True for check in RELEASE_CHECKS},
        "failed_checks": [],
    }


@pytest.mark.parametrize(
    ("mapping_index", "field", "value", "failed_check"),
    [
        (0, "total_return", 0.15, "full_return_improved"),
        (0, "max_drawdown", -0.350_001, "full_drawdown_within_limit"),
        (0, "annual_one_way_turnover", 8.000_001, "full_turnover_within_limit"),
        (1, "total_return", 0.05, "oos_return_improved"),
        (1, "max_drawdown", -0.350_001, "oos_drawdown_within_limit"),
        (1, "annual_one_way_turnover", 8.000_001, "oos_turnover_within_limit"),
    ],
)
def test_evaluate_release_rejects_each_failed_check(
    mapping_index: int,
    field: str,
    value: float,
    failed_check: str,
) -> None:
    metrics = _passing_release_metrics()
    metrics[mapping_index][field] = value

    result = evaluate_release(*metrics)

    assert result["status"] == "rejected"
    assert result["failed_checks"] == [failed_check]
    assert result["checks"][failed_check] is False


def test_evaluate_release_reports_failures_in_fixed_check_order() -> None:
    candidate_full, candidate_oos, baseline_full, baseline_oos = (
        _passing_release_metrics()
    )
    candidate_full.update(
        total_return=baseline_full["total_return"],
        max_drawdown=-0.36,
        annual_one_way_turnover=9.0,
    )
    candidate_oos.update(
        total_return=baseline_oos["total_return"],
        max_drawdown=-0.36,
        annual_one_way_turnover=9.0,
    )

    result = evaluate_release(
        candidate_full,
        candidate_oos,
        baseline_full,
        baseline_oos,
    )

    assert result["failed_checks"] == RELEASE_CHECKS


REQUIRED_RELEASE_METRICS = [
    (0, "total_return", "full_return_improved"),
    (0, "max_drawdown", "full_drawdown_within_limit"),
    (0, "annual_one_way_turnover", "full_turnover_within_limit"),
    (1, "total_return", "oos_return_improved"),
    (1, "max_drawdown", "oos_drawdown_within_limit"),
    (1, "annual_one_way_turnover", "oos_turnover_within_limit"),
    (2, "total_return", "full_return_improved"),
    (3, "total_return", "oos_return_improved"),
]


@pytest.mark.parametrize(
    ("mapping_index", "field", "failed_check"),
    REQUIRED_RELEASE_METRICS,
)
@pytest.mark.parametrize("invalid_value", [np.nan, np.inf, -np.inf])
def test_evaluate_release_rejects_every_non_finite_metric(
    mapping_index: int,
    field: str,
    failed_check: str,
    invalid_value: float,
) -> None:
    metrics = _passing_release_metrics()
    metrics[mapping_index][field] = invalid_value

    result = evaluate_release(*metrics)

    assert result["status"] == "rejected"
    assert result["checks"][failed_check] is False
    assert result["failed_checks"] == [failed_check]


@pytest.mark.parametrize(
    ("mapping_index", "field"),
    product(
        (2, 3),
        ("max_drawdown", "annual_one_way_turnover"),
    ),
)
@pytest.mark.parametrize("invalid_value", [np.nan, np.inf, -np.inf])
def test_evaluate_release_ignores_non_contract_baseline_risk_metrics(
    mapping_index: int,
    field: str,
    invalid_value: float,
) -> None:
    metrics = _passing_release_metrics()
    metrics[mapping_index][field] = invalid_value

    result = evaluate_release(*metrics)

    assert result["status"] == "accepted"
    assert result["failed_checks"] == []


@pytest.mark.parametrize(
    ("mapping_index", "field"),
    [(mapping_index, field) for mapping_index, field, _ in REQUIRED_RELEASE_METRICS],
)
def test_evaluate_release_propagates_every_missing_numeric_key(
    mapping_index: int,
    field: str,
) -> None:
    metrics = _passing_release_metrics()
    del metrics[mapping_index][field]

    with pytest.raises(KeyError, match=field):
        evaluate_release(*metrics)


@pytest.mark.parametrize(
    ("mapping_index", "field"),
    product(
        (2, 3),
        ("max_drawdown", "annual_one_way_turnover"),
    ),
)
def test_evaluate_release_does_not_require_baseline_risk_metrics(
    mapping_index: int,
    field: str,
) -> None:
    metrics = _passing_release_metrics()

    assert field not in metrics[mapping_index]
    assert evaluate_release(*metrics)["status"] == "accepted"
