import warnings
from dataclasses import FrozenInstanceError

import numpy as np
import pandas as pd
import pytest

from stock.etf.regime_overlay_charts import (
    STATE_COLORS,
    StateBand,
    aggregate_weekly_state_tracks,
    prepare_daily_state_tracks,
    state_band,
)


EXPECTED_COLORS = {
    "趋势向下": "#f3c1bc",
    "震荡向下": "#f3dfb1",
    "无趋势": "#e2e8f0",
    "震荡向上": "#c8e8d4",
    "趋势向上": "#9fd8b5",
}


def _signals() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": [
                "2026-07-16 15:00",
                "2026-07-06 09:30",
                "2026-07-10 15:00",
                "2026-07-08 15:00",
                "2026-07-13 15:00",
            ],
            "score_1d": [2.8, -2.8, 0.8, -1.8, 1.8],
            "score_3d": [2.5, -2.5, 0.5, -1.5, 1.5],
            "state_3d": [
                "趋势向上",
                "趋势向下",
                "无趋势",
                "震荡向下",
                "震荡向上",
            ],
            "ignored": ["e", "a", "c", "b", "d"],
        }
    )


def test_state_band_is_frozen_and_uses_documented_colors() -> None:
    assert STATE_COLORS == EXPECTED_COLORS

    band = state_band(2.0)

    assert band == StateBand(state="趋势向上", color="#9fd8b5")
    with pytest.raises(FrozenInstanceError):
        band.color = "#000000"


@pytest.mark.parametrize(
    ("score", "expected_state"),
    [
        (-3.0, "趋势向下"),
        (-2.0, "趋势向下"),
        (-1.999, "震荡向下"),
        (-1.0, "无趋势"),
        (0.0, "无趋势"),
        (1.0, "无趋势"),
        (1.001, "震荡向上"),
        (1.999, "震荡向上"),
        (2.0, "趋势向上"),
        (3.0, "趋势向上"),
    ],
)
def test_state_band_uses_strategy_boundaries(
    score: float,
    expected_state: str,
) -> None:
    band = state_band(score)

    assert band.state == expected_state
    assert band.color == EXPECTED_COLORS[expected_state]


@pytest.mark.parametrize(
    "score",
    [
        True,
        False,
        "2",
        None,
        np.nan,
        np.inf,
        -np.inf,
        -3.001,
        3.001,
    ],
)
def test_state_band_rejects_invalid_scores(score: object) -> None:
    with pytest.raises(ValueError, match="score"):
        state_band(score)


def test_prepare_daily_state_tracks_normalizes_sorts_and_does_not_fill() -> None:
    signals = _signals()
    original = signals.copy(deep=True)

    daily = prepare_daily_state_tracks(signals)

    assert daily.columns.tolist() == [
        "datetime",
        "score_1d",
        "score_3d",
        "state_3d",
        "state_color",
    ]
    assert daily["datetime"].tolist() == [
        pd.Timestamp("2026-07-06"),
        pd.Timestamp("2026-07-08"),
        pd.Timestamp("2026-07-10"),
        pd.Timestamp("2026-07-13"),
        pd.Timestamp("2026-07-16"),
    ]
    assert daily["state_color"].tolist() == [
        "#f3c1bc",
        "#f3dfb1",
        "#e2e8f0",
        "#c8e8d4",
        "#9fd8b5",
    ]
    assert len(daily) == len(signals)
    pd.testing.assert_frame_equal(signals, original)


def test_prepare_daily_state_tracks_parses_mixed_date_formats() -> None:
    signals = pd.DataFrame(
        {
            "datetime": ["07/07/2026 15:00", "2026-07-06 09:30"],
            "score_1d": [1.5, -1.5],
            "score_3d": [1.5, -1.5],
            "state_3d": ["震荡向上", "震荡向下"],
        }
    )

    daily = prepare_daily_state_tracks(signals)

    assert daily["datetime"].tolist() == [
        pd.Timestamp("2026-07-06"),
        pd.Timestamp("2026-07-07"),
    ]


@pytest.mark.parametrize(
    "column",
    ["datetime", "score_1d", "score_3d", "state_3d"],
)
def test_prepare_daily_state_tracks_requires_columns(column: str) -> None:
    with pytest.raises(ValueError, match="missing columns"):
        prepare_daily_state_tracks(_signals().drop(columns=column))


