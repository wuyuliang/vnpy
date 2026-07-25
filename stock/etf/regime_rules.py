"""Strictly causal rule-based market regime calculations."""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from math import isfinite
from typing import Literal

import numpy as np
import pandas as pd

PriceAdjustmentMode = Literal["raw", "point_in_time_adjusted"]


class RegimeState(str, Enum):
    """Five mutually exclusive market regimes."""

    TREND_DOWN = "趋势向下"
    OSCILLATING_DOWN = "震荡向下"
    NO_TREND = "无趋势"
    OSCILLATING_UP = "震荡向上"
    TREND_UP = "趋势向上"


@dataclass(frozen=True)
class RegimeConfig:
    """Frozen parameters for the preregistered rule set."""

    minimum_rows: int = 252
    price_adjustment_mode: PriceAdjustmentMode = "raw"

    def __post_init__(self) -> None:
        if (
            isinstance(self.minimum_rows, bool)
            or not isinstance(self.minimum_rows, int)
            or self.minimum_rows < 1
        ):
            raise ValueError("minimum_rows must be a positive integer")
        if self.price_adjustment_mode not in {"raw", "point_in_time_adjusted"}:
            raise ValueError(
                "price_adjustment_mode must be raw or point_in_time_adjusted"
            )


def score_to_state(score: float) -> RegimeState:
    """Map a finite score in [-3, 3] to its documented regime."""
    if not isinstance(score, (int, float, np.integer, np.floating)):
        raise ValueError("score must be numeric")
    numeric_score = float(score)
    if not isfinite(numeric_score) or not -3 <= numeric_score <= 3:
        raise ValueError("score must be finite and within [-3, 3]")
    if numeric_score <= -2:
        return RegimeState.TREND_DOWN
    if numeric_score < -1:
        return RegimeState.OSCILLATING_DOWN
    if numeric_score <= 1:
        return RegimeState.NO_TREND
    if numeric_score < 2:
        return RegimeState.OSCILLATING_UP
    return RegimeState.TREND_UP


def prepare_symbol_bars(
    bars: pd.DataFrame,
    config: RegimeConfig,
) -> pd.DataFrame:
    """Validate and normalize one symbol's OHLCV rows without filling data."""
    required = {"symbol", "datetime", "open", "high", "low", "close", "volume"}
    missing = required - set(bars.columns)
    if missing:
        raise ValueError(f"bars missing columns: {sorted(missing)}")

    frame = bars.copy()
    if frame.empty:
        raise ValueError("bars must not be empty")

    symbols = frame["symbol"].astype("string").str.strip()
    if symbols.isna().any() or symbols.eq("").any():
        raise ValueError("symbol must be a non-empty string")
    if symbols.nunique() != 1:
        raise ValueError("bars must contain exactly one symbol")
    frame["symbol"] = symbols.astype(str)

    dates = pd.to_datetime(frame["datetime"], errors="raise")
    if dates.isna().any():
        raise ValueError("datetime must not be missing")
    if dates.dt.tz is not None:
        raise ValueError("datetime must be timezone-naive")
    frame["datetime"] = dates.dt.normalize()
    if frame["datetime"].duplicated().any():
        raise ValueError("bars contain duplicate dates")

    numeric_columns = ["open", "high", "low", "close", "volume"]
    if "turnover" in frame.columns:
        numeric_columns.append("turnover")
    for column in numeric_columns:
        frame[column] = pd.to_numeric(frame[column], errors="coerce")
    if not np.isfinite(frame[numeric_columns].to_numpy(dtype=float)).all():
        raise ValueError("OHLCV values must be finite")

    if frame[["open", "high", "low", "close"]].le(0).any(axis=None):
        raise ValueError("OHLC prices must be positive")
    if (
        frame["high"].lt(frame[["open", "close"]].max(axis=1)).any()
        or frame["low"].gt(frame[["open", "close"]].min(axis=1)).any()
        or frame["high"].lt(frame["low"]).any()
    ):
        raise ValueError("bars contain invalid OHLC relationships")
    if frame["volume"].lt(0).any():
        raise ValueError("volume must be non-negative")
    if "turnover" in frame.columns and frame["turnover"].lt(0).any():
        raise ValueError("turnover must be non-negative")
    if len(frame) < config.minimum_rows:
        raise ValueError(f"bars require at least {config.minimum_rows} rows")

    return frame.sort_values("datetime", ignore_index=True)


def _wilder_dmi(bars: pd.DataFrame, period: int) -> pd.DataFrame:
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
        dtype=float,
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0),
        index=bars.index,
        dtype=float,
    )
    smoothing = {
        "alpha": 1.0 / period,
        "adjust": False,
        "min_periods": period,
    }
    atr = true_range.ewm(**smoothing).mean()
    plus_smoothed = plus_dm.ewm(**smoothing).mean()
    minus_smoothed = minus_dm.ewm(**smoothing).mean()
    valid_atr = atr.where(atr > 0)
    plus_di = 100 * plus_smoothed.div(valid_atr)
    minus_di = 100 * minus_smoothed.div(valid_atr)
    di_sum = plus_di + minus_di
    dx = 100 * (plus_di - minus_di).abs().div(di_sum)
    dx = dx.mask(di_sum.eq(0), 0.0)
    adx = dx.ewm(**smoothing).mean()
    return pd.DataFrame(
        {
            "atr": atr,
            "plus_di": plus_di,
            "minus_di": minus_di,
            "adx": adx,
        },
        index=bars.index,
    )


