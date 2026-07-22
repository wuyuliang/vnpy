import numpy as np
import pandas as pd
import pytest

from stock.etf import ema_trend_allocation_strategy as trend_strategy
from stock.etf.ema_trend_allocation_strategy import (
    TrendAllocationConfig,
    build_trend_signals,
    transition_target_weight,
)


BAR_COLUMNS = ["symbol", "datetime", "open", "high", "low", "close", "volume"]
CONDITION_COLUMNS = ["enter_half", "enter_full", "reduce_half", "exit_flat"]


def _bars(closes: list[float], opens: list[float] | None = None) -> pd.DataFrame:
    actual_opens = opens or closes
    return pd.DataFrame(
        {
            "symbol": ["159915.SZ"] * len(closes),
            "datetime": pd.bdate_range("2026-01-02", periods=len(closes)),
            "open": actual_opens,
            "high": [
                max(open_, close) + 0.2
                for open_, close in zip(actual_opens, closes, strict=True)
            ],
            "low": [
                min(open_, close) - 0.2
                for open_, close in zip(actual_opens, closes, strict=True)
            ],
            "close": closes,
            "volume": [1_000.0] * len(closes),
        }
    )


def _rising_bars(periods: int = 40) -> pd.DataFrame:
    return _bars([10.0 + index * 0.5 for index in range(periods)])


def _manual_signals(
    conditions: list[dict[str, bool]],
    *,
    opens: list[float] | None = None,
    closes: list[float] | None = None,
) -> pd.DataFrame:
    periods = len(conditions)
    actual_opens = opens or [10.0] * periods
    actual_closes = closes or actual_opens
    signals = _bars(actual_closes, actual_opens)
    for column in CONDITION_COLUMNS:
        signals[column] = [condition.get(column, False) for condition in conditions]
    signals["target_weight"] = 0.0
    signals["action"] = "flat"
    return signals


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"symbol": " "}, "symbol"),
        ({"initial_capital": 0.0}, "initial_capital"),
        ({"initial_capital": -1.0}, "initial_capital"),
        ({"lot_size": 50}, "lot_size"),
        ({"lot_size": 100.0}, "lot_size"),
        ({"commission_rate": -0.0001}, "cost"),
        ({"min_commission": -1.0}, "cost"),
        ({"slippage_rate": -0.0001}, "cost"),
        ({"slow_period": 25}, "slow_period"),
        ({"confirmation_days": 3}, "confirmation_days"),
        ({"slope_lookback": 4}, "slope_lookback"),
        ({"risk_increase_cooldown_days": 0}, "risk_increase_cooldown_days"),
        ({"risk_increase_cooldown_days": -1}, "risk_increase_cooldown_days"),
    ],
)
def test_config_rejects_invalid_values(
    kwargs: dict[str, object],
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        TrendAllocationConfig(**kwargs)


@pytest.mark.parametrize("symbol", [None, 159915])
def test_config_rejects_non_string_symbols(symbol: object) -> None:
    with pytest.raises(ValueError, match="symbol"):
        TrendAllocationConfig(symbol=symbol)


@pytest.mark.parametrize(
    ("field", "value"),
    [
        (field, value)
        for field in (
            "initial_capital",
            "commission_rate",
            "min_commission",
            "slippage_rate",
        )
        for value in (float("nan"), float("inf"), float("-inf"))
    ],
)
def test_config_rejects_non_finite_numbers(field: str, value: float) -> None:
    with pytest.raises(ValueError, match=field):
        TrendAllocationConfig(**{field: value})


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("slow_period", 20.0),
        ("slow_period", True),
        ("confirmation_days", 2.0),
        ("confirmation_days", True),
        ("slope_lookback", 3.0),
        ("slope_lookback", True),
        ("risk_increase_cooldown_days", 10.0),
        ("risk_increase_cooldown_days", True),
    ],
)
def test_config_requires_python_int_period_parameters(
    field: str,
    value: object,
) -> None:
    with pytest.raises(ValueError, match=field):
        TrendAllocationConfig(**{field: value})


