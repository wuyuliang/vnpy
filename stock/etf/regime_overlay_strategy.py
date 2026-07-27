"""EMA-capped portfolio overlay driven by causal regime forecasts."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import pandas as pd


WEIGHT_LEVELS = {0.0, 0.5, 1.0}


@dataclass(frozen=True)
class OverlayTransition:
    """Auditable result of one daily target-weight decision."""

    regime_cap: float
    entry_allowed: bool
    risk_ceiling: float
    risk_reduced_weight: float
    proposed_weight: float
    target_weight: float
    risk_increase_blocked: bool
    primary_reason: str
    ema_reduction_applied: bool
    regime_reduction_applied: bool
    score_1d_reduction_applied: bool


def _validate_score(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        raise ValueError(f"{name} must be numeric")
    score = float(value)
    if not isfinite(score) or not -3.0 <= score <= 3.0:
        raise ValueError(f"{name} must be finite and within [-3, 3]")
    return score


def _validate_weight(value: object, name: str) -> float:
    if isinstance(value, bool) or value not in WEIGHT_LEVELS:
        raise ValueError(f"{name} must be 0.0, 0.5, or 1.0")
    return float(value)


def map_regime_cap(score_3d: object) -> tuple[float, bool]:
    """Return the three-day position cap and flat-entry permission."""
    score = _validate_score(score_3d, "score_3d")
    if score <= -2.0:
        return 0.0, False
    if score <= 1.0:
        return 0.5, False
    if score < 2.0:
        return 0.5, True
    return 1.0, True


def transition_overlay_weight(
    *,
    current_weight: object,
    ema_target_weight: object,
    score_1d: object,
    score_3d: object,
    days_since_transition: int | None,
    cooldown_days: int,
) -> OverlayTransition:
    """Apply risk ceilings, one-day pacing, and final-position cooldown."""
    current = _validate_weight(current_weight, "current_weight")
    ema_target = _validate_weight(ema_target_weight, "ema_target_weight")
    one_day_score = _validate_score(score_1d, "score_1d")
    regime_cap, entry_allowed = map_regime_cap(score_3d)
    if (
        days_since_transition is not None
        and (
            type(days_since_transition) is not int
            or days_since_transition < 0
        )
    ):
        raise ValueError("days_since_transition must be a non-negative int or None")
    if type(cooldown_days) is not int or cooldown_days <= 0:
        raise ValueError("cooldown_days must be a positive int")

    risk_ceiling = min(ema_target, regime_cap)
    risk_reduced = min(current, risk_ceiling)
    ema_reduction = ema_target < current
    regime_reduction = regime_cap < current
    score_reduction = False

    if one_day_score <= -2.0:
        proposed = max(0.0, risk_reduced - 0.5)
        score_reduction = proposed < risk_reduced
        reason = "score_1d_reduce" if score_reduction else "hold"
    elif one_day_score > 1.0 and risk_reduced == current:
        can_enter = current > 0.0 or entry_allowed
        proposed = (
            min(current + 0.5, risk_ceiling) if can_enter else current
        )
        reason = "score_1d_increase" if proposed > current else "hold"
    else:
        proposed = risk_reduced
        reason = "hold"

    if proposed < current and not score_reduction:
        if ema_reduction and regime_reduction:
            reason = "ema_and_regime_reduce"
        elif ema_reduction:
            reason = "ema_target_reduce"
        else:
            reason = "regime_cap_reduce"

    blocked = bool(
        proposed > current
        and days_since_transition is not None
        and days_since_transition < cooldown_days
    )
    target = current if blocked else proposed
    if blocked:
        reason = "cooldown_block"

    return OverlayTransition(
        regime_cap=regime_cap,
        entry_allowed=entry_allowed,
        risk_ceiling=risk_ceiling,
        risk_reduced_weight=risk_reduced,
        proposed_weight=proposed,
        target_weight=target,
        risk_increase_blocked=blocked,
        primary_reason=reason,
        ema_reduction_applied=ema_reduction and risk_reduced < current,
        regime_reduction_applied=regime_reduction and risk_reduced < current,
        score_1d_reduction_applied=score_reduction,
    )


def _normalize_naive_dates(values: pd.Series, name: str) -> pd.Series:
    dates = pd.to_datetime(values, errors="raise")
    if dates.isna().any():
        raise ValueError(f"{name} contains missing dates")
    if dates.dt.tz is not None:
        raise ValueError(f"{name} must be timezone-naive")
    return dates.dt.normalize()


def align_regime_predictions(
    predictions: pd.DataFrame,
    bars: pd.DataFrame,
) -> pd.DataFrame:
    """Align close-time 1d/3d forecasts to the next actual symbol bar."""
    prediction_columns = {
        "symbol",
        "feature_asof_date",
        "max_feature_source_date",
        "prediction_horizon",
        "score",
        "state",
    }
    bar_columns = {"symbol", "datetime"}
    missing_predictions = prediction_columns - set(predictions.columns)
    missing_bars = bar_columns - set(bars.columns)
    if missing_predictions:
        raise ValueError(
            f"predictions missing columns: {sorted(missing_predictions)}"
        )
    if missing_bars:
        raise ValueError(f"bars missing columns: {sorted(missing_bars)}")

    forecast = predictions.loc[:, sorted(prediction_columns)].copy()
    forecast["feature_asof_date"] = _normalize_naive_dates(
        forecast["feature_asof_date"],
        "feature_asof_date",
    )
    forecast["max_feature_source_date"] = _normalize_naive_dates(
        forecast["max_feature_source_date"],
        "max_feature_source_date",
    )
    if not forecast["prediction_horizon"].isin(["1d", "3d"]).all():
        raise ValueError("prediction_horizon must be 1d or 3d")
    keys = ["symbol", "feature_asof_date"]
    if forecast.duplicated([*keys, "prediction_horizon"]).any():
        raise ValueError("predictions contain duplicate horizon rows")
    horizon_sets = forecast.groupby(keys, sort=False)[
        "prediction_horizon"
    ].agg(set)
    if not horizon_sets.map(lambda values: values == {"1d", "3d"}).all():
        raise ValueError("each feature date requires both 1d and 3d predictions")
    if (
        forecast["max_feature_source_date"] > forecast["feature_asof_date"]
    ).any():
        raise ValueError("predictions contain future feature source dates")

    base = (
        forecast.groupby(keys, as_index=False, sort=False)
        .agg(max_feature_source_date=("max_feature_source_date", "max"))
    )
    for horizon in ("1d", "3d"):
        horizon_rows = forecast.loc[
            forecast["prediction_horizon"].eq(horizon),
            [*keys, "score", "state"],
        ].rename(
            columns={
                "score": f"score_{horizon}",
                "state": f"state_{horizon}",
            }
        )
        base = base.merge(
            horizon_rows,
            on=keys,
            how="inner",
            validate="one_to_one",
        )

    actual_bars = bars.loc[:, ["symbol", "datetime"]].copy()
    actual_bars["datetime"] = _normalize_naive_dates(
        actual_bars["datetime"],
        "datetime",
    )
    if actual_bars.duplicated(["symbol", "datetime"]).any():
        raise ValueError("bars contain duplicate symbol/date rows")
    actual_bars = actual_bars.sort_values(
        ["symbol", "datetime"],
        ignore_index=True,
    )
    actual_bars["regime_feature_asof_date"] = actual_bars.groupby(
        "symbol",
        sort=False,
    )["datetime"].shift(1)
    aligned = actual_bars.merge(
        base.rename(
            columns={"feature_asof_date": "regime_feature_asof_date"}
        ),
        on=["symbol", "regime_feature_asof_date"],
        how="inner",
        validate="one_to_one",
    ).rename(columns={"datetime": "execution_date"})
    if (
        aligned["max_feature_source_date"]
        > aligned["regime_feature_asof_date"]
    ).any() or (
        aligned["regime_feature_asof_date"] >= aligned["execution_date"]
    ).any():
        raise ValueError("aligned predictions violate causal date ordering")
    return aligned.sort_values(
        ["symbol", "execution_date"],
        ignore_index=True,
    )
