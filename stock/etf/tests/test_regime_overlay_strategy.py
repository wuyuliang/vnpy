import math

import numpy as np
import pandas as pd
import pytest

from stock.etf.ema_trend_allocation_strategy import TrendAllocationConfig
from stock.etf.regime_overlay_strategy import (
    align_regime_predictions,
    build_buy_hold_equity,
    calculate_period_table,
    calculate_continuous_metrics,
    calendar_periods,
    execute_overlay_signals,
    map_regime_cap,
    run_regime_overlay_backtest,
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

    row = aligned.loc[aligned["execution_date"].eq(pd.Timestamp("2017-08-14"))].iloc[0]
    assert row["regime_feature_asof_date"] == pd.Timestamp("2017-08-11")
    assert row["score_1d"] == pytest.approx(1.1)
    assert row["score_3d"] == pytest.approx(2.1)
    assert row["execution_date"] != pd.Timestamp("2017-08-31")
    assert (
        aligned["max_feature_source_date"] <= aligned["regime_feature_asof_date"]
    ).all()
    assert (aligned["regime_feature_asof_date"] < aligned["execution_date"]).all()


def test_predictions_follow_suspension_gap_and_drop_last_feature_date() -> None:
    aligned = align_regime_predictions(
        _alignment_predictions(),
        _alignment_bars(),
    )

    august_16 = aligned.loc[
        aligned["execution_date"].eq(pd.Timestamp("2017-08-16"))
    ].iloc[0]
    assert august_16["regime_feature_asof_date"] == pd.Timestamp("2017-08-14")
    assert pd.Timestamp("2017-08-16") not in set(aligned["regime_feature_asof_date"])
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


def _manual_execution_signals() -> pd.DataFrame:
    dates = pd.bdate_range("2017-08-14", periods=3)
    return pd.DataFrame(
        {
            "symbol": ["159915.SZ"] * 3,
            "datetime": dates,
            "open": [10.0, 10.2, 10.4],
            "high": [10.1, 10.3, 10.5],
            "low": [9.9, 10.1, 10.3],
            "close": [10.0, 10.2, 10.4],
            "volume": [1_000_000.0] * 3,
            "regime_feature_asof_date": dates - pd.offsets.BDay(1),
            "max_feature_source_date": dates - pd.offsets.BDay(1),
            "score_1d": [2.5, 0.0, 0.0],
            "state_1d": ["趋势向上", "无趋势", "无趋势"],
            "score_3d": [1.5, 1.5, 1.5],
            "state_3d": ["震荡向上"] * 3,
            "ema_target_weight": [0.5, 0.5, 0.5],
        }
    )


def test_execute_overlay_signals_reuses_open_cost_and_lot_accounting() -> None:
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        confirmation_days=1,
    )

    result = execute_overlay_signals(_manual_execution_signals(), config)

    first_trade = result.trades.iloc[0]
    assert first_trade["datetime"] == pd.Timestamp("2017-08-14")
    assert first_trade["side"] == "buy"
    assert first_trade["quantity"] == 5_000
    assert first_trade["quantity"] % 100 == 0
    assert first_trade["raw_price"] == pytest.approx(10.0)
    assert first_trade["fill_price"] == pytest.approx(10.005)
    assert first_trade["commission"] == pytest.approx(15.0075)
    expected_cash = 100_000.0 - 5_000 * 10.005 - 15.0075
    expected_equity = expected_cash + 5_000 * 10.0
    assert result.equity_curve.iloc[0]["cash"] == pytest.approx(expected_cash)
    assert result.equity_curve.iloc[0]["equity"] == pytest.approx(expected_equity)
    assert result.signals.iloc[0]["target_weight"] == pytest.approx(0.5)


def test_execute_overlay_signals_does_not_force_final_liquidation() -> None:
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        confirmation_days=1,
    )

    result = execute_overlay_signals(_manual_execution_signals(), config)

    assert result.trades["side"].tolist() == ["buy"]
    assert result.summary["is_open"] is True
    assert result.summary["target_weight"] == pytest.approx(0.5)


def test_unaffordable_first_lot_does_not_advance_target_state() -> None:
    config = TrendAllocationConfig(
        initial_capital=1_000.0,
        confirmation_days=1,
    )
    signals = _manual_execution_signals().iloc[:1].copy()

    result = execute_overlay_signals(signals, config)

    assert result.trades.empty
    assert result.signals.iloc[0]["target_weight"] == pytest.approx(0.0)
    assert result.signals.iloc[0]["primary_reason"] == "insufficient_cash"
    assert result.summary["target_weight"] == pytest.approx(0.0)


def _rising_bars(periods: int = 100) -> pd.DataFrame:
    index = np.arange(periods, dtype=float)
    close = 10.0 + index * 0.1
    return pd.DataFrame(
        {
            "symbol": ["159915.SZ"] * periods,
            "datetime": pd.bdate_range("2017-05-01", periods=periods),
            "open": close,
            "high": close + 0.1,
            "low": close - 0.1,
            "close": close,
            "volume": [1_000_000.0] * periods,
        }
    )


def _bullish_predictions(bars: pd.DataFrame) -> pd.DataFrame:
    rows: list[dict[str, object]] = []
    for date in bars["datetime"]:
        for horizon in ("1d", "3d"):
            rows.append(
                {
                    "symbol": "159915.SZ",
                    "feature_asof_date": date,
                    "max_feature_source_date": date,
                    "prediction_horizon": horizon,
                    "prediction_for_date": date + pd.Timedelta(days=10),
                    "score": 2.5,
                    "state": "趋势向上",
                }
            )
    return pd.DataFrame(rows)