def test_config_defaults_match_preregistered_baseline() -> None:
    config = TrendAllocationConfig()

    assert config == TrendAllocationConfig(
        symbol="159915.SZ",
        initial_capital=1_000_000.0,
        lot_size=100,
        commission_rate=0.0003,
        min_commission=5.0,
        slippage_rate=0.0005,
        slow_period=20,
        confirmation_days=2,
        slope_lookback=3,
        risk_increase_cooldown_days=10,
    )


@pytest.mark.parametrize(
    (
        "current",
        "exit_flat",
        "reduce_half",
        "enter_half",
        "enter_full",
        "expected",
    ),
    [
        (0.0, True, False, True, True, 0.0),
        (0.0, False, False, True, True, 0.5),
        (0.0, False, False, False, True, 0.0),
        (0.5, False, False, False, True, 1.0),
        (0.5, False, False, False, False, 0.5),
        (1.0, False, True, True, True, 0.5),
        (1.0, True, True, True, True, 0.0),
        (1.0, False, False, False, False, 1.0),
    ],
)
def test_transition_target_weight_uses_risk_first_priority(
    current: float,
    exit_flat: bool,
    reduce_half: bool,
    enter_half: bool,
    enter_full: bool,
    expected: float,
) -> None:
    assert transition_target_weight(
        current,
        exit_flat=exit_flat,
        reduce_half=reduce_half,
        enter_half=enter_half,
        enter_full=enter_full,
    ) == pytest.approx(expected)


@pytest.mark.parametrize("current", [-0.5, 0.25, 0.75, 1.5, float("nan")])
def test_transition_target_weight_rejects_unknown_states(current: float) -> None:
    with pytest.raises(ValueError, match="current_weight"):
        transition_target_weight(
            current,
            exit_flat=False,
            reduce_half=False,
            enter_half=False,
            enter_full=False,
        )


@pytest.mark.parametrize(
    ("config", "minimum_rows", "first_ready"),
    [
        (TrendAllocationConfig(), 24, 23),
        (
            TrendAllocationConfig(slow_period=30, slope_lookback=5),
            36,
            35,
        ),
    ],
)
def test_signals_require_full_history_and_mark_first_ready_row(
    config: TrendAllocationConfig,
    minimum_rows: int,
    first_ready: int,
) -> None:
    with pytest.raises(ValueError, match=rf"at least {minimum_rows} rows"):
        build_trend_signals(_rising_bars(minimum_rows - 1), config)

    signals = build_trend_signals(_rising_bars(minimum_rows + 4), config)

    assert not signals.loc[: first_ready - 1, "ready"].any()
    assert bool(signals.loc[first_ready, "ready"])


def test_signals_expose_auditable_indicators_and_flat_initial_state() -> None:
    bars = _rising_bars()

    signals = build_trend_signals(bars, TrendAllocationConfig())

    assert signals.columns.tolist() == BAR_COLUMNS + [
        "ema5",
        "ema10",
        "ema_slow",
        "ema10_slope",
        "slow_slope",
        "previous_ema5",
        "previous_ema10",
        "previous_slow",
        "previous_ema10_slope",
        "previous_slow_slope",
        "enter_half",
        "enter_full",
        "reduce_half",
        "exit_flat",
        "ready",
        "risk_increase_blocked",
        "days_since_transition",
        "target_weight",
        "action",
    ]
    expected_ema10 = (
        bars["close"]
        .ewm(
            span=10,
            adjust=False,
            min_periods=10,
        )
        .mean()
    )
    expected_slow = (
        bars["close"]
        .ewm(
            span=20,
            adjust=False,
            min_periods=20,
        )
        .mean()
    )
    target = signals.index[-1]
    assert signals.loc[target, "ema10"] == pytest.approx(expected_ema10.loc[target])
    assert signals.loc[target, "ema_slow"] == pytest.approx(expected_slow.loc[target])
    assert signals.loc[target, "previous_ema10_slope"] == pytest.approx(
        (expected_ema10 / expected_ema10.shift(3) - 1).shift(1).loc[target]
    )
    assert signals.loc[target, "previous_slow_slope"] == pytest.approx(
        (expected_slow / expected_slow.shift(3) - 1).shift(1).loc[target]
    )
    assert signals["target_weight"].eq(0.0).all()
    assert signals["action"].eq("flat").all()
    assert not signals["risk_increase_blocked"].any()
    assert signals["days_since_transition"].isna().all()


