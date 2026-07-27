import math

import pandas as pd
import pytest

from stock.etf.regime_overlay_strategy import (
    align_regime_predictions,
    map_regime_cap,
    transition_overlay_weight,
)


@pytest.mark.parametrize(
    ("score", "cap", "entry_allowed"),
    [
        (-3.0, 0.0, False),
        (-2.0, 0.0, False),
        (-1.999, 0.5, False),
        (-1.0, 0.5, False),
        (0.0, 0.5, False),
        (1.0, 0.5, False),
        (1.001, 0.5, True),
        (1.999, 0.5, True),
        (2.0, 1.0, True),
        (3.0, 1.0, True),
    ],
)
def test_map_regime_cap_uses_documented_boundaries(
    score: float,
    cap: float,
    entry_allowed: bool,
) -> None:
    assert map_regime_cap(score) == (cap, entry_allowed)


@pytest.mark.parametrize(
    "score",
    [-3.01, 3.01, math.nan, math.inf, -math.inf, True, "2"],
)
def test_map_regime_cap_rejects_invalid_scores(score: object) -> None:
    with pytest.raises(ValueError, match="score_3d"):
        map_regime_cap(score)


def test_ema_target_reduction_executes_immediately() -> None:
    transition = transition_overlay_weight(
        current_weight=1.0,
        ema_target_weight=0.5,
        score_1d=0.0,
        score_3d=2.5,
        days_since_transition=0,
        cooldown_days=10,
    )

    assert transition.risk_ceiling == 0.5
    assert transition.target_weight == 0.5
    assert transition.ema_reduction_applied is True
    assert transition.risk_increase_blocked is False


def test_regime_cap_zero_exits_immediately() -> None:
    transition = transition_overlay_weight(
        current_weight=1.0,
        ema_target_weight=1.0,
        score_1d=0.0,
        score_3d=-2.0,
        days_since_transition=0,
        cooldown_days=10,
    )

    assert transition.regime_cap == 0.0
    assert transition.target_weight == 0.0
    assert transition.regime_reduction_applied is True


def test_one_day_downtrend_reduces_after_risk_ceiling() -> None:
    transition = transition_overlay_weight(
        current_weight=1.0,
        ema_target_weight=0.5,
        score_1d=-2.0,
        score_3d=2.5,
        days_since_transition=0,
        cooldown_days=10,
    )

    assert transition.risk_reduced_weight == 0.5
    assert transition.proposed_weight == 0.0
    assert transition.target_weight == 0.0
    assert transition.score_1d_reduction_applied is True


def test_three_day_entry_gate_blocks_opening() -> None:
    transition = transition_overlay_weight(
        current_weight=0.0,
        ema_target_weight=1.0,
        score_1d=1.1,
        score_3d=1.0,
        days_since_transition=None,
        cooldown_days=10,
    )

    assert transition.regime_cap == 0.5
    assert transition.entry_allowed is False
    assert transition.target_weight == 0.0


def test_one_day_uptrend_opens_only_one_level() -> None:
    transition = transition_overlay_weight(
        current_weight=0.0,
        ema_target_weight=1.0,
        score_1d=2.5,
        score_3d=2.5,
        days_since_transition=None,
        cooldown_days=10,
    )

    assert transition.proposed_weight == 0.5
    assert transition.target_weight == 0.5


@pytest.mark.parametrize(
    ("days_since_transition", "expected", "blocked"),
    [
        (9, 0.5, True),
        (10, 1.0, False),
    ],
)
def test_risk_increase_requires_ten_complete_trading_days(
    days_since_transition: int,
    expected: float,
    blocked: bool,
) -> None:
    transition = transition_overlay_weight(
        current_weight=0.5,
        ema_target_weight=1.0,
        score_1d=2.5,
        score_3d=2.5,
        days_since_transition=days_since_transition,
        cooldown_days=10,
    )

    assert transition.target_weight == expected
    assert transition.risk_increase_blocked is blocked


def test_risk_reduction_ignores_cooldown() -> None:
    transition = transition_overlay_weight(
        current_weight=1.0,
        ema_target_weight=1.0,
        score_1d=-2.5,
        score_3d=2.5,
        days_since_transition=0,
        cooldown_days=10,
    )

    assert transition.target_weight == 0.5
    assert transition.risk_increase_blocked is False


