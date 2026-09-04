"""Prefix-invariant Brooks price-action features on completed bars."""
from __future__ import annotations

from collections.abc import Iterable

import numpy as np
import pandas as pd

from ...config import BrooksCycleConfig, FeatureConfig


OHLC = ("open", "high", "low", "close")


def wilder_mean(values: pd.Series, period: int) -> pd.Series:
    if period < 1:
        raise ValueError("period must be positive")
    numeric = pd.to_numeric(values, errors="coerce").astype(float)
    result = pd.Series(np.nan, index=numeric.index, dtype=float)
    seed_values: list[float] = []
    prior: float | None = None
    for position, raw in enumerate(numeric):
        current = float(raw)
        if not np.isfinite(current):
            seed_values.clear()
            prior = None
            continue
        if prior is None:
            seed_values.append(current)
            if len(seed_values) < period:
                continue
            prior = float(np.mean(seed_values[-period:]))
        else:
            prior = ((period - 1) * prior + current) / period
        result.iloc[position] = prior
    return result


def wilder_atr(
    high: pd.Series,
    low: pd.Series,
    close: pd.Series,
    period: int,
) -> pd.Series:
    previous_close = close.shift(1)
    true_range = pd.concat(
        [high - low, (high - previous_close).abs(), (low - previous_close).abs()], axis=1
    ).max(axis=1, skipna=False)
    true_range.iloc[0] = high.iloc[0] - low.iloc[0]
    return wilder_mean(true_range, period)


def causal_percentile_rank(values: pd.Series, lookback: int) -> pd.Series:
    """Rank each current value against the preceding window, excluding itself."""
    if lookback < 1:
        raise ValueError("lookback must be positive")
    numeric = pd.to_numeric(values, errors="coerce")
    result = pd.Series(np.nan, index=numeric.index, dtype=float)
    for position in range(lookback, len(numeric)):
        current = float(numeric.iloc[position])
        history = numeric.iloc[position - lookback : position]
        if np.isfinite(current) and history.notna().all():
            result.iloc[position] = 100.0 * float((history <= current).sum()) / lookback
    return result


def confirmed_pivots(
    frame: pd.DataFrame,
    *,
    left: int,
    right: int,
) -> pd.DataFrame:
    _require_columns(frame, ("bar_end", "high", "low"))
    if left < 1 or right < 1:
        raise ValueError("pivot left/right must be positive")
    rows: list[dict[str, object]] = []
    for position in range(left, len(frame) - right):
        window = frame.iloc[position - left : position + right + 1]
        center = frame.iloc[position]
        other = window.drop(window.index[left])
        if bool((float(center["high"]) > other["high"]).all()):
            rows.append(
                {
                    "kind": "HIGH",
                    "price": float(center["high"]),
                    "pivot_position": position,
                    "pivot_bar_end": center["bar_end"],
                    "known_at": frame.iloc[position + right]["bar_end"],
                }
            )
        if bool((float(center["low"]) < other["low"]).all()):
            rows.append(
                {
                    "kind": "LOW",
                    "price": float(center["low"]),
                    "pivot_position": position,
                    "pivot_bar_end": center["bar_end"],
                    "known_at": frame.iloc[position + right]["bar_end"],
                }
            )
    return pd.DataFrame(
        rows,
        columns=["kind", "price", "pivot_position", "pivot_bar_end", "known_at"],
    )