def test_rising_trend_enables_half_and_full_entry_conditions() -> None:
    signals = build_trend_signals(_rising_bars(), TrendAllocationConfig())

    first_ready = signals.index[signals["ready"]][0]
    assert bool(signals.loc[first_ready, "enter_half"])
    assert bool(signals.loc[first_ready, "enter_full"])
    assert not bool(signals.loc[first_ready, "reduce_half"])
    assert not bool(signals.loc[first_ready, "exit_flat"])


def test_two_completed_fast_bear_days_reduce_full_position() -> None:
    closes = [10.0 + index * 0.5 for index in range(40)]
    closes.extend([28.0, 26.0, 24.0, 22.0, 20.0, 18.0])
    opens = closes.copy()
    opens[44] = 30.0

    signals = build_trend_signals(_bars(closes, opens), TrendAllocationConfig())

    assert (signals.loc[42:43, "ema5"] <= signals.loc[42:43, "ema10"]).all()
    assert (signals.loc[42:43, "ema10"] > signals.loc[42:43, "ema_slow"]).all()
    assert signals.loc[44, "open"] > signals.loc[44, "previous_ema10"]
    assert bool(signals.loc[44, "reduce_half"])
    assert not bool(signals.loc[44, "exit_flat"])


def test_open_below_previous_slow_triggers_flat_exit() -> None:
    bars = _rising_bars()
    baseline = build_trend_signals(bars, TrendAllocationConfig())
    target = 30
    broken_open = float(baseline.loc[target, "previous_slow"]) - 0.1
    bars.loc[target, "open"] = broken_open
    bars.loc[target, "high"] = max(broken_open, bars.loc[target, "close"]) + 0.2
    bars.loc[target, "low"] = min(broken_open, bars.loc[target, "close"]) - 0.2

    signals = build_trend_signals(bars, TrendAllocationConfig())

    assert signals.loc[target, "open"] < signals.loc[target, "previous_slow"]
    assert bool(signals.loc[target, "exit_flat"])


def test_current_close_cannot_change_current_open_conditions() -> None:
    bars = _rising_bars()
    target = 30
    baseline = build_trend_signals(bars, TrendAllocationConfig())
    changed = bars.copy()
    changed.loc[target, "close"] *= 1.5
    changed.loc[target, "high"] = (
        max(
            changed.loc[target, "open"],
            changed.loc[target, "close"],
        )
        + 0.2
    )
    changed.loc[target, "low"] = (
        min(
            changed.loc[target, "open"],
            changed.loc[target, "close"],
        )
        - 0.2
    )

    changed_signals = build_trend_signals(changed, TrendAllocationConfig())

    assert changed_signals.loc[target, "ema5"] != pytest.approx(
        baseline.loc[target, "ema5"]
    )
    pd.testing.assert_series_equal(
        changed_signals.loc[target, CONDITION_COLUMNS],
        baseline.loc[target, CONDITION_COLUMNS],
    )


@pytest.mark.parametrize(
    ("equity", "raw_price", "weight", "expected"),
    [
        (100_000.0, 10.0, 0.0, 0),
        (100_000.0, 10.0, 0.5, 5_000),
        (100_000.0, 10.0, 1.0, 10_000),
        (99_999.0, 12.0, 0.5, 4_100),
    ],
)
def test_target_quantity_uses_raw_price_and_whole_lots(
    equity: float,
    raw_price: float,
    weight: float,
    expected: int,
) -> None:
    assert (
        trend_strategy.calculate_target_quantity(equity, raw_price, weight, 100)
        == expected
    )


