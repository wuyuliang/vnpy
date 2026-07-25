"""Label and evaluate causal market regime predictions."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite
from typing import Any

import numpy as np
import pandas as pd

from .regime_rules import RegimeState

STATE_ORDER = [state.value for state in RegimeState]
STATE_SCORE = {
    RegimeState.TREND_DOWN.value: -2.5,
    RegimeState.OSCILLATING_DOWN.value: -1.5,
    RegimeState.NO_TREND.value: 0.0,
    RegimeState.OSCILLATING_UP.value: 1.5,
    RegimeState.TREND_UP.value: 2.5,
}
STATE_DIRECTION = {
    RegimeState.TREND_DOWN.value: "down",
    RegimeState.OSCILLATING_DOWN.value: "down",
    RegimeState.NO_TREND.value: "neutral",
    RegimeState.OSCILLATING_UP.value: "up",
    RegimeState.TREND_UP.value: "up",
}
STATE_STRUCTURE = {
    RegimeState.TREND_DOWN.value: "trend",
    RegimeState.OSCILLATING_DOWN.value: "oscillating",
    RegimeState.NO_TREND.value: "no_trend",
    RegimeState.OSCILLATING_UP.value: "oscillating",
    RegimeState.TREND_UP.value: "trend",
}
HORIZON_ROWS = {"1d": 1, "3d": 3}


@dataclass(frozen=True)
class RegimeEvaluation:
    """All tabular and summary artifacts produced by an evaluation."""

    accuracy_by_period: pd.DataFrame
    confusion_matrix_1d: pd.DataFrame
    confusion_matrix_3d: pd.DataFrame
    transition_accuracy: pd.DataFrame
    summary: dict[str, Any]


def _normalized_dates(values: pd.Series, field: str) -> pd.Series:
    dates = pd.to_datetime(values, errors="raise")
    if dates.isna().any():
        raise ValueError(f"{field} contains missing dates")
    if dates.dt.tz is not None:
        raise ValueError(f"{field} must be timezone-naive")
    return dates.dt.normalize()


def _validate_state_values(values: pd.Series, field: str) -> None:
    invalid = values.dropna().loc[~values.dropna().isin(STATE_ORDER)]
    if not invalid.empty:
        raise ValueError(f"{field} contains unknown states")


def attach_realized_labels(
    predictions: pd.DataFrame,
    realized: pd.DataFrame,
) -> pd.DataFrame:
    """Append future labels by actual per-symbol trading rows."""
    prediction_required = {
        "symbol",
        "feature_asof_date",
        "prediction_horizon",
        "prediction_for_date",
        "max_feature_source_date",
        "score",
        "state",
    }
    realized_required = {
        "symbol",
        "datetime",
        "realized_score",
        "realized_state",
    }
    missing_predictions = prediction_required - set(predictions.columns)
    missing_realized = realized_required - set(realized.columns)
    if missing_predictions:
        raise ValueError(f"predictions missing columns: {sorted(missing_predictions)}")
    if missing_realized:
        raise ValueError(f"realized data missing columns: {sorted(missing_realized)}")

    output = predictions.copy()
    for column in (
        "feature_asof_date",
        "prediction_for_date",
        "max_feature_source_date",
    ):
        output[column] = _normalized_dates(output[column], column)
    if not output["prediction_horizon"].isin(HORIZON_ROWS).all():
        raise ValueError("prediction_horizon must be 1d or 3d")
    if output.duplicated(["symbol", "feature_asof_date", "prediction_horizon"]).any():
        raise ValueError("predictions contain duplicate symbol/date/horizon rows")
    if (output["max_feature_source_date"] > output["feature_asof_date"]).any():
        raise ValueError("prediction features contain future source dates")
    _validate_state_values(output["state"], "state")

    answers = realized.copy()
    answers["datetime"] = _normalized_dates(answers["datetime"], "datetime")
    if answers.duplicated(["symbol", "datetime"]).any():
        raise ValueError("realized data contains duplicate symbol/date rows")
    answers["realized_score"] = pd.to_numeric(
        answers["realized_score"],
        errors="coerce",
    )
    finite_scores = (
        answers["realized_score"].dropna().map(lambda value: isfinite(float(value)))
    )
    if not finite_scores.all():
        raise ValueError("realized_score must be finite when present")
    _validate_state_values(answers["realized_state"], "realized_state")
    answers = answers.sort_values(["symbol", "datetime"], ignore_index=True)

    output["label_date"] = pd.NaT
    output["realized_score"] = np.nan
    output["realized_state"] = pd.Series(
        pd.NA,
        index=output.index,
        dtype="string",
    )
    output["first_transition_date"] = pd.NaT
    output["first_transition_offset"] = np.nan

    grouped_answers = {
        str(symbol): frame.reset_index(drop=True)
        for symbol, frame in answers.groupby("symbol", sort=False)
    }
    for row_index, prediction in output.iterrows():
        symbol = str(prediction["symbol"])
        if symbol not in grouped_answers:
            raise ValueError(f"realized data missing symbol: {symbol}")
        symbol_answers = grouped_answers[symbol]
        positions = symbol_answers.index[
            symbol_answers["datetime"].eq(prediction["feature_asof_date"])
        ]
        if len(positions) != 1:
            raise ValueError(
                f"feature date missing from realized data: "
                f"{symbol} {prediction['feature_asof_date']}"
            )
        feature_position = int(positions[0])
        horizon = HORIZON_ROWS[str(prediction["prediction_horizon"])]
        target_position = feature_position + horizon
        if target_position >= len(symbol_answers):
            continue

        target = symbol_answers.iloc[target_position]
        output.at[row_index, "label_date"] = target["datetime"]
        output.at[row_index, "realized_score"] = target["realized_score"]
        output.at[row_index, "realized_state"] = target["realized_state"]

        path = symbol_answers.iloc[feature_position : target_position + 1]
        previous_states = path["realized_state"].shift(1)
        transition_mask = path["realized_state"].ne(previous_states)
        transition_mask.iloc[0] = False
        if transition_mask.any():
            first_position = int(np.flatnonzero(transition_mask.to_numpy())[0])
            transition = path.iloc[first_position]
            output.at[row_index, "first_transition_date"] = transition["datetime"]
            output.at[row_index, "first_transition_offset"] = float(first_position)

    if (
        output["label_date"].notna()
        & (output["feature_asof_date"] >= output["label_date"])
    ).any():
        raise ValueError("labels must occur after feature dates")
    return output


def _balanced_accuracy(actual: pd.Series, predicted: pd.Series) -> float:
    recalls = []
    for state in actual.unique():
        mask = actual.eq(state)
        recalls.append(float(predicted.loc[mask].eq(state).mean()))
    return float(np.mean(recalls))


def _safe_spearman(predicted: pd.Series, actual: pd.Series) -> float | None:
    if predicted.nunique(dropna=True) < 2 or actual.nunique(dropna=True) < 2:
        return None
    correlation = predicted.corr(actual, method="spearman")
    if pd.isna(correlation) or not isfinite(float(correlation)):
        return None
    return float(correlation)


def _metric_row(
    frame: pd.DataFrame,
    predicted_state: pd.Series,
    predicted_score: pd.Series,
) -> dict[str, object]:
    actual_state = frame["realized_state"].astype(str)
    actual_score = frame["realized_score"].astype(float)
    predicted_state = predicted_state.astype(str)
    predicted_score = predicted_score.astype(float)
    actual_direction = actual_state.map(STATE_DIRECTION)
    predicted_direction = predicted_state.map(STATE_DIRECTION)
    actual_structure = actual_state.map(STATE_STRUCTURE)
    predicted_structure = predicted_state.map(STATE_STRUCTURE)
    return {
        "sample_count": len(frame),
        "observed_class_count": int(actual_state.nunique()),
        "exact_accuracy": float(predicted_state.eq(actual_state).mean()),
        "balanced_accuracy": _balanced_accuracy(actual_state, predicted_state),
        "direction_accuracy": float(predicted_direction.eq(actual_direction).mean()),
        "structure_accuracy": float(predicted_structure.eq(actual_structure).mean()),
        "score_mae": float((predicted_score - actual_score).abs().mean()),
        "spearman": _safe_spearman(predicted_score, actual_score),
    }


def _periods(frame: pd.DataFrame) -> list[tuple[str, pd.Timestamp, pd.Timestamp]]:
    full_start = pd.Timestamp(frame["feature_asof_date"].min())
    full_end = pd.Timestamp(frame["label_date"].max())
    periods = [
        ("full", full_start, full_end),
        ("development", full_start, pd.Timestamp("2022-12-30")),
        (
            "validation",
            pd.Timestamp("2023-01-01"),
            pd.Timestamp("2024-12-31"),
        ),
        ("oos", pd.Timestamp("2025-01-01"), full_end),
    ]
    for year in range(full_start.year, full_end.year + 1):
        periods.append(
            (
                f"year_{year}",
                pd.Timestamp(year=year, month=1, day=1),
                pd.Timestamp(year=year, month=12, day=31),
            )
        )
    return periods


def _period_slice(
    frame: pd.DataFrame,
    start: pd.Timestamp,
    end: pd.Timestamp,
) -> pd.DataFrame:
    return frame.loc[
        frame["feature_asof_date"].between(start, end, inclusive="both")
        & frame["label_date"].between(start, end, inclusive="both")
    ].copy()


def _model_predictions(
    frame: pd.DataFrame,
    model: str,
    majority_state: str | None,
) -> tuple[pd.Series, pd.Series] | None:
    if model == "rule":
        return frame["state"], frame["score"]
    if model == "persistence":
        return frame["current_realized_state"], frame["current_realized_score"]
    if majority_state is None:
        return None
    return (
        pd.Series(majority_state, index=frame.index),
        pd.Series(STATE_SCORE[majority_state], index=frame.index),
    )


def _confusion_rows(
    frame: pd.DataFrame,
    predicted: pd.Series,
    period: str,
    model: str,
) -> list[dict[str, object]]:
    rows = []
    for actual_state in STATE_ORDER:
        for predicted_state in STATE_ORDER:
            rows.append(
                {
                    "period": period,
                    "model": model,
                    "actual_state": actual_state,
                    "predicted_state": predicted_state,
                    "count": int(
                        (
                            frame["realized_state"].eq(actual_state)
                            & predicted.eq(predicted_state)
                        ).sum()
                    ),
                }
            )
    return rows


def _transition_row(
    frame: pd.DataFrame,
    predicted_state: pd.Series,
) -> dict[str, object]:
    actual_dates = sorted(
        pd.Timestamp(date) for date in frame["first_transition_date"].dropna().unique()
    )
    predicted_mask = predicted_state.ne(frame["current_realized_state"])
    predicted_rows = frame.loc[predicted_mask].sort_values("feature_asof_date")
    used_actual_dates: set[pd.Timestamp] = set()
    lead_offsets: list[float] = []
    lag_offsets: list[float] = []
    for _, prediction in predicted_rows.iterrows():
        transition_date = prediction["first_transition_date"]
        if pd.isna(transition_date):
            continue
        transition_date = pd.Timestamp(transition_date)
        if transition_date in used_actual_dates:
            continue
        used_actual_dates.add(transition_date)
        horizon = HORIZON_ROWS[str(prediction["prediction_horizon"])]
        offset = float(prediction["first_transition_offset"])
        lead_offsets.append(max(float(horizon) - offset, 0.0))
        lag_offsets.append(max(offset - float(horizon), 0.0))

    actual_count = len(actual_dates)
    predicted_count = int(predicted_mask.sum())
    matched_count = len(used_actual_dates)
    return {
        "actual_transition_count": actual_count,
        "predicted_transition_count": predicted_count,
        "matched_transition_count": matched_count,
        "transition_recall": (
            matched_count / actual_count if actual_count > 0 else None
        ),
        "false_positive_rate": (
            (predicted_count - matched_count) / predicted_count
            if predicted_count > 0
            else None
        ),
        "mean_lead_trading_days": (
            float(np.mean(lead_offsets)) if lead_offsets else None
        ),
        "mean_lag_trading_days": (float(np.mean(lag_offsets)) if lag_offsets else None),
    }


def evaluate_regime_predictions(
    labeled: pd.DataFrame,
    development_end: str = "2022-12-30",
    validation_start: str = "2023-01-01",
    validation_end: str = "2024-12-31",
    oos_start: str = "2025-01-01",
) -> RegimeEvaluation:
    """Evaluate rule forecasts and preregistered baselines by period."""
    required = {
        "symbol",
        "feature_asof_date",
        "prediction_horizon",
        "score",
        "state",
        "current_realized_score",
        "current_realized_state",
        "label_date",
        "realized_score",
        "realized_state",
    }
    missing = required - set(labeled.columns)
    if missing:
        raise ValueError(f"labeled predictions missing columns: {sorted(missing)}")
    frame = labeled.copy()
    frame["feature_asof_date"] = _normalized_dates(
        frame["feature_asof_date"],
        "feature_asof_date",
    )
    frame["label_date"] = pd.to_datetime(frame["label_date"], errors="coerce")
    frame = frame.loc[
        frame["label_date"].notna()
        & frame["realized_score"].notna()
        & frame["realized_state"].notna()
    ].copy()
    if frame.empty:
        raise ValueError("no labeled predictions are available for evaluation")
    frame["label_date"] = _normalized_dates(frame["label_date"], "label_date")
    if (frame["feature_asof_date"] >= frame["label_date"]).any():
        raise ValueError("label dates must be after feature dates")
    if not frame["prediction_horizon"].isin(HORIZON_ROWS).all():
        raise ValueError("prediction_horizon must be 1d or 3d")
    for field in ("state", "current_realized_state", "realized_state"):
        _validate_state_values(frame[field], field)
    for field in ("score", "current_realized_score", "realized_score"):
        frame[field] = pd.to_numeric(frame[field], errors="coerce")
        if frame[field].isna().any() or not np.isfinite(frame[field]).all():
            raise ValueError(f"{field} must contain finite values")
    if "first_transition_date" not in frame.columns:
        frame["first_transition_date"] = pd.NaT
    else:
        frame["first_transition_date"] = pd.to_datetime(
            frame["first_transition_date"],
            errors="coerce",
        )
    if "first_transition_offset" not in frame.columns:
        frame["first_transition_offset"] = np.nan

    development_end_date = pd.Timestamp(development_end)
    validation_start_date = pd.Timestamp(validation_start)
    validation_end_date = pd.Timestamp(validation_end)
    oos_start_date = pd.Timestamp(oos_start)
    if not (
        development_end_date
        < validation_start_date
        <= validation_end_date
        < oos_start_date
    ):
        raise ValueError("evaluation period boundaries must be ordered")

    majority_by_horizon: dict[str, str | None] = {}
    for horizon in HORIZON_ROWS:
        development = frame.loc[
            frame["prediction_horizon"].eq(horizon)
            & frame["feature_asof_date"].le(development_end_date)
            & frame["label_date"].le(development_end_date)
        ]
        if development.empty:
            majority_by_horizon[horizon] = None
            continue
        counts = development["realized_state"].value_counts()
        max_count = int(counts.max())
        majority_by_horizon[horizon] = next(
            state for state in STATE_ORDER if int(counts.get(state, 0)) == max_count
        )

    period_definitions = _periods(frame)
    period_definitions[1] = (
        "development",
        period_definitions[1][1],
        development_end_date,
    )
    period_definitions[2] = (
        "validation",
        validation_start_date,
        validation_end_date,
    )
    period_definitions[3] = (
        "oos",
        oos_start_date,
        period_definitions[3][2],
    )

    accuracy_rows: list[dict[str, object]] = []
    confusion_by_horizon: dict[str, list[dict[str, object]]] = {
        "1d": [],
        "3d": [],
    }
    transition_rows: list[dict[str, object]] = []
    for period_name, start, end in period_definitions:
        period_frame = _period_slice(frame, start, end)
        for horizon in HORIZON_ROWS:
            horizon_frame = period_frame.loc[
                period_frame["prediction_horizon"].eq(horizon)
            ].copy()
            if horizon_frame.empty:
                continue
            for model in ("rule", "persistence", "majority"):
                model_values = _model_predictions(
                    horizon_frame,
                    model,
                    majority_by_horizon[horizon],
                )
                if model_values is None:
                    continue
                predicted_state, predicted_score = model_values
                accuracy_rows.append(
                    {
                        "period": period_name,
                        "period_start": start,
                        "period_end": end,
                        "horizon": horizon,
                        "model": model,
                        **_metric_row(
                            horizon_frame,
                            predicted_state,
                            predicted_score,
                        ),
                    }
                )
                confusion_by_horizon[horizon].extend(
                    _confusion_rows(
                        horizon_frame,
                        predicted_state,
                        period_name,
                        model,
                    )
                )
                transition_rows.append(
                    {
                        "period": period_name,
                        "period_start": start,
                        "period_end": end,
                        "horizon": horizon,
                        "model": model,
                        **_transition_row(horizon_frame, predicted_state),
                    }
                )

    accuracy = pd.DataFrame(accuracy_rows)
    oos_comparison: dict[str, dict[str, float] | None] = {}
    eligible = True
    for horizon in HORIZON_ROWS:
        rows = accuracy.loc[
            accuracy["period"].eq("oos")
            & accuracy["horizon"].eq(horizon)
            & accuracy["model"].isin(["rule", "persistence"])
        ]
        values = rows.set_index("model")["balanced_accuracy"]
        if not {"rule", "persistence"} <= set(values.index):
            oos_comparison[horizon] = None
            eligible = False
            continue
        rule_value = float(values["rule"])
        persistence_value = float(values["persistence"])
        oos_comparison[horizon] = {
            "rule_balanced_accuracy": rule_value,
            "persistence_balanced_accuracy": persistence_value,
            "difference": rule_value - persistence_value,
        }
        eligible = eligible and rule_value > persistence_value

    summary = {
        "majority_state_by_horizon": majority_by_horizon,
        "oos_balanced_accuracy_comparison": oos_comparison,
        "eligible_for_phase_two": bool(eligible),
        "eligibility_rule": (
            "Both 1d and 3d OOS balanced accuracy must exceed persistence."
        ),
        "labeled_sample_count": int(len(frame)),
    }
    return RegimeEvaluation(
        accuracy_by_period=accuracy,
        confusion_matrix_1d=pd.DataFrame(confusion_by_horizon["1d"]),
        confusion_matrix_3d=pd.DataFrame(confusion_by_horizon["3d"]),
        transition_accuracy=pd.DataFrame(transition_rows),
        summary=summary,
    )
