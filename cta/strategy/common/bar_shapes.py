"""Causal one-bar predicates used by price-action strategies."""
from __future__ import annotations

import math

import numpy as np
import pandas as pd


def is_big_bear_body(
    open_price: float,
    close_price: float,
    atr_value: float,
    multiplier: float,
) -> bool:
    """Return whether a bear body meets the ATR-normalized threshold."""
    return bool(
        math.isfinite(atr_value)
        and atr_value > 0
        and close_price < open_price
        and open_price - close_price >= multiplier * atr_value
    )


def is_leg_drop(
    first_open: float,
    last_close: float,
    atr_value: float,
    multiplier: float,
) -> bool:
    """Return whether a whole leg's net drop meets the ATR threshold.

    The per-bar reading of "big bear body" asks two adjacent bars to each be a
    3-sigma event, which on one-minute data is a one-in-a-hundred-thousand
    coincidence. What the setup is actually about is the *leg* being strong:
    open of the first bar down to close of the last, however many bars that
    took. Bars in between still carry a per-bar floor (see
    ``is_big_bear_body`` with ``multiplier / 2``) so a single spike plus a flat
    bar cannot qualify.
    """
    return bool(
        math.isfinite(atr_value)
        and atr_value > 0
        and first_open - last_close >= multiplier * atr_value
    )


def is_small_body(
    open_price: float,
    close_price: float,
    atr_value: float,
    multiplier: float,
) -> bool:
    """Return whether the absolute body is no larger than the ATR threshold."""
    return bool(
        math.isfinite(atr_value)
        and atr_value > 0
        and abs(close_price - open_price) <= multiplier * atr_value
    )


def has_small_upper_wick(
    open_price: float,
    high_price: float,
    close_price: float,
    maximum_ratio: float,
) -> bool:
    """Return whether the upper wick is small relative to the candle body."""
    body = abs(close_price - open_price)
    wick = high_price - open_price
    limit = maximum_ratio * body
    return bool(body > 0 and (wick <= limit or math.isclose(wick, limit)))


def has_small_lower_wick(
    open_price: float,
    close_price: float,
    low_price: float,
    maximum_ratio: float,
) -> bool:
    """Return whether the lower wick is small relative to the candle body."""
    body = abs(close_price - open_price)
    wick = close_price - low_price
    limit = maximum_ratio * body
    return bool(body > 0 and (wick <= limit or math.isclose(wick, limit)))


def is_volume_surge(
    volume: float,
    baseline: float,
    baseline_samples: int,
    multiplier: float,
    minimum_samples: int,
) -> bool:
    """Return whether volume meets its causal baseline and sample threshold."""
    return bool(
        baseline_samples >= minimum_samples
        and math.isfinite(baseline)
        and baseline > 0
        and volume >= multiplier * baseline
    )


def causal_volume_baseline(
    volume: pd.Series,
    is_segment_first: pd.Series,
    *,
    window: int,
) -> tuple[pd.Series, pd.Series]:
    """Calculate prior-bar volume means excluding each segment's first bar."""
    if window <= 0:
        raise ValueError("volume baseline window must be positive")
    if len(volume) != len(is_segment_first):
        raise ValueError("volume and segment flags must have equal length")
    eligible = pd.to_numeric(volume, errors="coerce").where(
        ~is_segment_first.astype(bool)
    )
    prior = eligible.shift(1).rolling(window, min_periods=1)
    return prior.mean(), prior.count().astype(int)


def bearish_ema_alignment(fast: float, mid: float, slow: float) -> bool:
    """Return whether EMA values form a strict bearish stack."""
    return bool(
        all(math.isfinite(value) for value in (fast, mid, slow))
        and fast < mid < slow
    )


def breaks_structure_low(low_price: float, structure_low: float) -> bool:
    """Return whether price strictly breaks the prior structure low."""
    return bool(
        math.isfinite(low_price)
        and math.isfinite(structure_low)
        and low_price < structure_low
    )


def causal_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    *,
    period: int,
    method: str = "wilder",
) -> pd.Series:
    """True-range average, shifted so bar ``t`` only sees data through ``t-1``.

    ``wilder`` is the usual ``ewm(alpha=1/period)``; ``sma`` is a flat mean over
    the last ``period`` bars. On one-minute bars the flat mean over a long
    window is the steadier normaliser: Wilder's decay puts most of the weight
    on the last handful of minutes, so one violent bar deflates the very
    threshold it should be measured against.
    """
    if period <= 0:
        raise ValueError("atr period must be positive")
    previous_close = close.shift(1)
    true_range = pd.concat(
        [
            high - low,
            (high - previous_close).abs(),
            (low - previous_close).abs(),
        ],
        axis=1,
    ).max(axis=1)
    if method == "wilder":
        averaged = true_range.ewm(alpha=1 / period, adjust=False).mean()
    elif method == "sma":
        averaged = true_range.rolling(period, min_periods=period).mean()
    else:
        raise ValueError(f"unknown atr method: {method!r}")
    return averaged.shift(1)