def add_causal_features(
    frame: pd.DataFrame,
    config: BrooksCycleConfig,
    *,
    price_tick: float | pd.Series,
    round_trip_cost_price: float | pd.Series | None = None,
) -> pd.DataFrame:
    """Add only completed-bar features; output prefix is invariant to future rows."""
    _require_columns(frame, (*OHLC, "bar_end"))
    feature_config = config.features
    cycle_config = config.cycle
    result = frame.copy().reset_index(drop=True)
    _validate_time_and_order(result)
    eps = _tick_series(price_tick, result.index).clip(lower=1e-12)
    open_ = pd.to_numeric(result["open"], errors="coerce")
    high = pd.to_numeric(result["high"], errors="coerce")
    low = pd.to_numeric(result["low"], errors="coerce")
    close = pd.to_numeric(result["close"], errors="coerce")
    result["bar_range"] = high - low
    result["body"] = close - open_
    denominator = result["bar_range"].clip(lower=eps)
    result["body_ratio"] = result["body"].abs() / denominator
    result["close_pos_long"] = (close - low) / denominator
    result["close_pos_short"] = (high - close) / denominator
    result["upper_wick_ratio"] = (high - pd.concat([open_, close], axis=1).max(axis=1)) / denominator
    result["lower_wick_ratio"] = (pd.concat([open_, close], axis=1).min(axis=1) - low) / denominator

    config = feature_config
    result["atr"] = wilder_atr(high, low, close, config.atr_period)
    up_move = high.diff()
    down_move = -low.diff()
    plus_dm = pd.Series(
        np.where((up_move > down_move) & (up_move > 0), up_move, 0.0), index=result.index
    )
    minus_dm = pd.Series(
        np.where((down_move > up_move) & (down_move > 0), down_move, 0.0), index=result.index
    )
    plus_di = 100.0 * wilder_mean(plus_dm, config.adx_period) / (result["atr"] + eps)
    minus_di = 100.0 * wilder_mean(minus_dm, config.adx_period) / (result["atr"] + eps)
    result["plus_di"] = plus_di
    result["minus_di"] = minus_di
    result["adx"] = wilder_mean(
        100.0 * (plus_di - minus_di).abs() / (plus_di + minus_di + eps), config.adx_period
    )
    prior_atr_median = result["atr"].shift(1).rolling(
        config.atr_compression_window, min_periods=config.atr_compression_window
    ).median()
    result["atr_compression"] = result["atr"] / (prior_atr_median + eps)

    momentum_now = result["body"].rolling(
        config.momentum_window, min_periods=config.momentum_window
    ).sum().abs() / (config.momentum_window * result["atr"] + eps)
    momentum_prior = momentum_now.shift(config.momentum_window)
    result["momentum_now"] = momentum_now
    result["momentum_decay"] = ((momentum_prior - momentum_now) / (momentum_prior + eps)).clip(0, 1)

    bull_bar = (result["body"] > 0) & (
        result["body_ratio"] >= config.trend_bar_min_body_ratio
    )
    bear_bar = (result["body"] < 0) & (
        result["body_ratio"] >= config.trend_bar_min_body_ratio
    )
    structure_window = config.structure_window
    result["bull_trend_bar"] = bull_bar.astype(int)
    result["bear_trend_bar"] = bear_bar.astype(int)
    result["bull_trend_bar_ratio"] = bull_bar.astype(float).rolling(
        structure_window, min_periods=structure_window
    ).mean()
    result["bear_trend_bar_ratio"] = bear_bar.astype(float).rolling(
        structure_window, min_periods=structure_window
    ).mean()
    result["close_pos_long_mean"] = result["close_pos_long"].rolling(
        structure_window, min_periods=structure_window
    ).mean()
    result["close_pos_short_mean"] = result["close_pos_short"].rolling(
        structure_window, min_periods=structure_window
    ).mean()

    overlap = (pd.concat([high, high.shift(1)], axis=1).min(axis=1)
        - pd.concat([low, low.shift(1)], axis=1).max(axis=1)).clip(lower=0)
    union = pd.concat([high, high.shift(1)], axis=1).max(axis=1) - pd.concat(
        [low, low.shift(1)], axis=1
    ).min(axis=1)
    pair_overlap = overlap / union.clip(lower=eps)
    result["overlap_ratio"] = pair_overlap.rolling(
        structure_window, min_periods=structure_window
    ).mean()
    result["overlap_ratio_recent"] = pair_overlap.rolling(
        cycle_config.bo_recent_window, min_periods=cycle_config.bo_recent_window
    ).mean()
    path = close.diff().abs().rolling(structure_window, min_periods=structure_window).sum()
    result["trend_efficiency"] = (close - close.shift(structure_window)).abs() / (path + eps)
    signs = np.sign(result["body"])
    changes = (signs * signs.shift(1) < 0).astype(float)
    result["direction_change_ratio"] = changes.rolling(
        structure_window - 1, min_periods=structure_window - 1
    ).mean()

    result["ema_fast"] = close.ewm(span=config.ema_fast, adjust=False).mean()
    result["ema_slow"] = close.ewm(span=config.ema_slow, adjust=False).mean()
    result["ema_slope"] = (
        result["ema_fast"] - result["ema_fast"].shift(config.ema_slope_lookback)
    ) / (config.ema_slope_lookback * result["atr"] + eps)
    result["ema_distance"] = (close - result["ema_fast"]) / (result["atr"] + eps)
    result["ema_separation"] = (
        result["ema_fast"] - result["ema_slow"]
    ) / (result["atr"] + eps)

    result["prior_high"] = high.shift(1).rolling(
        structure_window, min_periods=structure_window
    ).max()
    result["prior_low"] = low.shift(1).rolling(
        structure_window, min_periods=structure_window
    ).min()
    result["new_high"] = (high > result["prior_high"]).astype(int)
    result["new_low"] = (low < result["prior_low"]).astype(int)
    result["breakout_distance_long"] = (close - result["prior_high"]) / (result["atr"] + eps)
    result["breakout_distance_short"] = (result["prior_low"] - close) / (result["atr"] + eps)
    result["bull_trend_bar_count_recent"] = bull_bar.astype(float).rolling(
        cycle_config.bo_recent_window, min_periods=cycle_config.bo_recent_window
    ).sum()
    result["bear_trend_bar_count_recent"] = bear_bar.astype(float).rolling(
        cycle_config.bo_recent_window, min_periods=cycle_config.bo_recent_window
    ).sum()

    result["range_high"] = high.shift(1).rolling(
        cycle_config.range_window, min_periods=cycle_config.range_window
    ).max()
    result["range_low"] = low.shift(1).rolling(
        cycle_config.range_window, min_periods=cycle_config.range_window
    ).min()
    result["range_mid"] = (result["range_high"] + result["range_low"]) / 2.0
    range_width = result["range_high"] - result["range_low"]
    result["range_pct"] = (close - result["range_low"]) / range_width.clip(lower=eps)
    result["range_width_atr"] = range_width / (result["atr"] + eps)
    cost_price = _cost_series(round_trip_cost_price, result.index)
    result["round_trip_cost_price"] = cost_price
    result["range_width_cost_multiple"] = range_width / cost_price.where(cost_price > 0)

    result["adx_percentile"] = causal_percentile_rank(
        result["adx"], config.percentile_lookback
    )
    result["atr_compression_percentile"] = causal_percentile_rank(
        result["atr_compression"], config.percentile_lookback
    )
    result["range_aux_gate"] = (
        result["adx_percentile"].le(cycle_config.range_max_adx_percentile)
        & result["atr_compression_percentile"].le(
            cycle_config.range_max_atr_compression_percentile
        )
    ).astype(float)
    result["bar_range_percentile"] = causal_percentile_rank(
        result["bar_range"], config.percentile_lookback
    )
    result["cumulative_move_atr"] = (
        close - close.shift(config.momentum_window)
    ).abs() / (result["atr"] + eps)

    pivots = confirmed_pivots(
        result, left=config.pivot_left, right=config.pivot_right
    )
    _add_structure_scores(result, pivots, config.structure_pivot_count)
    _add_channel_features(result, config, eps, cycle_config.tight_ema_cross_window)
    _add_volume_ratio(result, config.volume_bucket_lookback)
    return result.replace([np.inf, -np.inf], np.nan)