def test_full_backtest_keeps_independent_ema_target_as_hard_ceiling() -> None:
    bars = _rising_bars()
    start = bars.iloc[50]["datetime"]
    end = bars.iloc[80]["datetime"]
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        confirmation_days=1,
    )

    result = run_regime_overlay_backtest(
        bars,
        _bullish_predictions(bars),
        start=start,
        end=end,
        config=config,
    )

    expected_ema = result.ema_result.signals.set_index("datetime")["target_weight"]
    actual = result.signals.set_index("datetime")
    pd.testing.assert_series_equal(
        actual["ema_target_weight"],
        expected_ema,
        check_names=False,
    )
    assert (actual["target_weight"] <= actual["ema_target_weight"]).all()
    assert (actual["target_weight"] <= actual["regime_cap"]).all()
    assert set(actual["target_weight"]) <= {0.0, 0.5, 1.0}
    assert result.equity_curve.iloc[0]["datetime"] == pd.Timestamp(start)
    assert result.equity_curve.iloc[-1]["datetime"] == pd.Timestamp(end)


def test_continuous_metrics_include_first_day_change_and_initial_peak() -> None:
    equity = pd.DataFrame(
        {
            "datetime": pd.bdate_range("2026-01-05", periods=3),
            "equity": [90.0, 99.0, 80.0],
            "daily_return": [-0.1, 0.1, 80.0 / 99.0 - 1.0],
        }
    )
    trades = pd.DataFrame(
        columns=["datetime", "fill_price", "quantity", "commission", "slippage_cost"]
    )

    metrics = calculate_continuous_metrics(
        equity,
        trades,
        initial_equity=100.0,
        start="2026-01-05",
        end="2026-01-07",
    )

    assert metrics["total_return"] == pytest.approx(-0.2)
    assert metrics["max_drawdown"] == pytest.approx(-0.2)
    assert metrics["max_drawdown_start"] == "2026-01-05"
    assert metrics["max_drawdown_end"] == "2026-01-07"


def _cross_half_equity() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                ["2017-12-29", "2018-01-02", "2018-06-29", "2018-07-02"]
            ),
            "cash": [100.0, 100.0, 100.0, 100.0],
            "market_value": [10.0, 21.0, 33.1, 19.79],
            "equity": [110.0, 121.0, 133.1, 119.79],
            "daily_return": [0.1, 0.1, 0.1, -0.1],
            "drawdown": [0.0, 0.0, 0.0, -0.1],
        }
    )


def _cross_half_trades() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2018-01-02", "2018-07-02"]),
            "fill_price": [10.0, 10.0],
            "quantity": [100, 100],
            "commission": [5.0, 5.0],
            "slippage_cost": [0.5, 0.5],
        }
    )


def test_half_year_periods_keep_partial_first_and_last_segments() -> None:
    periods = calendar_periods(
        pd.Timestamp("2017-12-29"),
        pd.Timestamp("2018-07-02"),
        frequency="half_year",
    )

    assert [period.label for period in periods] == [
        "2017-H2",
        "2018-H1",
        "2018-H2",
    ]
    assert [period.is_partial_period for period in periods] == [
        True,
        False,
        True,
    ]


def test_half_year_metrics_inherit_previous_close_equity() -> None:
    periods = calendar_periods(
        pd.Timestamp("2017-12-29"),
        pd.Timestamp("2018-07-02"),
        frequency="half_year",
    )

    metrics = calculate_period_table(
        _cross_half_equity(),
        _cross_half_trades(),
        initial_capital=100.0,
        periods=periods,
        strategy="regime_overlay",
    ).set_index("period")

    assert metrics.loc["2017-H2", "initial_equity"] == pytest.approx(100.0)
    assert metrics.loc["2017-H2", "total_return"] == pytest.approx(0.1)
    assert metrics.loc["2018-H1", "initial_equity"] == pytest.approx(110.0)
    assert metrics.loc["2018-H1", "total_return"] == pytest.approx(0.21)
    assert metrics.loc["2018-H2", "initial_equity"] == pytest.approx(133.1)
    assert metrics.loc["2018-H2", "total_return"] == pytest.approx(-0.1)
    assert metrics.loc["2018-H1", "trade_count"] == 1
    assert metrics.loc["2018-H2", "trade_count"] == 1


def test_half_year_periods_cover_each_equity_date_once() -> None:
    equity = _cross_half_equity()
    periods = calendar_periods(
        equity["datetime"].min(),
        equity["datetime"].max(),
        frequency="half_year",
    )

    membership = pd.Series(0, index=equity.index)
    for period in periods:
        membership += (
            equity["datetime"]
            .between(
                period.start,
                period.end,
                inclusive="both",
            )
            .astype(int)
        )

    assert membership.eq(1).all()


def test_buy_hold_equity_starts_at_initial_capital() -> None:
    bars = pd.DataFrame(
        {
            "datetime": pd.bdate_range("2026-01-05", periods=3),
            "close": [10.0, 11.0, 9.0],
        }
    )

    equity = build_buy_hold_equity(bars, initial_capital=100_000.0)

    assert equity["equity"].tolist() == pytest.approx([100_000.0, 110_000.0, 90_000.0])
    assert equity["daily_return"].tolist() == pytest.approx([0.0, 0.1, -2 / 11])
    assert equity["drawdown"].tolist() == pytest.approx([0.0, 0.0, -2 / 11])
