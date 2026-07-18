import numpy as np
import pandas as pd


def calculate_true_range(frame: pd.DataFrame) -> pd.Series:
    """Calculate true range from high, low and previous close."""
    previous_close = frame["close"].shift(1)
    components = pd.concat(
        [
            frame["high"] - frame["low"],
            (frame["high"] - previous_close).abs(),
            (frame["low"] - previous_close).abs(),
        ],
        axis=1,
    )
    return components.max(axis=1)


def calculate_atr(frame: pd.DataFrame, period: int = 5) -> pd.Series:
    """Calculate Wilder ATR with exponential smoothing."""
    true_range = calculate_true_range(frame)
    return true_range.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def calculate_adx(frame: pd.DataFrame, period: int = 5) -> pd.Series:
    """Calculate Wilder ADX, returning NaN when directional sum is zero."""
    high_change = frame["high"].diff()
    low_change = -frame["low"].diff()
    plus_dm = pd.Series(
        np.where((high_change > low_change) & (high_change > 0), high_change, 0.0),
        index=frame.index,
    )
    minus_dm = pd.Series(
        np.where((low_change > high_change) & (low_change > 0), low_change, 0.0),
        index=frame.index,
    )
    atr = calculate_atr(frame, period)
    plus_smoothed = plus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    minus_smoothed = minus_dm.ewm(
        alpha=1 / period, adjust=False, min_periods=period
    ).mean()
    plus_di = 100 * plus_smoothed / atr
    minus_di = 100 * minus_smoothed / atr
    denominator = plus_di + minus_di
    dx = (100 * (plus_di - minus_di).abs() / denominator).where(denominator != 0)
    return dx.ewm(alpha=1 / period, adjust=False, min_periods=period).mean()


def add_indicators(
    frame: pd.DataFrame, atr_period: int = 5, adx_period: int = 5
) -> pd.DataFrame:
    """Return a sorted copy with all strategy indicators attached."""
    if atr_period not in {5, 10} or adx_period not in {5, 10}:
        raise ValueError("strategy ATR and ADX periods must be 5 or 10")
    result = frame.copy()
    if "datetime" in result.columns:
        result = result.sort_values("datetime").reset_index(drop=True)
    close = result["close"]
    for period in (5, 10, 20):
        result[f"ema{period}"] = close.ewm(
            span=period,
            adjust=False,
            min_periods=period,
        ).mean()
    for period in (3, 5, 10, 20):
        result[f"return_{period}"] = close / close.shift(period) - 1
    result["ema5_slope"] = result["ema5"] / result["ema5"].shift(5) - 1
    result["atr5"] = calculate_atr(result, 5)
    result["normalized_atr5"] = result["atr5"] / close
    result["risk_atr"] = calculate_atr(result, atr_period)
    result["trend_adx"] = calculate_adx(result, adx_period)
    if "turnover" in result:
        result["turnover_median20"] = (
            result["turnover"]
            .rolling(
                20,
                min_periods=20,
            )
            .median()
        )
    else:
        result["turnover_median20"] = np.nan
    result["bar_count"] = np.arange(1, len(result) + 1)
    return result