def causal_low_pullback_structure(
    low: pd.Series,
    high: pd.Series,
    segment_id: pd.Series,
    atr: pd.Series,
    *,
    first_leg_lookback: int,
    first_leg_atr_mult: float,
    pullback_min_bars: int,
    pullback_max_bars: int,
    pullback_min_ratio: float,
    pullback_max_ratio: float,
) -> pd.DataFrame:
    """Find first-leg lows followed by a bounded pullback, using completed bars.

    The function is setup-neutral: it identifies the low and rally structure,
    while the caller decides what an entry bar looks like. Candidate spans are
    evaluated as whole columns, nearest swing first, instead of scanning every
    bar in Python.
    """
    lengths = {len(value) for value in (low, high, segment_id, atr)}
    if len(lengths) != 1:
        raise ValueError("structure inputs must have equal length")
    if first_leg_lookback <= 0:
        raise ValueError("first_leg_lookback must be positive")
    if not 1 <= pullback_min_bars <= pullback_max_bars:
        raise ValueError("pullback bars must satisfy 1 <= min <= max")
    if not 0 < pullback_min_ratio < pullback_max_ratio <= 1:
        raise ValueError("pullback ratios must satisfy 0 < min < max <= 1")
    if not math.isfinite(first_leg_atr_mult) or first_leg_atr_mult <= 0:
        raise ValueError("first_leg_atr_mult must be finite and positive")

    original_index = low.index
    source = pd.DataFrame(
        {
            "low": pd.to_numeric(low, errors="coerce").reset_index(drop=True),
            "high": pd.to_numeric(high, errors="coerce").reset_index(drop=True),
            "segment": segment_id.reset_index(drop=True),
            "atr": pd.to_numeric(atr, errors="coerce").reset_index(drop=True),
        }
    )
    grouped = source.groupby("segment", sort=False, dropna=False)
    window = first_leg_lookback + 1
    swing_window_low = (
        grouped["low"]
        .rolling(window, min_periods=window)
        .min()
        .reset_index(level=0, drop=True)
        .sort_index()
    )
    swing_window_high = (
        grouped["high"]
        .rolling(window, min_periods=window)
        .max()
        .reset_index(level=0, drop=True)
        .sort_index()
    )

    size = len(source)
    matched = np.zeros(size, dtype=bool)
    swing_index = np.full(size, -1, dtype=np.int64)
    pullback_bars = np.zeros(size, dtype=np.int64)
    first_leg = np.full(size, np.nan)
    rally_high = np.full(size, np.nan)
    pullback_ratio = np.full(size, np.nan)
    swing_low_price = np.full(size, np.nan)

    for span in range(pullback_min_bars, pullback_max_bars + 1):
        candidate_low = source["low"].shift(span)
        candidate_window_low = swing_window_low.shift(span)
        candidate_window_high = swing_window_high.shift(span)
        candidate_leg = candidate_window_high - candidate_low
        candidate_rally_high = (
            source["high"].shift(1).rolling(span - 1, min_periods=span - 1).max()
        )
        candidate_ratio = (candidate_rally_high - candidate_low) / candidate_leg
        valid = (
            ~matched
            & source["segment"].eq(source["segment"].shift(span)).to_numpy()
            & candidate_low.eq(candidate_window_low).to_numpy()
            & candidate_leg.ge(first_leg_atr_mult * source["atr"]).to_numpy()
            & candidate_ratio.between(
                pullback_min_ratio, pullback_max_ratio, inclusive="both"
            ).to_numpy()
        )
        positions = np.flatnonzero(valid)
        if not len(positions):
            continue
        matched[positions] = True
        swing_index[positions] = positions - span
        pullback_bars[positions] = span
        first_leg[positions] = candidate_leg.iloc[positions]
        rally_high[positions] = candidate_rally_high.iloc[positions]
        pullback_ratio[positions] = candidate_ratio.iloc[positions]
        swing_low_price[positions] = candidate_low.iloc[positions]

    result = pd.DataFrame(
        {
            "swing_window_low": swing_window_low,
            "swing_window_high": swing_window_high,
            "pullback_matched": matched,
            "swing_index": swing_index,
            "pullback_bars": pullback_bars,
            "first_leg": first_leg,
            "rally_high": rally_high,
            "pullback_ratio": pullback_ratio,
            "swing_low": swing_low_price,
        }
    )
    result.index = original_index
    return result