@pytest.mark.parametrize(
    ("equity", "raw_price", "weight", "lot_size", "message"),
    [
        (0.0, 10.0, 0.5, 100, "equity"),
        (-1.0, 10.0, 0.5, 100, "equity"),
        (True, 10.0, 0.5, 100, "equity"),
        (float("nan"), 10.0, 0.5, 100, "equity"),
        (float("inf"), 10.0, 0.5, 100, "equity"),
        (float("-inf"), 10.0, 0.5, 100, "equity"),
        (100_000.0, 0.0, 0.5, 100, "raw_price"),
        (100_000.0, -1.0, 0.5, 100, "raw_price"),
        (100_000.0, True, 0.5, 100, "raw_price"),
        (100_000.0, float("nan"), 0.5, 100, "raw_price"),
        (100_000.0, float("inf"), 0.5, 100, "raw_price"),
        (100_000.0, float("-inf"), 0.5, 100, "raw_price"),
        (100_000.0, 10.0, 0.25, 100, "weight"),
        (100_000.0, 10.0, True, 100, "weight"),
        (100_000.0, 10.0, float("nan"), 100, "weight"),
        (100_000.0, 10.0, float("inf"), 100, "weight"),
        (100_000.0, 10.0, float("-inf"), 100, "weight"),
        (100_000.0, 10.0, 0.5, 50, "lot_size"),
        (100_000.0, 10.0, 0.5, 100.0, "lot_size"),
    ],
)
def test_target_quantity_rejects_invalid_inputs(
    equity: float,
    raw_price: float,
    weight: float,
    lot_size: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        trend_strategy.calculate_target_quantity(equity, raw_price, weight, lot_size)


def test_execute_target_weights_trades_all_three_levels_and_records_actions() -> None:
    signals = _manual_signals(
        [
            {"enter_half": True},
            {},
            {"enter_full": True},
            {},
            {"reduce_half": True},
            {},
            {"exit_flat": True},
            {},
        ]
    )
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        commission_rate=0.0,
        min_commission=0.0,
        slippage_rate=0.0,
        risk_increase_cooldown_days=1,
    )

    result = trend_strategy.execute_target_weights(signals, config)

    assert result.trades["side"].tolist() == ["buy", "buy", "sell", "sell"]
    assert result.trades["quantity"].tolist() == [5_000, 5_000, 5_000, 5_000]
    assert (result.trades["quantity"] % 100 == 0).all()
    assert result.trades["primary_reason"].tolist() == [
        "target_weight_0_to_0.5",
        "target_weight_0.5_to_1",
        "target_weight_1_to_0.5",
        "target_weight_0.5_to_0",
    ]
    assert result.signals["target_weight"].tolist() == [
        0.5,
        0.5,
        1.0,
        1.0,
        0.5,
        0.5,
        0.0,
        0.0,
    ]
    assert result.signals["action"].tolist() == [
        "buy_to_half",
        "hold",
        "buy_to_full",
        "hold",
        "sell_to_half",
        "hold",
        "sell_to_flat",
        "hold",
    ]
    assert result.positions["quantity"].tolist() == [
        5_000,
        5_000,
        10_000,
        10_000,
        5_000,
        5_000,
    ]
    assert result.positions["target_weight"].tolist() == [
        0.5,
        0.5,
        1.0,
        1.0,
        0.5,
        0.5,
    ]
    assert result.equity_curve["cash"].min() >= 0.0


def test_unchanged_half_state_never_rebalances_after_large_open_price_moves() -> None:
    signals = _manual_signals(
        [{"enter_half": True}, {}, {}],
        opens=[10.0, 20.0, 5.0],
    )
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        commission_rate=0.0,
        min_commission=0.0,
        slippage_rate=0.0,
    )

    result = trend_strategy.execute_target_weights(signals, config)

    assert result.trades["quantity"].tolist() == [5_000]
    assert result.positions["quantity"].tolist() == [5_000, 5_000, 5_000]
    assert result.signals["target_weight"].tolist() == [0.5, 0.5, 0.5]
    assert result.signals["action"].tolist() == ["buy_to_half", "hold", "hold"]


