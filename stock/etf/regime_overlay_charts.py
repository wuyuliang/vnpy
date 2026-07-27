"""Prepare daily and weekly state tracks for regime overlay charts."""

from __future__ import annotations

from dataclasses import dataclass
from math import isfinite

import numpy as np
import pandas as pd

from stock.etf.regime_rules import score_to_state


@dataclass(frozen=True)
class StateBand:
    """A market regime label and its chart color."""

    state: str
    color: str


STATE_COLORS: dict[str, str] = {
    "趋势向下": "#f3c1bc",
    "震荡向下": "#f3dfb1",
    "无趋势": "#e2e8f0",
    "震荡向上": "#c8e8d4",
    "趋势向上": "#9fd8b5",
}

TRACK_COLUMNS = [
    "datetime",
    "score_1d",
    "score_3d",
    "state_3d",
    "state_color",
]


def _validate_score(value: object, name: str) -> float:
    if isinstance(value, bool) or not isinstance(
        value,
        (int, float, np.integer, np.floating),
    ):
        raise ValueError(f"{name} must be numeric")
    score = float(value)
    if not isfinite(score) or not -3.0 <= score <= 3.0:
        raise ValueError(f"{name} must be finite and within [-3, 3]")
    return score


def state_band(score: object) -> StateBand:
    """Return the documented regime and color for one valid score."""
    state = score_to_state(_validate_score(score, "score")).value
    return StateBand(state=state, color=STATE_COLORS[state])


def prepare_daily_state_tracks(signals: pd.DataFrame) -> pd.DataFrame:
    """Validate and normalize daily regime tracks without filling dates."""
    required = ["datetime", "score_1d", "score_3d", "state_3d"]
    missing = set(required) - set(signals.columns)
    if missing:
        raise ValueError(f"state signals missing columns: {sorted(missing)}")

    frame = signals.loc[:, required].copy()
    try:
        dates = pd.to_datetime(frame["datetime"], errors="raise")
    except (TypeError, ValueError) as exc:
        raise ValueError("datetime must contain parseable dates") from exc
    if dates.isna().any():
        raise ValueError("datetime must not be missing")
    if dates.dt.tz is not None:
        raise ValueError("datetime must be timezone-naive")
    frame["datetime"] = dates.dt.normalize()
    if frame["datetime"].duplicated().any():
        raise ValueError("state signals contain duplicate dates")

    for column in ("score_1d", "score_3d"):
        frame[column] = frame[column].map(
            lambda value, name=column: _validate_score(value, name)
        )

    expected_states = frame["score_3d"].map(lambda value: state_band(value).state)
    if not frame["state_3d"].astype(str).eq(expected_states).all():
        raise ValueError("state_3d does not match score_3d")
    frame["state_color"] = frame["score_3d"].map(lambda value: state_band(value).color)
    return frame.loc[:, TRACK_COLUMNS].sort_values("datetime", ignore_index=True)


def aggregate_weekly_state_tracks(daily: pd.DataFrame) -> pd.DataFrame:
    """Select each Friday-ending week's last actual daily state row."""
    frame = prepare_daily_state_tracks(daily)
    if frame.empty:
        return frame
    return (
        frame.set_index("datetime")
        .resample("W-FRI")
        .last()
        .dropna(subset=["score_1d", "score_3d"])
        .reset_index()
        .loc[:, TRACK_COLUMNS]
    )
