import numpy as np
import pandas as pd
import pytest

from stock.etf.regime_rules import (
    RegimeConfig,
    RegimeState,
    _efficiency_ratio,
    _rolling_log_regression,
    calculate_realized_regime,
    prepare_symbol_bars,
    score_to_state,
)


def _bars(periods: int = 252, symbol: str = "159915.SZ") -> pd.DataFrame:
    close = np.linspace(1.0, 2.0, periods)
    return pd.DataFrame(
        {
            "symbol": [symbol] * periods,
            "datetime": pd.bdate_range("2025-01-02", periods=periods),
            "open": close,
            "high": close + 0.02,
            "low": close - 0.02,
            "close": close,
            "volume": np.full(periods, 1_000_000.0),
        }
    )


@pytest.mark.parametrize(
    ("score", "expected"),
    [
        (-3.0, RegimeState.TREND_DOWN),
        (-2.0, RegimeState.TREND_DOWN),
        (-1.999, RegimeState.OSCILLATING_DOWN),
        (-1.001, RegimeState.OSCILLATING_DOWN),
        (-1.0, RegimeState.NO_TREND),
        (0.0, RegimeState.NO_TREND),
        (1.0, RegimeState.NO_TREND),
        (1.001, RegimeState.OSCILLATING_UP),
        (1.999, RegimeState.OSCILLATING_UP),
        (2.0, RegimeState.TREND_UP),
        (3.0, RegimeState.TREND_UP),
    ],
)
def test_score_to_state_uses_documented_boundaries(
    score: float,
    expected: RegimeState,
) -> None:
    assert score_to_state(score) is expected


@pytest.mark.parametrize("score", [-3.01, 3.01, np.nan, np.inf, -np.inf])
def test_score_to_state_rejects_invalid_values(score: float) -> None:
    with pytest.raises(ValueError, match="score"):
        score_to_state(score)


def test_prepare_symbol_bars_sorts_without_mutating_input() -> None:
    bars = _bars(symbol="600000.SH")
    descending = bars.sort_values("datetime", ascending=False).reset_index(drop=True)

    prepared = prepare_symbol_bars(descending, RegimeConfig())

    assert prepared["symbol"].unique().tolist() == ["600000.SH"]
    assert prepared["datetime"].is_monotonic_increasing
    pd.testing.assert_frame_equal(
        descending,
        bars.sort_values("datetime", ascending=False).reset_index(drop=True),
    )


@pytest.mark.parametrize(
    ("mutate", "message"),
    [
        (lambda frame: frame.assign(symbol=""), "symbol"),
        (
            lambda frame: pd.concat(
                [frame.iloc[:-1], frame.iloc[[-1]].assign(symbol="510300.SH")],
                ignore_index=True,
            ),
            "one symbol",
        ),
        (lambda frame: frame.drop(columns="volume"), "missing columns"),
        (lambda frame: frame.assign(open=np.nan), "finite"),
        (lambda frame: frame.assign(close=0.0), "positive"),
        (lambda frame: frame.assign(high=frame["close"] - 0.1), "OHLC"),
        (lambda frame: frame.assign(low=frame["close"] + 0.1), "OHLC"),
        (lambda frame: frame.assign(volume=-1.0), "volume"),
        (lambda frame: frame.assign(turnover=-1.0), "turnover"),
    ],
)
def test_prepare_symbol_bars_rejects_invalid_ohlcv(
    mutate: object,
    message: str,
) -> None:
    with pytest.raises(ValueError, match=message):
        prepare_symbol_bars(mutate(_bars()), RegimeConfig())


def test_prepare_symbol_bars_rejects_duplicate_dates() -> None:
    bars = _bars()
    bars.loc[1, "datetime"] = bars.loc[0, "datetime"]

    with pytest.raises(ValueError, match="duplicate"):
        prepare_symbol_bars(bars, RegimeConfig())


def test_prepare_symbol_bars_rejects_timezone_aware_dates() -> None:
    bars = _bars()
    bars["datetime"] = bars["datetime"].dt.tz_localize("Asia/Shanghai")

    with pytest.raises(ValueError, match="timezone"):
        prepare_symbol_bars(bars, RegimeConfig())


def test_prepare_symbol_bars_requires_configured_history() -> None:
    with pytest.raises(ValueError, match="at least 252"):
        prepare_symbol_bars(_bars(251), RegimeConfig())


@pytest.mark.parametrize(
    "mode",
    ["unknown", "", None],
)
def test_config_rejects_unknown_adjustment_mode(mode: object) -> None:
    with pytest.raises(ValueError, match="price_adjustment_mode"):
        RegimeConfig(price_adjustment_mode=mode)


def _variable_bars(periods: int = 80) -> pd.DataFrame:
    index = np.arange(periods, dtype=float)
    close = 10.0 + 0.03 * index + np.sin(index / 2.3) * 0.4
    open_ = close + np.sin(index / 3.1) * 0.08
    high = np.maximum(open_, close) + 0.15 + (index % 3) * 0.01
    low = np.minimum(open_, close) - 0.14 - (index % 4) * 0.01
    frame = _bars(periods)
    frame[["open", "high", "low", "close"]] = np.column_stack(
        [open_, high, low, close]
    )
    return frame