def test_risk_increase_waits_ten_complete_sessions_despite_price_jumps() -> None:
    cooldown_opens = [20.0 if index % 2 == 0 else 5.0 for index in range(10)]
    signals = _manual_signals(
        [{"enter_half": True}, *[{"enter_full": True}] * 11],
        opens=[10.0, *cooldown_opens, 10.0],
    )
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        commission_rate=0.0,
        min_commission=0.0,
        slippage_rate=0.0,
    )

    result = trend_strategy.execute_target_weights(signals, config)

    assert result.trades["quantity"].tolist() == [5_000, 5_000]
    assert result.positions["quantity"].tolist() == [5_000] * 11 + [10_000]
    assert result.signals["target_weight"].tolist() == [0.5] * 11 + [1.0]
    assert result.signals["action"].tolist() == ["buy_to_half"] + ["hold"] * 10 + [
        "buy_to_full"
    ]
    assert result.signals["risk_increase_blocked"].tolist() == [False] + [True] * 10 + [
        False
    ]
    assert pd.isna(result.signals.iloc[0]["days_since_transition"])
    assert result.signals.iloc[1:]["days_since_transition"].tolist() == list(range(11))


def test_risk_exit_is_immediate_and_restarts_cooldown_before_reentry() -> None:
    signals = _manual_signals(
        [
            {"enter_half": True},
            {"exit_flat": True},
            *[{"enter_half": True}] * 11,
        ]
    )
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        commission_rate=0.0,
        min_commission=0.0,
        slippage_rate=0.0,
    )

    result = trend_strategy.execute_target_weights(signals, config)

    assert result.trades["side"].tolist() == ["buy", "sell", "buy"]
    assert result.signals["target_weight"].tolist() == [0.5, 0.0] + [0.0] * 10 + [0.5]
    assert result.signals["action"].tolist() == [
        "buy_to_half",
        "sell_to_flat",
        *["hold"] * 10,
        "buy_to_half",
    ]
    assert result.signals["risk_increase_blocked"].tolist() == [
        False,
        False,
        *[True] * 10,
        False,
    ]
    assert result.signals.iloc[2:]["days_since_transition"].tolist() == list(range(11))


def test_buy_quantity_steps_down_until_commission_is_affordable() -> None:
    signals = _manual_signals(
        [{"enter_half": True}, {}, {"enter_full": True}],
        opens=[1.0, 1.0, 1.0],
    )
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        commission_rate=0.0,
        min_commission=600.0,
        slippage_rate=0.0,
        risk_increase_cooldown_days=1,
    )

    result = trend_strategy.execute_target_weights(signals, config)

    assert result.trades["quantity"].tolist() == [50_000, 48_800]
    assert result.positions.iloc[-1]["quantity"] == 98_800
    assert result.equity_curve["cash"].min() == pytest.approx(0.0)


def test_target_transition_without_a_whole_lot_is_not_a_fake_trade() -> None:
    signals = _manual_signals(
        [{"enter_half": True}, {"exit_flat": True}],
        opens=[1_000.0, 1_000.0],
    )
    config = TrendAllocationConfig(
        initial_capital=10_000.0,
        commission_rate=0.0,
        min_commission=0.0,
        slippage_rate=0.0,
    )

    result = trend_strategy.execute_target_weights(signals, config)

    assert result.trades.empty
    assert result.positions.empty
    assert result.signals["target_weight"].tolist() == [0.0, 0.0]
    assert result.signals["action"].tolist() == ["flat", "hold"]


def test_half_position_advances_when_full_target_is_already_achieved() -> None:
    signals = _manual_signals(
        [{"enter_half": True}, {}, {"enter_full": True}],
        opens=[10.0, 10.0, 1_000.0],
    )
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        commission_rate=0.0,
        min_commission=0.0,
        slippage_rate=0.0,
        risk_increase_cooldown_days=1,
    )

    result = trend_strategy.execute_target_weights(signals, config)

    assert result.trades["quantity"].tolist() == [5_000]
    assert result.signals["target_weight"].tolist() == [0.5, 0.5, 1.0]
    assert result.signals["action"].tolist() == [
        "buy_to_half",
        "hold",
        "hold_full",
    ]


