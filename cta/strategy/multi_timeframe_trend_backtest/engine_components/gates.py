"""Session, liquidity, and cross-break replay gates."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date
import math
from typing import Any

import numpy as np
import pandas as pd

from cta.config.multi_timeframe_trend_config import (
    MultiTimeframeTrendConfig,
    is_entry_window_blocked,
    minute_of_day_from_time,
)

from ..diagnostics import GateFailOpenDiagnostics, observe_gate
from .models import _Position

def _session_break_closes(sessions: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return the closing time of each session that is followed by a long break.

    A session's last segment end is the point after which the market is shut for
    hours: the day session closes into the evening gap, the night session closes
    into the overnight gap. The midday recess is a segment boundary inside one
    session and is deliberately not treated as a break.
    """
    closes = []
    for session in sessions:
        if not session.segments:
            continue
        closes.append(session.segments[-1].end)
    if not closes:
        raise ValueError("portfolio replay requires at least one session close")
    return tuple(closes)


def _is_pre_break_time(
    timestamp: pd.Timestamp,
    sessions: tuple[Any, ...],
    lead_minutes: int,
) -> bool:
    """Return whether ``timestamp`` sits in the lead-in to any session close."""
    current = minute_of_day_from_time(timestamp.timetz())
    for close_time in _session_break_closes(sessions):
        close = minute_of_day_from_time(close_time)
        start = (close - int(lead_minutes)) % 1440
        if start <= close:
            if start <= current <= close:
                return True
        elif current >= start or current <= close:
            return True
    return False

def turnover_eligible_by_date(
    table: pd.DataFrame | None,
    *,
    share: float,
    lookback_days: int,
    diagnostics: GateFailOpenDiagnostics | None = None,
) -> dict[date, frozenset[str]]:
    """Map each trade date to the roots covering ``share`` of recent turnover.

    Turnover is summed over the ``lookback_days`` completed trade dates strictly
    before each date, so the cohort for a day never sees that day's own volume.
    Roots are ranked high to low and the smallest prefix whose cumulative share
    reaches the threshold is eligible. An empty or missing table yields an empty
    mapping, and callers treat that as "cannot judge" rather than "nobody
    qualifies".
    """
    if share <= 0:
        return {}
    if table is None or table.empty:
        observe_gate(diagnostics, "turnover_table_unavailable", fail_open=True)
        return {}
    required = {"root_symbol", "trade_date", "turnover"}
    if not required.issubset(table.columns):
        observe_gate(diagnostics, "turnover_table_unavailable", fail_open=True)
        return {}
    frame = table.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date"])
    if frame.empty:
        observe_gate(diagnostics, "turnover_table_unavailable", fail_open=True)
        return {}
    observe_gate(diagnostics, "turnover_table_unavailable")
    frame["root_symbol"] = frame["root_symbol"].astype(str).str.upper()
    frame["turnover"] = pd.to_numeric(frame["turnover"], errors="coerce").fillna(0.0)
    pivot = (
        frame.pivot_table(
            index="trade_date",
            columns="root_symbol",
            values="turnover",
            aggfunc="sum",
        )
        .sort_index()
        .fillna(0.0)
    )
    window = max(1, int(lookback_days))
    rolled = pivot.rolling(window, min_periods=1).sum().shift(1)
    result: dict[date, frozenset[str]] = {}
    for stamp, row in rolled.iterrows():
        values = row.dropna()
        values = values[values > 0]
        if values.empty:
            continue
        ordered = values.sort_values(ascending=False)
        cumulative = ordered.cumsum() / ordered.sum()
        keep = int((cumulative < float(share)).sum()) + 1
        result[pd.Timestamp(stamp).date()] = frozenset(ordered.index[:keep])
    return result


def _turnover_share_blocked(
    root_symbol: str,
    trade_date: Any,
    eligible_by_date: dict[date, frozenset[str]],
    *,
    diagnostics: GateFailOpenDiagnostics | None = None,
) -> str:
    """Return a rejection detail when the root is outside the turnover cohort."""
    if not eligible_by_date:
        return ""
    cohort = eligible_by_date.get(pd.Timestamp(trade_date).date())
    if cohort is None:
        observe_gate(
            diagnostics, "turnover_cohort_missing_date", fail_open=True
        )
        return ""
    observe_gate(diagnostics, "turnover_cohort_missing_date")
    if str(root_symbol).upper() in cohort:
        return ""
    return f"cohort_size={len(cohort)}"