def _efficiency_ratio(close: pd.Series, period: int) -> pd.Series:
    movement = close.diff().abs().rolling(period, min_periods=period).sum()
    displacement = (close - close.shift(period)).abs()
    ratio = displacement.div(movement)
    return ratio.mask(movement.eq(0), 0.0).clip(0.0, 1.0)


def _rolling_log_regression(close: pd.Series, period: int) -> pd.DataFrame:
    log_close = np.log(close.astype(float))
    x = np.arange(period, dtype=float)

    def slope(values: np.ndarray) -> float:
        return float(np.polyfit(x, values, 1)[0])

    def r_squared(values: np.ndarray) -> float:
        fitted_coefficients = np.polyfit(x, values, 1)
        fitted = fitted_coefficients[1] + fitted_coefficients[0] * x
        residual_sum = float(np.square(values - fitted).sum())
        total_sum = float(np.square(values - values.mean()).sum())
        if total_sum == 0:
            return 0.0
        return float(np.clip(1.0 - residual_sum / total_sum, 0.0, 1.0))

    rolling = log_close.rolling(period, min_periods=period)
    return pd.DataFrame(
        {
            "slope": rolling.apply(slope, raw=True),
            "r2": rolling.apply(r_squared, raw=True),
        },
        index=close.index,
    )


def _direction_quality_to_score(
    direction: pd.Series,
    quality: pd.Series,
) -> pd.Series:
    direction_values = direction.to_numpy(dtype=float)
    quality_values = quality.to_numpy(dtype=float)
    confidence = np.abs(direction_values)
    sign = np.sign(direction_values)
    score = np.full(len(direction), np.nan, dtype=float)
    valid = np.isfinite(direction_values) & np.isfinite(quality_values)

    no_trend = valid & (confidence <= 0.25)
    score[no_trend] = sign[no_trend] * confidence[no_trend] / 0.25

    oscillating = valid & (confidence > 0.25) & (quality_values < 0.55)
    oscillating_magnitude = 1.0 + np.clip(
        0.65 * (confidence - 0.25) / 0.75
        + 0.35 * quality_values / 0.55,
        0.0,
        1.0 - 1e-12,
    )
    score[oscillating] = sign[oscillating] * oscillating_magnitude[oscillating]

    trending = valid & (confidence > 0.25) & (quality_values >= 0.55)
    trend_magnitude = 2.0 + np.clip(
        0.55 * (confidence - 0.25) / 0.75
        + 0.45 * (quality_values - 0.55) / 0.45,
        0.0,
        1.0,
    )
    score[trending] = sign[trending] * trend_magnitude[trending]
    return pd.Series(np.clip(score, -3.0, 3.0), index=direction.index)


def _state_values(scores: pd.Series) -> pd.Series:
    states = pd.Series(pd.NA, index=scores.index, dtype="string")
    valid = scores.notna()
    states.loc[valid] = scores.loc[valid].map(lambda value: score_to_state(value).value)
    return states


def calculate_realized_regime(
    bars: pd.DataFrame,
    config: RegimeConfig,
) -> pd.DataFrame:
    """Calculate the trailing price-only regime observed at each date."""
    frame = prepare_symbol_bars(bars, config)
    close = frame["close"]
    frame["ema5"] = close.ewm(span=5, adjust=False, min_periods=5).mean()
    frame["ema20"] = close.ewm(span=20, adjust=False, min_periods=20).mean()

    dmi10 = _wilder_dmi(frame, 10)
    frame["atr10"] = dmi10["atr"]
    frame["plus_di10"] = dmi10["plus_di"]
    frame["minus_di10"] = dmi10["minus_di"]
    frame["adx10"] = dmi10["adx"]
    frame["er10"] = _efficiency_ratio(close, 10)
    frame["er20"] = _efficiency_ratio(close, 20)

    regression20 = _rolling_log_regression(close, 20)
    frame["log_slope20"] = regression20["slope"]
    frame["r2_20"] = regression20["r2"]
    frame["atr_pct"] = frame["atr10"].div(close).where(frame["atr10"] > 0)
    frame["ema_direction"] = np.tanh(
        frame["ema5"].sub(frame["ema20"]).div(frame["atr10"]) / 2.0
    )
    frame["regression_direction"] = np.tanh(
        frame["log_slope20"].mul(19).div(frame["atr_pct"]) / 3.0
    )
    di_sum = frame["plus_di10"] + frame["minus_di10"]
    frame["di_direction10"] = (
        frame["plus_di10"].sub(frame["minus_di10"]).div(di_sum)
    ).mask(di_sum.eq(0), 0.0)
    frame["direction_evidence"] = (
        0.35 * frame["ema_direction"]
        + 0.35 * frame["regression_direction"]
        + 0.30 * frame["di_direction10"]
    ).clip(-1.0, 1.0)
    frame["adx_quality10"] = ((frame["adx10"] - 15.0) / 25.0).clip(0.0, 1.0)
    frame["trend_quality"] = (
        0.30 * frame["er10"]
        + 0.25 * frame["er20"]
        + 0.25 * frame["adx_quality10"]
        + 0.20 * frame["r2_20"]
    ).clip(0.0, 1.0)
    frame["realized_score"] = _direction_quality_to_score(
        frame["direction_evidence"],
        frame["trend_quality"],
    )
    frame["realized_state"] = _state_values(frame["realized_score"])
    return frame