@pytest.mark.parametrize(
    "kwargs",
    [
        {"current_weight": 0.25},
        {"ema_target_weight": 0.75},
        {"score_1d": math.nan},
        {"score_1d": 3.1},
        {"days_since_transition": -1},
        {"cooldown_days": 0},
        {"cooldown_days": 10.0},
    ],
)
def test_transition_rejects_invalid_inputs(kwargs: dict[str, object]) -> None:
    arguments: dict[str, object] = {
        "current_weight": 0.5,
        "ema_target_weight": 1.0,
        "score_1d": 0.0,
        "score_3d": 2.0,
        "days_since_transition": None,
        "cooldown_days": 10,
    }
    arguments.update(kwargs)

    with pytest.raises(ValueError):
        transition_overlay_weight(**arguments)


def _alignment_bars() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["159915.SZ"] * 4,
            "datetime": pd.to_datetime(
                ["2017-08-10", "2017-08-11", "2017-08-14", "2017-08-16"]
            ),
        }
    )


def _alignment_predictions() -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for ordinal, date in enumerate(_alignment_bars()["datetime"]):
        for horizon, score in (("1d", 0.1 + ordinal), ("3d", 1.1 + ordinal)):
            rows.append(
                {
                    "symbol": "159915.SZ",
                    "feature_asof_date": date,
                    "max_feature_source_date": date,
                    "prediction_horizon": horizon,
                    "prediction_for_date": date + pd.Timedelta(days=20),
                    "score": score,
                    "state": f"{horizon}-state-{ordinal}",
                }
            )
    return pd.DataFrame(rows)


def test_predictions_align_to_next_actual_bar_not_target_date() -> None:
    aligned = align_regime_predictions(
        _alignment_predictions(),
        _alignment_bars(),
    )

    row = aligned.loc[
        aligned["execution_date"].eq(pd.Timestamp("2017-08-14"))
    ].iloc[0]
    assert row["regime_feature_asof_date"] == pd.Timestamp("2017-08-11")
    assert row["score_1d"] == pytest.approx(1.1)
    assert row["score_3d"] == pytest.approx(2.1)
    assert row["execution_date"] != pd.Timestamp("2017-08-31")
    assert (
        aligned["max_feature_source_date"]
        <= aligned["regime_feature_asof_date"]
    ).all()
    assert (
        aligned["regime_feature_asof_date"] < aligned["execution_date"]
    ).all()


def test_predictions_follow_suspension_gap_and_drop_last_feature_date() -> None:
    aligned = align_regime_predictions(
        _alignment_predictions(),
        _alignment_bars(),
    )

    august_16 = aligned.loc[
        aligned["execution_date"].eq(pd.Timestamp("2017-08-16"))
    ].iloc[0]
    assert august_16["regime_feature_asof_date"] == pd.Timestamp("2017-08-14")
    assert pd.Timestamp("2017-08-16") not in set(
        aligned["regime_feature_asof_date"]
    )
    assert len(aligned) == 3


def test_alignment_requires_both_horizons_per_feature_date() -> None:
    predictions = _alignment_predictions()
    missing = predictions.drop(
        predictions.index[
            predictions["feature_asof_date"].eq(pd.Timestamp("2017-08-11"))
            & predictions["prediction_horizon"].eq("3d")
        ]
    )

    with pytest.raises(ValueError, match="both 1d and 3d"):
        align_regime_predictions(missing, _alignment_bars())


def test_mutating_execution_day_and_future_predictions_does_not_change_open() -> None:
    predictions = _alignment_predictions()
    original = align_regime_predictions(predictions, _alignment_bars())
    changed = predictions.copy()
    future = changed["feature_asof_date"] >= pd.Timestamp("2017-08-14")
    changed.loc[future, "score"] = -2.999
    mutated = align_regime_predictions(changed, _alignment_bars())

    original_row = original.loc[
        original["execution_date"].eq(pd.Timestamp("2017-08-14"))
    ].reset_index(drop=True)
    mutated_row = mutated.loc[
        mutated["execution_date"].eq(pd.Timestamp("2017-08-14"))
    ].reset_index(drop=True)
    pd.testing.assert_frame_equal(original_row, mutated_row, check_exact=True)