def _order_crossed_recess(
    active_at: pd.Timestamp,
    timestamp: pd.Timestamp,
    max_recess_minutes: int,
) -> bool:
    """Return whether a resting order has sat through a recess it must not survive.

    An order carries the information of the bar that produced it. Once the market
    has been shut longer than ``max_recess_minutes`` that information is stale, so
    the order is cancelled rather than matched against a reopening print. Bars are
    at most one minute apart inside a session, so any gap beyond the threshold
    between the order becoming active and the current bar is a recess.
    """
    if max_recess_minutes <= 0:
        return False
    elapsed = (timestamp - active_at).total_seconds() / 60.0
    return elapsed > float(max_recess_minutes)


def _entry_blocked_at_match(
    active_at: pd.Timestamp,
    timestamp: pd.Timestamp,
    config: MultiTimeframeTrendConfig,
) -> str:
    """Re-check the entry gates that must hold at match time, not only at signal time.

    A candidate is screened when it arrives, but the resting stop order can fill
    minutes or hours later. Without this second check an order placed before the
    midday recess fills inside a blocked afternoon window.
    """
    if is_entry_window_blocked(
        minute_of_day_from_time(timestamp.timetz()),
        config.entry_blocked_session_windows,
    ):
        return "ENTRY_SESSION_WINDOW_BLOCKED"
    if _order_crossed_recess(
        active_at, timestamp, config.order_max_recess_minutes
    ):
        return "ORDER_CROSSED_SESSION_RECESS"
    return ""


def _unrealized_r(position: "_Position", mark: float) -> float:
    """Return the position's open profit in units of its entry-locked 1R."""
    risk = float(position.initial_risk_cash)
    if not math.isfinite(risk) or risk <= 0:
        return math.nan
    direction = int(position.pending.candidate["direction"])
    multiplier = float(position.current_metadata.contract_size)
    move = direction * (float(mark) - float(position.entry_price))
    return move * position.quantity * multiplier / risk


@dataclass(frozen=True)
class _PreBreakDecision:
    reason: str = ""
    close_quantity: int = 0
    open_r: float = math.nan
    threshold: float = math.nan
    high_gap: bool = False


def _pre_break_decision(
    position: _Position,
    *,
    mark: float,
    trade_date: date,
    high_gap_by_date: dict[date, bool],
    config: MultiTimeframeTrendConfig,
) -> _PreBreakDecision:
    """Return the shared cross-break risk action for one open position."""
    open_r = _unrealized_r(position, mark)
    if not math.isfinite(open_r):
        return _PreBreakDecision()
    high_gap = bool(high_gap_by_date.get(trade_date, False))
    threshold = (
        float(config.pre_break_high_gap_min_unrealized_r)
        if high_gap
        else float(config.pre_break_min_unrealized_r)
    )
    if open_r < threshold:
        return _PreBreakDecision(
            "PRE_BREAK_NO_BUFFER",
            int(position.quantity),
            open_r,
            threshold,
            high_gap,
        )
    if high_gap and float(config.pre_break_gap_risk_scale) < 1.0:
        kept = int(
            math.floor(
                position.quantity * float(config.pre_break_gap_risk_scale)
            )
        )
        return _PreBreakDecision(
            "PRE_BREAK_GAP_RISK_REDUCTION",
            int(position.quantity) - kept,
            open_r,
            threshold,
            high_gap,
        )
    return _PreBreakDecision(
        open_r=open_r,
        threshold=threshold,
        high_gap=high_gap,
    )


