import numpy as np
import pandas as pd
import pytest

from stock.etf.regime_evaluation import (
    attach_realized_labels,
    evaluate_regime_predictions,
)
from stock.etf.regime_rules import RegimeState


def _realized(states: list[str], dates: list[str]) -> pd.DataFrame:
    score_by_state = {
        RegimeState.TREND_DOWN.value: -2.5,
        RegimeState.OSCILLATING_DOWN.value: -1.5,
        RegimeState.NO_TREND.value: 0.0,
        RegimeState.OSCILLATING_UP.value: 1.5,
        RegimeState.TREND_UP.value: 2.5,
    }
    return pd.DataFrame(
        {
            "symbol": ["159915.SZ"] * len(states),
            "datetime": pd.to_datetime(dates),
            "realized_score": [score_by_state[state] for state in states],
            "realized_state": states,
        }
    )


def _prediction_rows(feature_dates: list[str]) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for date in pd.to_datetime(feature_dates):
        for horizon in ("1d", "3d"):
            rows.append(
                {
                    "symbol": "159915.SZ",
                    "feature_asof_date": date,
                    "prediction_horizon": horizon,
                    "prediction_for_date": date + pd.Timedelta(days=10),
                    "max_feature_source_date": date,
                    "current_realized_score": 0.0,
                    "current_realized_state": RegimeState.NO_TREND.value,
                    "score": 0.0,
                    "state": RegimeState.NO_TREND.value,
                }
            )
    return pd.DataFrame(rows)


def test_labels_follow_actual_symbol_rows_not_calendar_days() -> None:
    realized = _realized(
        [
            RegimeState.NO_TREND.value,
            RegimeState.OSCILLATING_UP.value,
            RegimeState.TREND_UP.value,
            RegimeState.OSCILLATING_DOWN.value,
            RegimeState.TREND_DOWN.value,
        ],
        ["2026-01-02", "2026-01-05", "2026-01-08", "2026-01-09", "2026-01-12"],
    )
    predictions = _prediction_rows(["2026-01-02"])

    labeled = attach_realized_labels(predictions, realized)
    by_horizon = labeled.set_index("prediction_horizon")

    assert by_horizon.loc["1d", "label_date"] == pd.Timestamp("2026-01-05")
    assert by_horizon.loc["3d", "label_date"] == pd.Timestamp("2026-01-09")
    assert (
        by_horizon.loc["3d", "realized_state"]
        == RegimeState.OSCILLATING_DOWN.value
    )


def test_latest_predictions_keep_unknown_labels() -> None:
    realized = _realized(
        [RegimeState.NO_TREND.value] * 4,
        ["2026-01-02", "2026-01-05", "2026-01-06", "2026-01-07"],
    )
    predictions = _prediction_rows(["2026-01-06", "2026-01-07"])

    labeled = attach_realized_labels(predictions, realized)
    latest = labeled["feature_asof_date"].eq(pd.Timestamp("2026-01-07"))

    assert labeled.loc[latest, "label_date"].isna().all()
    assert labeled.loc[latest, "realized_score"].isna().all()
    assert labeled.loc[latest, "realized_state"].isna().all()


def _metric_fixture() -> pd.DataFrame:
    actual_states = [
        RegimeState.TREND_DOWN.value,
        RegimeState.OSCILLATING_DOWN.value,
        RegimeState.NO_TREND.value,
        RegimeState.OSCILLATING_UP.value,
        RegimeState.TREND_UP.value,
    ]
    predicted_states = [
        RegimeState.TREND_DOWN.value,
        RegimeState.NO_TREND.value,
        RegimeState.NO_TREND.value,
        RegimeState.TREND_UP.value,
        RegimeState.TREND_UP.value,
    ]
    actual_scores = [-2.5, -1.5, 0.0, 1.5, 2.5]
    predicted_scores = [-2.5, 0.0, 0.0, 2.5, 2.5]
    feature_dates = pd.bdate_range("2021-01-04", periods=5)
    label_dates = feature_dates + pd.offsets.BDay(1)
    return pd.DataFrame(
        {
            "symbol": ["159915.SZ"] * 5,
            "feature_asof_date": feature_dates,
            "prediction_horizon": ["1d"] * 5,
            "prediction_for_date": label_dates,
            "max_feature_source_date": feature_dates,
            "current_realized_score": actual_scores,
            "current_realized_state": actual_states,
            "score": predicted_scores,
            "state": predicted_states,
            "label_date": label_dates,
            "realized_score": actual_scores,
            "realized_state": actual_states,
            "first_transition_date": pd.Series(pd.NaT, index=range(5)),
            "first_transition_offset": np.nan,
        }
    )


