import pandas as pd
import pytest

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