def _add_structure_scores(
    frame: pd.DataFrame,
    pivots: pd.DataFrame,
    count: int,
) -> None:
    for column in (
        "hh_score", "hl_score", "lh_score", "ll_score",
        "latest_confirmed_swing_high", "latest_confirmed_swing_low",
    ):
        frame[column] = np.nan
    if pivots.empty:
        return
    ordered = pivots.sort_values(["known_at", "pivot_position", "kind"]).to_dict("records")
    pivot_index = 0
    known_highs: list[float] = []
    known_lows: list[float] = []
    for position, event_time in enumerate(frame["bar_end"]):
        while (
            pivot_index < len(ordered)
            and pd.Timestamp(ordered[pivot_index]["known_at"]) <= pd.Timestamp(event_time)
        ):
            pivot = ordered[pivot_index]
            target = known_highs if pivot["kind"] == "HIGH" else known_lows
            target.append(float(pivot["price"]))
            pivot_index += 1
        highs = np.asarray(known_highs[-count:], dtype=float)
        lows = np.asarray(known_lows[-count:], dtype=float)
        if len(highs):
            frame.loc[position, "latest_confirmed_swing_high"] = highs[-1]
        if len(lows):
            frame.loc[position, "latest_confirmed_swing_low"] = lows[-1]
        if len(highs) >= 2:
            higher = float(np.mean(np.diff(highs) > 0))
            frame.loc[position, "hh_score"] = higher
            frame.loc[position, "lh_score"] = 1.0 - higher
        if len(lows) >= 2:
            higher = float(np.mean(np.diff(lows) > 0))
            frame.loc[position, "hl_score"] = higher
            frame.loc[position, "ll_score"] = 1.0 - higher


