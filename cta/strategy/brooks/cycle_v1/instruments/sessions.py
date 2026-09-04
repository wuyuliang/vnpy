"""Versionable session contracts and causal intraday aggregation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
import math

import numpy as np
import pandas as pd


@dataclass(frozen=True)
class SessionSegment:
    segment_id: str
    start: time
    end: time
    bucket_anchor: time

    def __post_init__(self) -> None:
        if not self.segment_id:
            raise ValueError("segment_id is required")
        if self.start == self.end:
            raise ValueError("session segment must have nonzero duration")


@dataclass(frozen=True)
class SessionSpec:
    session_id: str
    is_night: bool
    segments: tuple[SessionSegment, ...]

    def __post_init__(self) -> None:
        if not self.session_id or not self.segments:
            raise ValueError("session_id and segments are required")
        ids = [segment.segment_id for segment in self.segments]
        if len(ids) != len(set(ids)):
            raise ValueError("session segment ids must be unique")


@dataclass(frozen=True)
class TradingCalendarEntry:
    exchange: str
    source_calendar_date: date
    session_id: str
    exchange_trade_date: date
    effective_from: datetime
    effective_to: datetime
    known_at: datetime
    source: str

    def __post_init__(self) -> None:
        for name in ("effective_from", "effective_to", "known_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"calendar {name} must be timezone-aware")
        if self.effective_to <= self.effective_from:
            raise ValueError("calendar effective window is invalid")


class VersionedTradingCalendar:
    """Explicit natural-date/session to exchange-trade-date mapping."""

    def __init__(self, entries: list[TradingCalendarEntry]) -> None:
        self.entries = list(entries)

    def trade_date(
        self,
        exchange: str,
        source_calendar_date: date,
        session_id: str,
        decision_asof: datetime,
    ) -> date:
        if decision_asof.tzinfo is None or decision_asof.utcoffset() is None:
            raise ValueError("calendar decision_asof must be timezone-aware")
        matched = [
            item for item in self.entries
            if item.exchange == exchange
            and item.source_calendar_date == source_calendar_date
            and item.session_id == session_id
            and item.effective_from <= decision_asof < item.effective_to
            and item.known_at <= decision_asof
        ]
        if len(matched) != 1:
            raise LookupError(
                f"BLOCKED_CALENDAR: expected one effective row, got {len(matched)}"
            )
        return matched[0].exchange_trade_date


def aggregate_completed_bars(
    frame: pd.DataFrame,
    *,
    minutes: int,
    sessions: tuple[SessionSpec, ...],
) -> pd.DataFrame:
    """Aggregate compact cycle bars without carrying scalp audit metadata."""
    if minutes < 1:
        raise ValueError("minutes must be positive")
    required = {
        "bar_end", "open", "high", "low", "close", "volume", "open_interest",
        "contract_code", "exchange_trade_date",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"missing aggregation columns: {','.join(missing)}")
    if frame.empty:
        return frame.copy()
    bars = frame.copy().sort_values(["contract_code", "bar_end"]).reset_index(drop=True)
    if bars.duplicated(["contract_code", "bar_end"]).any():
        raise ValueError("contract_code and bar_end must be unique")
    ends = pd.to_datetime(bars["bar_end"])
    if ends.dt.tz is None:
        raise ValueError("bar_end must be timezone-aware")
    bars["_segment"], bars["_bucket_end"] = _assign_segments(
        ends,
        sessions,
        minutes,
    )
    bars = bars.loc[bars["_segment"].notna()].copy()
    if bars.empty:
        return pd.DataFrame()
    bars["_feature_sequence"] = (
        bars["feature_sequence"] if "feature_sequence" in bars else 0
    )
    keys = ["contract_code", "exchange_trade_date", "_segment", "_bucket_end"]
    aggregations: dict[str, tuple[str, str]] = {
        "feature_sequence": ("_feature_sequence", "last"),
        "open": ("open", "first"),
        "high": ("high", "max"),
        "low": ("low", "min"),
        "close": ("close", "last"),
        "volume": ("volume", "sum"),
        "open_interest": ("open_interest", "last"),
        "bar_count": ("bar_end", "size"),
        "span_start": ("bar_end", "min"),
        "span_end": ("bar_end", "max"),
    }
    if "turnover" in bars:
        aggregations["turnover"] = ("turnover", "sum")
    grouped = (
        bars.groupby(keys, sort=True, dropna=False)
        .agg(**aggregations)
        .reset_index()
    )
    # Upstream uniqueness plus count and endpoint span proves no minute is
    # missing inside a bucket; bucket-tail equality preserves anchor alignment.
    complete = (
        grouped["bar_count"].eq(minutes)
        & grouped["span_end"].sub(grouped["span_start"]).eq(
            pd.Timedelta(minutes=minutes - 1)
        )
        & grouped["span_end"].eq(grouped["_bucket_end"])
    )
    grouped = grouped.loc[complete].reset_index(drop=True)
    if grouped.empty:
        return pd.DataFrame()
    result = pd.DataFrame(
        {
            "bar_end": grouped["_bucket_end"],
            "feature_asof": grouped["_bucket_end"],
            "feature_sequence": grouped["feature_sequence"].astype(int),
            "open": grouped["open"].astype(float),
            "high": grouped["high"].astype(float),
            "low": grouped["low"].astype(float),
            "close": grouped["close"].astype(float),
            "volume": grouped["volume"].astype(float),
            "open_interest": grouped["open_interest"].astype(float),
            "contract_code": grouped["contract_code"].astype(str),
            "exchange_trade_date": grouped["exchange_trade_date"],
            "segment_id": grouped["_segment"].astype(str),
            "source_max_bar_end": grouped["span_end"],
        }
    )
    if "turnover" in grouped:
        result["turnover"] = grouped["turnover"].astype(float)
    return result


def aggregate_completed_daily_bars(
    frame: pd.DataFrame,
    *,
    sessions: tuple[SessionSpec, ...],
) -> pd.DataFrame:
    """Aggregate a trade-date bar only when every declared segment is complete."""
    required = {
        "bar_end", "open", "high", "low", "close", "volume", "open_interest",
        "contract_code", "exchange_trade_date",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"missing daily aggregation columns: {','.join(missing)}")
    if frame.empty:
        return frame.copy()
    bars = frame.copy().sort_values(["contract_code", "bar_end"]).reset_index(drop=True)
    if bars.duplicated(["contract_code", "bar_end"]).any():
        raise ValueError("contract_code and bar_end must be unique")
    ends = pd.to_datetime(bars["bar_end"])
    if ends.dt.tz is None:
        raise ValueError("bar_end must be timezone-aware")
    bars["_segment"], _ = _assign_segments(ends, sessions, 1)
    bars = bars.loc[bars["_segment"].notna()].copy()
    segment_specs = {
        f"{session.session_id}:{segment.segment_id}": segment
        for session in sessions
        for segment in session.segments
    }

    if bars.empty:
        return pd.DataFrame()
    segment_keys = ["contract_code", "exchange_trade_date", "_segment"]
    segment_status = (
        bars.groupby(segment_keys, sort=True, dropna=False)
        .agg(
            bar_count=("bar_end", "size"),
            span_start=("bar_end", "min"),
            span_end=("bar_end", "max"),
        )
        .reset_index()
    )
    segment_status["expected_count"] = segment_status["_segment"].map(
        {
            segment_id: _segment_duration_minutes(segment)
            for segment_id, segment in segment_specs.items()
        }
    )
    segment_status["expected_first_minute"] = segment_status["_segment"].map(
        {
            segment_id: (_minute_of_day(segment.start) + 1) % (24 * 60)
            for segment_id, segment in segment_specs.items()
        }
    )
    segment_status["expected_last_minute"] = segment_status["_segment"].map(
        {
            segment_id: _minute_of_day(segment.end)
            for segment_id, segment in segment_specs.items()
        }
    )
    first_minute = (
        segment_status["span_start"].dt.hour * 60
        + segment_status["span_start"].dt.minute
    )
    last_minute = (
        segment_status["span_end"].dt.hour * 60
        + segment_status["span_end"].dt.minute
    )
    segment_status["complete"] = (
        segment_status["bar_count"].eq(segment_status["expected_count"])
        & segment_status["span_end"].sub(segment_status["span_start"]).eq(
            pd.to_timedelta(segment_status["expected_count"] - 1, unit="min")
        )
        & first_minute.eq(segment_status["expected_first_minute"])
        & last_minute.eq(segment_status["expected_last_minute"])
    )
    daily_keys = ["contract_code", "exchange_trade_date"]
    valid_days = (
        segment_status.groupby(daily_keys, sort=True, dropna=False)
        .agg(segment_count=("_segment", "size"), complete=("complete", "all"))
        .reset_index()
    )
    valid_days = valid_days.loc[
        valid_days["segment_count"].eq(len(segment_specs))
        & valid_days["complete"]
    ].loc[:, daily_keys]
    bars = bars.merge(valid_days, on=daily_keys, how="inner", validate="many_to_one")
    if bars.empty:
        return pd.DataFrame()
    bars = bars.sort_values([*daily_keys, "bar_end"], kind="stable")
    bars["_feature_sequence"] = (
        bars["feature_sequence"] if "feature_sequence" in bars else 0
    )
    aggregations = {
        "bar_end": ("bar_end", "last"),
        "feature_sequence": ("_feature_sequence", "last"),
        "open": ("open", "first"),
        "high": ("high", "max"),
        "low": ("low", "min"),
        "close": ("close", "last"),
        "volume": ("volume", "sum"),
        "open_interest": ("open_interest", "last"),
        "source_max_bar_end": ("bar_end", "max"),
    }
    if "turnover" in bars:
        aggregations["turnover"] = ("turnover", "sum")
    grouped = (
        bars.groupby(daily_keys, sort=True, dropna=False)
        .agg(**aggregations)
        .reset_index()
    )
    result = pd.DataFrame(
        {
            "bar_end": grouped["bar_end"],
            "feature_asof": grouped["bar_end"],
            "feature_sequence": grouped["feature_sequence"].astype(int),
            "open": grouped["open"].astype(float),
            "high": grouped["high"].astype(float),
            "low": grouped["low"].astype(float),
            "close": grouped["close"].astype(float),
            "volume": grouped["volume"].astype(float),
            "open_interest": grouped["open_interest"].astype(float),
            "contract_code": grouped["contract_code"].astype(str),
            "exchange_trade_date": grouped["exchange_trade_date"],
            "segment_id": "DAILY",
            "source_max_bar_end": grouped["source_max_bar_end"],
        }
    )
    if "turnover" in grouped:
        result["turnover"] = grouped["turnover"].astype(float)
    return result


def _assign_segments(
    ends: pd.Series,
    sessions: tuple[SessionSpec, ...],
    minutes: int,
) -> tuple[pd.Series, pd.Series]:
    minute_of_day = ends.dt.hour * 60 + ends.dt.minute
    assigned = pd.Series(False, index=ends.index)
    segment_ids = pd.Series(None, index=ends.index, dtype="object")
    bucket_ends = pd.Series(pd.NaT, index=ends.index, dtype=ends.dtype)
    local_midnights = ends.dt.normalize()

    for session in sessions:
        for segment in session.segments:
            start_mod = _minute_of_day(segment.start)
            end_mod = _minute_of_day(segment.end)
            if start_mod < end_mod:
                in_segment = minute_of_day.gt(start_mod) & minute_of_day.le(end_mod)
            else:
                in_segment = minute_of_day.gt(start_mod) | minute_of_day.le(end_mod)
            selected = in_segment & ~assigned
            if not selected.any():
                continue
            anchor_dates = local_midnights.loc[selected].copy()
            if start_mod > end_mod:
                after_midnight = minute_of_day.loc[selected].le(end_mod)
                anchor_dates.loc[after_midnight] -= pd.Timedelta(days=1)
            anchor = anchor_dates + pd.Timedelta(
                hours=segment.bucket_anchor.hour,
                minutes=segment.bucket_anchor.minute,
                seconds=segment.bucket_anchor.second,
                microseconds=segment.bucket_anchor.microsecond,
            )
            delta_minutes = (
                ends.loc[selected].sub(anchor).dt.total_seconds().to_numpy()
                / 60.0
            )
            bucket_number = np.ceil(
                np.maximum(delta_minutes, 1e-12) / minutes
            ).astype(np.int64)
            segment_ids.loc[selected] = (
                f"{session.session_id}:{segment.segment_id}"
            )
            bucket_ends.loc[selected] = (
                anchor
                + pd.to_timedelta(bucket_number * minutes, unit="min")
            )
            assigned.loc[selected] = True
    return segment_ids, bucket_ends


def _minute_of_day(value: time) -> int:
    return value.hour * 60 + value.minute


def _complete_minute_segment(group: pd.DataFrame, segment: SessionSegment) -> bool:
    if group.empty:
        return False
    duration = _segment_duration_minutes(segment)
    ends = pd.DatetimeIndex(pd.to_datetime(group.sort_values("bar_end")["bar_end"]))
    if len(ends) != duration:
        return False
    expected = pd.date_range(ends[-1] - pd.Timedelta(minutes=duration - 1), ends[-1], freq="1min")
    expected_first_time = (
        datetime.combine(date.min, segment.start) + timedelta(minutes=1)
    ).time()
    return bool(
        ends.equals(expected)
        and ends[0].timetz().replace(tzinfo=None) == expected_first_time
        and ends[-1].timetz().replace(tzinfo=None) == segment.end
    )


def _segment_duration_minutes(segment: SessionSegment) -> int:
    start = datetime.combine(date.min, segment.start)
    end_date = date.min + timedelta(days=1) if segment.start > segment.end else date.min
    end = datetime.combine(end_date, segment.end)
    return int((end - start).total_seconds() // 60)


def _assign_segment(
    timestamp: pd.Timestamp,
    sessions: tuple[SessionSpec, ...],
    minutes: int,
) -> tuple[str, pd.Timestamp] | None:
    current = timestamp.timetz().replace(tzinfo=None)
    for session in sessions:
        for segment in session.segments:
            if not _time_in_segment(current, segment.start, segment.end):
                continue
            anchor_date = timestamp.date()
            if segment.start > segment.end and current <= segment.end:
                anchor_date -= timedelta(days=1)
            anchor = pd.Timestamp.combine(anchor_date, segment.bucket_anchor).tz_localize(timestamp.tz)
            delta_minutes = (timestamp - anchor).total_seconds() / 60.0
            bucket_number = math.ceil(max(delta_minutes, 1e-12) / minutes)
            bucket_end = anchor + pd.Timedelta(minutes=bucket_number * minutes)
            return f"{session.session_id}:{segment.segment_id}", bucket_end
    return None


def _time_in_segment(value: time, start: time, end: time) -> bool:
    if start < end:
        return start < value <= end
    return value > start or value <= end


__all__ = [
    "SessionSegment", "SessionSpec", "TradingCalendarEntry", "VersionedTradingCalendar",
    "aggregate_completed_bars", "aggregate_completed_daily_bars",
]