def _high_gap_flags_by_trade_date(
    daily_context: pd.DataFrame | None,
    config: MultiTimeframeTrendConfig,
    *,
    diagnostics: GateFailOpenDiagnostics | None = None,
) -> dict[date, bool]:
    """Classify each trade date as high-gap using only prior completed days.

    The measure is ``|open - previous close| / previous ATR14``. Its rolling
    quantile is shifted by one bar so a day is never classified with its own
    gap, keeping the flag causal.
    """
    if daily_context is None or daily_context.empty:
        observe_gate(diagnostics, "high_gap_classification", fail_open=True)
        return {}
    observe_gate(diagnostics, "high_gap_classification")
    frame = daily_context
    required = {"open", "close", "daily_atr14"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(
            "daily_context is missing required high-gap columns: "
            + ",".join(missing)
        )
    if "exchange_trade_date" in frame.columns:
        keys = pd.to_datetime(frame["exchange_trade_date"], errors="coerce")
    elif "bar_end" in frame.columns:
        keys = pd.to_datetime(frame["bar_end"], errors="coerce", utc=True)
        keys = keys.dt.tz_convert("Asia/Shanghai")
    else:
        raise ValueError(
            "daily_context requires exchange_trade_date or bar_end for high-gap classification"
        )
    open_price = pd.to_numeric(frame["open"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    atr_value = pd.to_numeric(frame["daily_atr14"], errors="coerce")
    ratio = (open_price - close.shift(1)).abs() / atr_value.shift(1)
    ratio = ratio.replace([np.inf, -np.inf], np.nan)
    rolling = ratio.rolling(
        int(config.high_gap_lookback_days),
        min_periods=max(10, int(config.high_gap_lookback_days) // 3),
    ).quantile(float(config.high_gap_quantile))
    flags = rolling.shift(1) >= float(config.high_gap_ratio_threshold)
    result: dict[date, bool] = {}
    for key, flag in zip(keys, flags):
        if pd.isna(key):
            continue
        result[pd.Timestamp(key).date()] = bool(flag)
    return result


def _is_overnight_reduction_time(
    timestamp: pd.Timestamp,
    sessions: tuple[Any, ...],
    lead_minutes: int,
    *,
    bounds_cache: dict[
        tuple[Any, ...], tuple[pd.Timestamp, pd.Timestamp]
    ] | None = None,
    sessions_id: tuple[Any, ...] | None = None,
) -> bool:
    resolved_sessions_id = sessions_id or _sessions_cache_id(sessions)
    key = (
        resolved_sessions_id,
        timestamp.date(),
        str(timestamp.tz),
        int(lead_minutes),
    )
    bounds = bounds_cache.get(key) if bounds_cache is not None else None
    if bounds is None:
        day_ends = [
            segment.end
            for session in sessions
            if not bool(session.is_night)
            for segment in session.segments
        ]
        if not day_ends:
            raise ValueError("portfolio replay requires a day-session close")
        close_time = max(day_ends)
        close = pd.Timestamp.combine(timestamp.date(), close_time).tz_localize(
            timestamp.tz
        )
        bounds = (close - pd.Timedelta(minutes=lead_minutes), close)
        if bounds_cache is not None:
            bounds_cache[key] = bounds
    cutoff, close = bounds
    return bool(cutoff <= timestamp <= close)


def _sessions_cache_id(sessions: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(
        (
            str(session.session_id),
            bool(session.is_night),
            tuple(
                (
                    str(segment.segment_id),
                    segment.start,
                    segment.end,
                    segment.bucket_anchor,
                )
                for segment in session.segments
            ),
        )
        for session in sessions
    )


def _is_night_timestamp(
    timestamp: pd.Timestamp,
    sessions: tuple[Any, ...],
) -> bool:
    value = timestamp.timetz().replace(tzinfo=None)
    for session in sessions:
        if not bool(session.is_night):
            continue
        for segment in session.segments:
            if segment.start < segment.end:
                inside = segment.start < value <= segment.end
            else:
                inside = value > segment.start or value <= segment.end
            if inside:
                return True
    return False


def _is_session_timestamp(
    timestamp: pd.Timestamp,
    sessions: tuple[Any, ...],
) -> bool:
    value = timestamp.timetz().replace(tzinfo=None)
    for session in sessions:
        for segment in session.segments:
            if segment.start < segment.end:
                inside = segment.start < value <= segment.end
            else:
                inside = value > segment.start or value <= segment.end
            if inside:
                return True
    return False