def test_partial_sell_allocates_entry_commission_and_keeps_half_position() -> None:
    signals = _manual_signals(
        [
            {"enter_half": True},
            {},
            {"enter_full": True},
            {"reduce_half": True},
        ],
        opens=[10.0, 10.0, 10.0, 12.0],
    )
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        commission_rate=0.001,
        min_commission=0.0,
        slippage_rate=0.0,
        risk_increase_cooldown_days=1,
    )

    result = trend_strategy.execute_target_weights(signals, config)

    assert result.trades["quantity"].tolist() == [5_000, 4_900, 5_000]
    assert result.trades["commission"].tolist() == pytest.approx([50.0, 49.0, 60.0])
    assert result.trades.iloc[-1]["realized_pnl"] == pytest.approx(9_890.0)
    assert result.positions.iloc[-1]["quantity"] == 4_900
    assert result.positions.iloc[-1]["average_price"] == pytest.approx(10.0)
    assert result.positions.iloc[-1]["target_weight"] == pytest.approx(0.5)


def test_backtest_leaves_last_position_open_and_marks_it_at_close() -> None:
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        commission_rate=0.0,
        min_commission=0.0,
        slippage_rate=0.0,
    )

    result = trend_strategy.run_trend_allocation_backtest(_rising_bars(), config)

    assert isinstance(result, trend_strategy.TrendAllocationResult)
    assert result.trades["side"].tolist() == ["buy", "buy"]
    assert result.trades["primary_reason"].tolist() == [
        "target_weight_0_to_0.5",
        "target_weight_0.5_to_1",
    ]
    last_date = result.equity_curve.iloc[-1]["datetime"]
    last_position = result.positions.iloc[-1]
    assert last_position["datetime"] == last_date
    assert result.summary["is_open"] is True
    assert result.summary["target_weight"] == pytest.approx(1.0)
    assert result.summary["final_equity"] == pytest.approx(
        last_position["market_value"] + result.equity_curve.iloc[-1]["cash"]
    )
    assert result.summary["final_equity"] == pytest.approx(
        last_position["quantity"] * _rising_bars().iloc[-1]["close"]
        + result.equity_curve.iloc[-1]["cash"]
    )
    assert {
        "symbol",
        "start_date",
        "end_date",
        "initial_equity",
        "final_equity",
        "total_return",
        "annual_return",
        "annual_volatility",
        "sharpe",
        "max_drawdown",
        "annual_one_way_turnover",
        "trade_count",
        "total_commission",
        "total_slippage_cost",
        "target_weight",
        "is_open",
    } <= result.summary.keys()


def test_summary_includes_first_day_execution_costs_from_initial_capital() -> None:
    signals = _manual_signals(
        [{"enter_half": True}],
        opens=[10.0],
        closes=[10.0],
    )
    config = TrendAllocationConfig(
        initial_capital=100_000.0,
        commission_rate=0.001,
        min_commission=0.0,
        slippage_rate=0.01,
    )

    result = trend_strategy.execute_target_weights(signals, config)

    assert result.equity_curve.iloc[0]["daily_return"] == pytest.approx(-0.005505)
    assert result.summary["final_equity"] == pytest.approx(99_449.5)
    assert result.summary["total_return"] == pytest.approx(-0.005505)
    assert result.summary["annual_return"] == pytest.approx(-0.7511966627728078)
    assert result.summary["annual_volatility"] == pytest.approx(0.0)
    assert result.summary["sharpe"] == pytest.approx(0.0)
    assert result.summary["max_drawdown"] == pytest.approx(-0.005505)
    assert result.summary["annual_one_way_turnover"] == pytest.approx(
        63.982222132841294
    )
    assert result.summary["trade_count"] == 1
    assert result.summary["total_commission"] == pytest.approx(50.5)
    assert result.summary["total_slippage_cost"] == pytest.approx(500.0)