def _add_channel_features(
    frame: pd.DataFrame,
    config: FeatureConfig,
    eps: pd.Series,
    ema_cross_window: int,
) -> None:
    window = config.structure_window
    body = frame["body"].to_numpy(float)
    high = frame["high"].to_numpy(float)
    low = frame["low"].to_numpy(float)
    close = frame["close"].to_numpy(float)
    atr = frame["atr"].to_numpy(float)
    ema_slope = frame["ema_slope"].to_numpy(float)
    eps_values = eps.to_numpy(float)
    crosses = ((frame["close"] - frame["ema_fast"]) *
        (frame["close"].shift(1) - frame["ema_fast"].shift(1)) < 0).astype(float)
    for direction, prefix in ((1, "bull"), (-1, "bear")):
        for suffix in (
            "channel_age", "channel_slope_atr", "median_pullback_bars",
            "median_pullback_depth", "max_pullback_depth", "ema_cross_count",
            "opposite_trend_bar_ratio",
        ):
            frame[f"{prefix}_{suffix}"] = np.nan
        age = 0
        for position in range(len(frame)):
            slope = ema_slope[position]
            age = age + 1 if np.isfinite(slope) and direction * slope > 0 else 0
            frame.loc[position, f"{prefix}_channel_age"] = float(age)
            if position + 1 < window or not np.isfinite(atr[position]) or atr[position] <= 0:
                continue
            start = position - window + 1
            current_eps = eps_values[position]
            raw_slope = (close[position] - close[start]) / (
                (window - 1) * atr[position] + current_eps
            )
            lengths, depths = _pullback_runs(
                body[start : position + 1],
                high[start : position + 1],
                low[start : position + 1],
                close[start : position + 1],
                direction,
                current_eps,
            )
            frame.loc[position, f"{prefix}_channel_slope_atr"] = raw_slope
            frame.loc[position, f"{prefix}_median_pullback_bars"] = (
                float(np.median(lengths)) if lengths else 0.0
            )
            frame.loc[position, f"{prefix}_median_pullback_depth"] = (
                float(np.median(depths)) if depths else 0.0
            )
            frame.loc[position, f"{prefix}_max_pullback_depth"] = (
                float(max(depths)) if depths else 0.0
            )
            cross_window = crosses.iloc[
                max(0, position - ema_cross_window + 1) : position + 1
            ]
            frame.loc[position, f"{prefix}_ema_cross_count"] = float(cross_window.sum())
            opposite = frame["bear_trend_bar_ratio"] if direction > 0 else frame["bull_trend_bar_ratio"]
            frame.loc[position, f"{prefix}_opposite_trend_bar_ratio"] = opposite.iloc[position]