def test_metrics_match_hand_calculation() -> None:
    evaluation = evaluate_regime_predictions(_metric_fixture())

    metrics = evaluation.accuracy_by_period
    rule = metrics.loc[
        metrics["period"].eq("full")
        & metrics["horizon"].eq("1d")
        & metrics["model"].eq("rule")
    ].iloc[0]

    assert rule["sample_count"] == 5
    assert rule["exact_accuracy"] == pytest.approx(0.6)
    assert rule["balanced_accuracy"] == pytest.approx(0.6)
    assert rule["direction_accuracy"] == pytest.approx(0.8)
    assert rule["structure_accuracy"] == pytest.approx(0.6)
    assert rule["score_mae"] == pytest.approx(0.5)
    expected_spearman = pd.Series([-2.5, 0.0, 0.0, 2.5, 2.5]).corr(
        pd.Series([-2.5, -1.5, 0.0, 1.5, 2.5]),
        method="spearman",
    )
    assert rule["spearman"] == pytest.approx(expected_spearman)
    confusion = evaluation.confusion_matrix_1d
    full_rule = confusion.loc[
        confusion["period"].eq("full") & confusion["model"].eq("rule")
    ]
    assert full_rule["count"].sum() == 5


def test_period_requires_feature_and_label_inside_boundary() -> None:
    fixture = _metric_fixture().iloc[[0]].copy()
    fixture["feature_asof_date"] = pd.Timestamp("2022-12-30")
    fixture["label_date"] = pd.Timestamp("2023-01-03")

    evaluation = evaluate_regime_predictions(fixture)
    metrics = evaluation.accuracy_by_period

    assert not metrics["period"].isin(["development", "validation"]).any()
    assert metrics["period"].eq("full").any()


def test_constant_scores_produce_missing_spearman() -> None:
    fixture = _metric_fixture()
    fixture["score"] = 0.0

    evaluation = evaluate_regime_predictions(fixture)
    rule = evaluation.accuracy_by_period.loc[
        evaluation.accuracy_by_period["period"].eq("full")
        & evaluation.accuracy_by_period["horizon"].eq("1d")
        & evaluation.accuracy_by_period["model"].eq("rule")
    ].iloc[0]

    assert pd.isna(rule["spearman"])


def test_majority_baseline_uses_development_period_only() -> None:
    development = _metric_fixture()
    development["state"] = RegimeState.NO_TREND.value
    development["realized_state"] = [
        RegimeState.NO_TREND.value,
        RegimeState.NO_TREND.value,
        RegimeState.NO_TREND.value,
        RegimeState.OSCILLATING_UP.value,
        RegimeState.TREND_UP.value,
    ]
    oos = development.copy()
    oos["feature_asof_date"] = pd.bdate_range("2025-01-02", periods=5)
    oos["label_date"] = oos["feature_asof_date"] + pd.offsets.BDay(1)
    oos["realized_state"] = RegimeState.TREND_UP.value

    evaluation = evaluate_regime_predictions(
        pd.concat([development, oos], ignore_index=True)
    )

    assert (
        evaluation.summary["majority_state_by_horizon"]["1d"]
        == RegimeState.NO_TREND.value
    )


def test_transition_matching_does_not_double_count_actual_switch() -> None:
    realized = _realized(
        [
            RegimeState.NO_TREND.value,
            RegimeState.NO_TREND.value,
            RegimeState.NO_TREND.value,
            RegimeState.TREND_UP.value,
            RegimeState.TREND_UP.value,
            RegimeState.TREND_UP.value,
        ],
        [
            "2021-01-04",
            "2021-01-05",
            "2021-01-06",
            "2021-01-07",
            "2021-01-08",
            "2021-01-11",
        ],
    )
    predictions = _prediction_rows(["2021-01-04", "2021-01-05", "2021-01-06"])
    predictions = predictions.loc[predictions["prediction_horizon"].eq("3d")].copy()
    predictions["score"] = 2.5
    predictions["state"] = RegimeState.TREND_UP.value
    labeled = attach_realized_labels(predictions, realized)

    evaluation = evaluate_regime_predictions(labeled)
    transitions = evaluation.transition_accuracy
    full_rule = transitions.loc[
        transitions["period"].eq("full")
        & transitions["horizon"].eq("3d")
        & transitions["model"].eq("rule")
    ].iloc[0]

    assert full_rule["actual_transition_count"] == 1
    assert full_rule["matched_transition_count"] == 1
    assert full_rule["predicted_transition_count"] == 3