def _independent_wilder_dmi(
    bars: pd.DataFrame,
    period: int,
) -> tuple[pd.Series, pd.Series, pd.Series, pd.Series]:
    previous_close = bars["close"].shift(1)
    true_range = pd.concat(
        [
            bars["high"] - bars["low"],
            (bars["high"] - previous_close).abs(),
            (bars["low"] - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    up_move = bars["high"].diff()
    down_move = -bars["low"].diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0),
        index=bars.index,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=bars.index,
    )

    alpha = 1.0 / period
    atr = true_range.ewm(
        alpha=alpha,
        adjust=False,
        min_periods=period,
    ).mean()
    plus_smoothed = plus_dm.ewm(
        alpha=alpha,
        adjust=False,
        min_periods=period,
    ).mean()
    minus_smoothed = minus_dm.ewm(
        alpha=alpha,
        adjust=False,
        min_periods=period,
    ).mean()
    plus_di = 100 * plus_smoothed.div(atr)
    minus_di = 100 * minus_smoothed.div(atr)
    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs().div(di_sum)
    dx = dx.mask(di_sum.eq(0), 0.0)
    adx = dx.ewm(
        alpha=alpha,
        adjust=False,
        min_periods=period,
    ).mean()
    return atr, plus_di, minus_di, adx


def test_wilder_atr_and_dmi_match_independent_reference() -> None:
    bars = _variable_bars()
    expected = _independent_wilder_dmi(bars, 10)

    result = calculate_realized_regime(
        bars,
        RegimeConfig(minimum_rows=1),
    )

    for column, reference in zip(
        ("atr10", "plus_di10", "minus_di10", "adx10"),
        expected,
        strict=True,
    ):
        pd.testing.assert_series_equal(
            result[column],
            reference,
            check_names=False,
            rtol=1e-12,
            atol=1e-12,
        )


def test_efficiency_ratio_handles_flat_and_direct_paths() -> None:
    flat = pd.Series([10.0] * 8)
    direct = pd.Series(np.arange(1.0, 9.0))

    flat_result = _efficiency_ratio(flat, 5)
    direct_result = _efficiency_ratio(direct, 5)

    assert flat_result.iloc[-1] == pytest.approx(0.0)
    assert direct_result.iloc[-1] == pytest.approx(1.0)
    assert flat_result.iloc[:5].isna().all()


def test_log_regression_matches_numpy_reference() -> None:
    close = pd.Series(np.exp(np.linspace(1.0, 1.4, 30)))

    result = _rolling_log_regression(close, 20)

    x = np.arange(20, dtype=float)
    expected_slope, expected_intercept = np.polyfit(x, np.log(close.iloc[-20:]), 1)
    fitted = expected_intercept + expected_slope * x
    residual = np.log(close.iloc[-20:]) - fitted
    total = np.log(close.iloc[-20:]) - np.log(close.iloc[-20:]).mean()
    expected_r2 = 1 - float(np.square(residual).sum() / np.square(total).sum())
    assert result.iloc[-1]["slope"] == pytest.approx(expected_slope)
    assert result.iloc[-1]["r2"] == pytest.approx(expected_r2)
    assert result.iloc[:19].isna().all(axis=None)


@pytest.mark.parametrize(
    ("closes", "expected"),
    [
        (np.linspace(10.0, 20.0, 320), RegimeState.TREND_UP.value),
        (np.linspace(20.0, 10.0, 320), RegimeState.TREND_DOWN.value),
        (np.full(320, 10.0), RegimeState.NO_TREND.value),
    ],
)
def test_realized_regime_classifies_clear_synthetic_paths(
    closes: np.ndarray,
    expected: str,
) -> None:
    bars = _bars(len(closes))
    bars["open"] = closes
    bars["high"] = closes + 0.1
    bars["low"] = closes - 0.1
    bars["close"] = closes

    result = calculate_realized_regime(bars, RegimeConfig())

    assert result.iloc[-1]["realized_state"] == expected


def test_realized_regime_does_not_depend_on_volume_or_turnover() -> None:
    bars = _variable_bars(320)
    first = bars.assign(turnover=np.linspace(1_000.0, 2_000.0, len(bars)))
    second = bars.assign(
        volume=np.linspace(7.0, 700_000_000.0, len(bars)),
        turnover=np.linspace(9_000_000.0, 1.0, len(bars)),
    )

    first_result = calculate_realized_regime(first, RegimeConfig())
    second_result = calculate_realized_regime(second, RegimeConfig())

    columns = [
        "direction_evidence",
        "trend_quality",
        "realized_score",
        "realized_state",
    ]
    pd.testing.assert_frame_equal(first_result[columns], second_result[columns])