def _pullback_runs(
    body: np.ndarray,
    high: np.ndarray,
    low: np.ndarray,
    close: np.ndarray,
    direction: int,
    eps: float,
) -> tuple[list[int], list[float]]:
    adverse = direction * body < 0
    total_range = max(float(np.max(high) - np.min(low)), eps)
    lengths: list[int] = []
    depths: list[float] = []
    start: int | None = None
    for position, is_adverse in enumerate(adverse):
        if is_adverse and start is None:
            start = position
        if start is not None and (not is_adverse or position == len(adverse) - 1):
            end = position if is_adverse else position - 1
            lengths.append(end - start + 1)
            if direction > 0:
                depth = max(0.0, close[max(0, start - 1)] - float(np.min(low[start : end + 1])))
            else:
                depth = max(0.0, float(np.max(high[start : end + 1])) - close[max(0, start - 1)])
            depths.append(depth / total_range)
            start = None
    return lengths, depths


def _add_volume_ratio(frame: pd.DataFrame, lookback: int) -> None:
    if "volume" not in frame or "session_bucket" not in frame:
        frame["volume_ratio"] = np.nan
        return
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    denominator = volume.groupby(frame["session_bucket"], sort=False).transform(
        lambda values: values.shift(1).rolling(lookback, min_periods=lookback).median()
    )
    frame["volume_ratio"] = volume / denominator.where(denominator > 0)


def _validate_time_and_order(frame: pd.DataFrame) -> None:
    timestamps = pd.to_datetime(frame["bar_end"])
    if timestamps.dt.tz is None:
        raise ValueError("bar_end must be timezone-aware")
    if not timestamps.is_monotonic_increasing:
        raise ValueError("bar_end must be increasing")
    if "contract_code" in frame and frame.duplicated(["contract_code", "bar_end"]).any():
        raise ValueError("contract_code and bar_end must be unique")


def _cost_series(
    values: float | pd.Series | None,
    index: pd.Index,
) -> pd.Series:
    if values is None:
        return pd.Series(np.nan, index=index, dtype=float)
    if isinstance(values, pd.Series):
        if len(values) != len(index):
            raise ValueError("round_trip_cost_price must align one-to-one with bars")
        result = pd.to_numeric(values.reset_index(drop=True), errors="coerce").astype(float)
        result.index = index
    else:
        result = pd.Series(float(values), index=index, dtype=float)
    if (result < 0).any():
        raise ValueError("round_trip_cost_price must be nonnegative")
    return result


def _tick_series(values: float | pd.Series, index: pd.Index) -> pd.Series:
    if isinstance(values, pd.Series):
        if len(values) != len(index):
            raise ValueError("price_tick must align one-to-one with bars")
        result = pd.to_numeric(
            values.reset_index(drop=True), errors="coerce"
        ).astype(float)
        result.index = index
    else:
        result = pd.Series(float(values), index=index, dtype=float)
    if not np.isfinite(result).all() or (result <= 0).any():
        raise ValueError("price_tick must be finite and positive")
    return result


def _require_columns(frame: pd.DataFrame, columns: Iterable[str]) -> None:
    missing = [column for column in columns if column not in frame]
    if missing:
        raise ValueError(f"missing required columns: {','.join(missing)}")


__all__ = [
    "add_causal_features", "causal_percentile_rank", "confirmed_pivots", "wilder_atr",
    "wilder_mean",
]