@pytest.mark.parametrize("invalid_date", ["not-a-date", None])
def test_prepare_daily_state_tracks_rejects_invalid_dates(
    invalid_date: object,
) -> None:
    signals = _signals()
    signals.loc[0, "datetime"] = invalid_date

    with warnings.catch_warnings():
        warnings.simplefilter("error", UserWarning)
        with pytest.raises(ValueError, match="datetime"):
            prepare_daily_state_tracks(signals)


def test_prepare_daily_state_tracks_rejects_duplicate_normalized_dates() -> None:
    signals = _signals()
    signals.loc[1, "datetime"] = "2026-07-16 09:30"

    with pytest.raises(ValueError, match="duplicate"):
        prepare_daily_state_tracks(signals)


def test_prepare_daily_state_tracks_rejects_timezone_aware_dates() -> None:
    signals = _signals()
    signals["datetime"] = pd.to_datetime(signals["datetime"]).dt.tz_localize(
        "Asia/Shanghai"
    )

    with pytest.raises(ValueError, match="timezone"):
        prepare_daily_state_tracks(signals)


@pytest.mark.parametrize("column", ["score_1d", "score_3d"])
@pytest.mark.parametrize(
    "invalid_score",
    [True, "1", np.nan, np.inf, -np.inf, -3.01, 3.01],
)
def test_prepare_daily_state_tracks_rejects_invalid_scores(
    column: str,
    invalid_score: object,
) -> None:
    signals = _signals()
    signals[column] = [invalid_score, *signals[column].iloc[1:].tolist()]

    with pytest.raises(ValueError, match=column):
        prepare_daily_state_tracks(signals)


def test_prepare_daily_state_tracks_rejects_inconsistent_state() -> None:
    signals = _signals()
    signals.loc[0, "state_3d"] = "趋势向下"

    with pytest.raises(ValueError, match="state_3d"):
        prepare_daily_state_tracks(signals)


def test_aggregate_weekly_state_tracks_uses_last_actual_row_without_fill() -> None:
    daily = prepare_daily_state_tracks(_signals())

    weekly = aggregate_weekly_state_tracks(daily)

    assert weekly["datetime"].tolist() == [
        pd.Timestamp("2026-07-10"),
        pd.Timestamp("2026-07-17"),
    ]
    pd.testing.assert_series_equal(
        weekly.iloc[0].drop(labels="datetime"),
        daily.loc[daily["datetime"].eq("2026-07-10")].iloc[0].drop(labels="datetime"),
        check_names=False,
    )
    pd.testing.assert_series_equal(
        weekly.iloc[1].drop(labels="datetime"),
        daily.loc[daily["datetime"].eq("2026-07-16")].iloc[0].drop(labels="datetime"),
        check_names=False,
    )


def test_aggregate_weekly_state_tracks_omits_fully_missing_week() -> None:
    signals = pd.DataFrame(
        {
            "datetime": ["2026-07-10", "2026-07-24"],
            "score_1d": [-2.5, 2.5],
            "score_3d": [-2.5, 2.5],
            "state_3d": ["趋势向下", "趋势向上"],
        }
    )

    weekly = aggregate_weekly_state_tracks(prepare_daily_state_tracks(signals))

    assert weekly["datetime"].tolist() == [
        pd.Timestamp("2026-07-10"),
        pd.Timestamp("2026-07-24"),
    ]
    assert not weekly.isna().any(axis=None)


def test_mutating_future_week_does_not_change_prior_week() -> None:
    original = aggregate_weekly_state_tracks(prepare_daily_state_tracks(_signals()))
    mutated_signals = _signals()
    future = pd.to_datetime(mutated_signals["datetime"]).dt.normalize().gt("2026-07-10")
    mutated_signals.loc[future, "score_1d"] = -2.9
    mutated_signals.loc[future, "score_3d"] = -2.8
    mutated_signals.loc[future, "state_3d"] = "趋势向下"

    mutated = aggregate_weekly_state_tracks(prepare_daily_state_tracks(mutated_signals))

    pd.testing.assert_frame_equal(
        original.iloc[[0]].reset_index(drop=True),
        mutated.iloc[[0]].reset_index(drop=True),
        check_exact=True,
    )
