"""As-of instrument research profiles and eligibility gates."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
import math

import numpy as np
import pandas as pd

from ..config import ProfileConfig


@dataclass(frozen=True)
class TimeframeConfig:
    base_tf: str
    large_tf: str
    medium_tf: str
    small_tf: str
    execution_tf: str


@dataclass(frozen=True)
class TimeframeMetrics:
    timeframe: str
    duration_minutes: int
    complete_history_bars: int
    bars_per_session: float
    median_atr_ticks: float
    median_cost_to_atr: float
    zero_volume_ratio: float
    bar_gap_ratio: float

    def __post_init__(self) -> None:
        values = (
            self.bars_per_session,
            self.median_atr_ticks,
            self.median_cost_to_atr,
            self.zero_volume_ratio,
            self.bar_gap_ratio,
        )
        if not self.timeframe or self.duration_minutes <= 0 or self.complete_history_bars < 0:
            raise ValueError("invalid timeframe identity or history")
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise ValueError("timeframe metrics must be finite and nonnegative")


@dataclass(frozen=True)
class TimeframeSelection:
    timeframes: TimeframeConfig | None
    reason_codes: tuple[str, ...]


@dataclass(frozen=True)
class InstrumentResearchProfile:
    root_symbol: str
    sector: str
    timeframe_config: TimeframeConfig
    median_atr_ticks: float
    median_cost_to_atr: float
    session_gap_atr_p95: float
    zero_volume_ratio: float
    bar_gap_ratio: float
    roll_frequency: float
    limit_event_rate: float
    history_bars: int
    history_sessions: int
    metadata_coverage: float
    profile_asof: datetime

    def __post_init__(self) -> None:
        if self.profile_asof.tzinfo is None or self.profile_asof.utcoffset() is None:
            raise ValueError("profile_asof must be timezone-aware")


@dataclass(frozen=True)
class ProfileEligibility:
    eligible: bool
    reason_codes: tuple[str, ...]


def calculate_timeframe_metrics(
    frame: pd.DataFrame,
    *,
    timeframe: str,
    duration_minutes: int,
    price_tick: float,
) -> TimeframeMetrics:
    """Build profile inputs from an explicit completed-bar quality prefix."""
    required = {
        "bar_end", "exchange_trade_date", "atr", "round_trip_cost_price", "volume",
        "unexpected_gap",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"missing timeframe metric columns: {','.join(missing)}")
    if frame.empty or price_tick <= 0:
        raise ValueError("timeframe metrics require bars and a positive price tick")
    timestamps = pd.to_datetime(frame["bar_end"])
    if (
        timestamps.dt.tz is None
        or not timestamps.is_monotonic_increasing
        or timestamps.duplicated().any()
    ):
        raise ValueError("timeframe bars must be timezone-aware and strictly increasing")
    atr = pd.to_numeric(frame["atr"], errors="coerce")
    cost = pd.to_numeric(frame["round_trip_cost_price"], errors="coerce")
    volume = pd.to_numeric(frame["volume"], errors="coerce")
    if (
        not np.isfinite(atr).all()
        or not np.isfinite(cost).all()
        or not np.isfinite(volume).all()
        or (atr <= 0).any()
        or (cost < 0).any()
        or (volume < 0).any()
    ):
        raise ValueError("timeframe metric inputs must be finite with valid signs")
    gap = frame["unexpected_gap"]
    if not gap.isin([True, False]).all():
        raise ValueError("unexpected_gap must be explicit booleans")
    bars_per_session = frame.groupby("exchange_trade_date", sort=False).size()
    return TimeframeMetrics(
        timeframe=timeframe,
        duration_minutes=duration_minutes,
        complete_history_bars=len(frame),
        bars_per_session=float(bars_per_session.median()),
        median_atr_ticks=float((atr / price_tick).median()),
        median_cost_to_atr=float((cost / atr).median()),
        zero_volume_ratio=float(volume.eq(0).mean()),
        bar_gap_ratio=float(gap.astype(bool).mean()),
    )


def evaluate_profile(
    profile: InstrumentResearchProfile,
    config: ProfileConfig,
) -> ProfileEligibility:
    reasons: list[str] = []
    if profile.history_sessions < config.min_history_sessions:
        reasons.append("BLOCKED_INSUFFICIENT_HISTORY")
    if profile.median_atr_ticks < config.min_atr_ticks:
        reasons.append("BLOCKED_ATR_TICKS")
    if profile.median_cost_to_atr > config.max_cost_to_atr:
        reasons.append("BLOCKED_COST_TO_ATR")
    if profile.zero_volume_ratio > config.max_zero_volume_ratio:
        reasons.append("BLOCKED_ZERO_VOLUME")
    if profile.bar_gap_ratio > config.max_gap_ratio:
        reasons.append("BLOCKED_BAR_GAP")
    if profile.metadata_coverage != 1.0:
        reasons.append("BLOCKED_METADATA")
    return ProfileEligibility(not reasons, tuple(reasons))


def select_timeframes(
    metrics: Mapping[str, TimeframeMetrics],
    config: ProfileConfig,
    *,
    base_tf: str,
    execution_tf: str,
) -> TimeframeSelection:
    """Select structural roles by frozen data-quality and duration rules only."""
    if base_tf not in metrics:
        return TimeframeSelection(None, ("BLOCKED_BASE_TIMEFRAME",))
    if execution_tf not in metrics:
        return TimeframeSelection(None, ("BLOCKED_EXECUTION_TIMEFRAME",))

    small_pool = _usable_pool(metrics, config.small_tf_candidates, "small", config)
    if not small_pool:
        return TimeframeSelection(None, ("BLOCKED_SMALL_TIMEFRAME",))
    small = min(small_pool, key=lambda item: item.duration_minutes)

    medium_pool = _usable_pool(metrics, config.medium_tf_candidates, "medium", config)
    medium = _choose_ratio(
        medium_pool,
        base_minutes=small.duration_minutes,
        target=config.target_medium_small_ratio,
    )
    if medium is None:
        return TimeframeSelection(None, ("BLOCKED_MEDIUM_TIMEFRAME",))

    large_pool = _usable_pool(metrics, config.large_tf_candidates, "large", config)
    large = _choose_ratio(
        large_pool,
        base_minutes=medium.duration_minutes,
        target=config.target_large_medium_ratio,
    )
    if large is None:
        return TimeframeSelection(None, ("BLOCKED_LARGE_TIMEFRAME",))
    return TimeframeSelection(
        TimeframeConfig(
            base_tf=base_tf,
            large_tf=large.timeframe,
            medium_tf=medium.timeframe,
            small_tf=small.timeframe,
            execution_tf=execution_tf,
        ),
        (),
    )


def timeframe_usable(
    metrics: TimeframeMetrics,
    role: str,
    config: ProfileConfig,
) -> bool:
    if role not in {"small", "medium", "large"}:
        raise ValueError("timeframe role must be small, medium, or large")
    history_required = (
        config.min_large_history_bars
        if role == "large"
        else config.min_intraday_history_bars
    )
    density_ok = role == "large" or metrics.bars_per_session >= (
        config.min_small_bars_per_session
        if role == "small"
        else config.min_medium_bars_per_session
    )
    cost_ok = role == "large" or metrics.median_cost_to_atr <= config.max_cost_to_atr
    return bool(
        metrics.complete_history_bars >= history_required
        and density_ok
        and metrics.median_atr_ticks >= config.min_atr_ticks
        and cost_ok
        and metrics.zero_volume_ratio <= config.max_zero_volume_ratio
        and metrics.bar_gap_ratio <= config.max_gap_ratio
    )


def _usable_pool(
    metrics: Mapping[str, TimeframeMetrics],
    candidates: tuple[str, ...],
    role: str,
    config: ProfileConfig,
) -> list[TimeframeMetrics]:
    return [
        metrics[timeframe]
        for timeframe in candidates
        if timeframe in metrics and timeframe_usable(metrics[timeframe], role, config)
    ]


def _choose_ratio(
    pool: list[TimeframeMetrics],
    *,
    base_minutes: int,
    target: int,
) -> TimeframeMetrics | None:
    longer = [item for item in pool if item.duration_minutes > base_minutes]
    if not longer:
        return None
    return min(
        longer,
        key=lambda item: (
            abs(math.log(item.duration_minutes / base_minutes / target)),
            -item.duration_minutes,
        ),
    )


__all__ = [
    "InstrumentResearchProfile", "ProfileEligibility", "TimeframeConfig",
    "TimeframeMetrics", "TimeframeSelection", "calculate_timeframe_metrics",
    "evaluate_profile", "select_timeframes", "timeframe_usable",
]