def test_full_summary_uses_initial_capital_and_n_minus_one_comparable_returns() -> None:
    dates = pd.bdate_range("2026-01-02", periods=3)
    equity_curve = pd.DataFrame(
        {
            "datetime": dates,
            "equity": [90.0, 101.0, 99.0],
            "daily_return": [-0.1, 99.0, -99.0],
            "drawdown": [-0.1, 0.0, 99.0 / 101.0 - 1],
        }
    )
    trades = pd.DataFrame(
        columns=["fill_price", "quantity", "commission", "slippage_cost"]
    )
    returns = pd.Series([0.0, 101.0 / 90.0 - 1, 99.0 / 101.0 - 1])

    summary = trend_strategy._build_summary(
        equity_curve,
        trades,
        TrendAllocationConfig(initial_capital=100.0),
        target_weight=0.0,
        is_open=False,
    )

    assert summary["total_return"] == pytest.approx(-0.01)
    assert summary["annual_return"] == pytest.approx(0.99 ** (252 / 2) - 1)
    assert summary["annual_volatility"] == pytest.approx(
        returns.std(ddof=0) * np.sqrt(252)
    )
    assert summary["sharpe"] == pytest.approx(
        returns.mean() / returns.std(ddof=0) * np.sqrt(252)
    )
    assert summary["max_drawdown"] == pytest.approx(-0.1)


def test_period_metrics_reset_returns_drawdown_and_filter_trades() -> None:
    dates = pd.bdate_range("2026-01-02", periods=6)
    equity_curve = pd.DataFrame(
        {
            "datetime": dates,
            "equity": [100.0, 110.0, 99.0, 108.0, 90.0, 120.0],
        }
    )
    trades = pd.DataFrame(
        {
            "datetime": [dates[0], dates[2], dates[4], dates[5]],
            "fill_price": [5.0, 10.0, 12.0, 20.0],
            "quantity": [10, 10, 5, 10],
        }
    )
    period_equity = np.array([110.0, 99.0, 108.0, 90.0])
    period_returns = np.array([0.0, -0.1, 108.0 / 99.0 - 1, 90.0 / 108.0 - 1])

    metrics = trend_strategy.calculate_period_metrics(
        equity_curve,
        trades,
        dates[1],
        dates[4],
    )

    assert metrics["start_date"] == str(dates[1].date())
    assert metrics["end_date"] == str(dates[4].date())
    assert metrics["days"] == 4
    assert metrics["trading_days"] == 4
    assert metrics["total_return"] == pytest.approx(90.0 / 110.0 - 1)
    assert metrics["annual_return"] == pytest.approx((90.0 / 110.0) ** (252 / 3) - 1)
    assert metrics["annual_volatility"] == pytest.approx(
        period_returns.std(ddof=0) * np.sqrt(252)
    )
    assert metrics["sharpe"] == pytest.approx(
        period_returns.mean() / period_returns.std(ddof=0) * np.sqrt(252)
    )
    assert metrics["max_drawdown"] == pytest.approx(90.0 / 110.0 - 1)
    assert metrics["annual_one_way_turnover"] == pytest.approx(
        0.5 * (10.0 * 10 + 12.0 * 5) / period_equity.mean() / (4 / 252)
    )
    assert metrics["trade_count"] == 2


def test_period_metrics_single_point_has_zero_return_and_one_day_turnover() -> None:
    date = pd.Timestamp("2026-01-02")
    equity_curve = pd.DataFrame({"datetime": [date], "equity": [100.0]})

    metrics = trend_strategy.calculate_period_metrics(
        equity_curve,
        pd.DataFrame(columns=["datetime", "fill_price", "quantity"]),
        date,
        date,
    )

    assert metrics["days"] == 1
    assert metrics["total_return"] == pytest.approx(0.0)
    assert metrics["annual_return"] == pytest.approx(0.0)
    assert metrics["annual_volatility"] == pytest.approx(0.0)
    assert metrics["sharpe"] == pytest.approx(0.0)
    assert metrics["max_drawdown"] == pytest.approx(0.0)
    assert metrics["annual_one_way_turnover"] == pytest.approx(0.0)
    assert metrics["trade_count"] == 0


def test_period_metrics_reject_an_empty_date_range() -> None:
    dates = pd.bdate_range("2026-01-02", periods=2)
    equity_curve = pd.DataFrame({"datetime": dates, "equity": [100.0, 101.0]})
    trades = pd.DataFrame(columns=["datetime", "fill_price", "quantity"])

    with pytest.raises(ValueError, match="no equity rows"):
        trend_strategy.calculate_period_metrics(
            equity_curve,
            trades,
            "2027-01-01",
            "2027-01-31",
        )


def test_period_metrics_rejects_reversed_date_range_before_filtering() -> None:
    equity_curve = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2026-01-02", "2026-01-05"]),
            "equity": [100.0, 101.0],
        }
    )

    with pytest.raises(ValueError, match="start"):
        trend_strategy.calculate_period_metrics(
            equity_curve,
            pd.DataFrame(),
            "2026-01-05",
            "2026-01-02",
        )


def test_period_metrics_numericizes_equity_values() -> None:
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    equity_curve = pd.DataFrame({"datetime": dates, "equity": ["100", "110"]})

    metrics = trend_strategy.calculate_period_metrics(
        equity_curve,
        pd.DataFrame(columns=["datetime", "fill_price", "quantity"]),
        dates[0],
        dates[-1],
    )

    assert metrics["total_return"] == pytest.approx(0.1)


def test_period_metrics_accepts_truly_empty_trades_without_columns() -> None:
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    equity_curve = pd.DataFrame({"datetime": dates, "equity": [100.0, 110.0]})

    metrics = trend_strategy.calculate_period_metrics(
        equity_curve,
        pd.DataFrame(),
        dates[0],
        dates[-1],
    )

    assert metrics["annual_one_way_turnover"] == pytest.approx(0.0)
    assert metrics["trade_count"] == 0


def test_period_metrics_requires_trade_columns_only_for_non_empty_trades() -> None:
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    equity_curve = pd.DataFrame({"datetime": dates, "equity": [100.0, 110.0]})
    trades = pd.DataFrame({"datetime": [dates[0]]})

    with pytest.raises(ValueError, match="trades require"):
        trend_strategy.calculate_period_metrics(
            equity_curve,
            trades,
            dates[0],
            dates[-1],
        )


@pytest.mark.parametrize(
    "invalid_equity",
    ["invalid", float("nan"), float("inf"), float("-inf"), 0.0, -1.0],
)
def test_period_metrics_rejects_non_positive_or_non_finite_equity(
    invalid_equity: object,
) -> None:
    dates = pd.to_datetime(["2026-01-02", "2026-01-05"])
    equity_curve = pd.DataFrame({"datetime": dates, "equity": [100.0, invalid_equity]})

    with pytest.raises(ValueError, match="equity"):
        trend_strategy.calculate_period_metrics(
            equity_curve,
            pd.DataFrame(columns=["datetime", "fill_price", "quantity"]),
            dates[0],
            dates[-1],
        )


def test_period_metrics_include_full_endpoint_dates() -> None:
    dates = pd.to_datetime(["2026-01-02 15:00", "2026-01-05 15:00"])
    equity_curve = pd.DataFrame({"datetime": dates, "equity": [100.0, 110.0]})
    trades = pd.DataFrame(
        {
            "datetime": [pd.Timestamp("2026-01-05 09:30")],
            "fill_price": [10.0],
            "quantity": [10],
        }
    )

    metrics = trend_strategy.calculate_period_metrics(
        equity_curve,
        trades,
        "2026-01-02",
        "2026-01-05",
    )

    assert metrics["days"] == 2
    assert metrics["trade_count"] == 1
    assert metrics["total_return"] == pytest.approx(0.1)
