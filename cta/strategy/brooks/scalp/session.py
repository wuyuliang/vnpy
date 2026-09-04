"""Exchange-calendar-backed Chinese futures session assignment."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo


SHANGHAI_TZ = ZoneInfo("Asia/Shanghai")
ONE_MINUTE = timedelta(minutes=1)


class SessionError(ValueError):
    """Raised when a bar cannot be assigned without guessing session semantics."""

    def __init__(self, message: str, *, code: str = "INVALID_SESSION") -> None:
        super().__init__(message)
        self.code = code


def ensure_shanghai(value: datetime, field_name: str) -> datetime:
    if value.tzinfo is None or value.utcoffset() is None:
        raise SessionError(
            f"{field_name} must be timezone-aware Asia/Shanghai",
            code="NAIVE_DATETIME",
        )
    return value.astimezone(SHANGHAI_TZ)


@dataclass(frozen=True)
class SessionSegment:
    segment_id: str
    start: time
    end: time
    bucket_anchor: time

    def __post_init__(self) -> None:
        if not self.segment_id:
            raise ValueError("segment_id is required")
        for value in (self.start, self.end, self.bucket_anchor):
            if value.tzinfo is not None:
                raise ValueError("session wall-clock times must be timezone-naive")


@dataclass(frozen=True)
class SessionSpec:
    session_id: str
    is_night: bool
    segments: tuple[SessionSegment, ...]

    def __post_init__(self) -> None:
        if not self.session_id:
            raise ValueError("session_id is required")
        if not self.segments:
            raise ValueError("a session requires at least one segment")
        segment_ids = [segment.segment_id for segment in self.segments]
        if len(segment_ids) != len(set(segment_ids)):
            raise ValueError(f"duplicate segment ids in session {self.session_id}")

    def to_dict(self) -> dict[str, object]:
        return {
            "session_id": self.session_id,
            "is_night": self.is_night,
            "segments": [
                {
                    "segment_id": segment.segment_id,
                    "start": segment.start.isoformat(),
                    "end": segment.end.isoformat(),
                    "bucket_anchor": segment.bucket_anchor.isoformat(),
                }
                for segment in self.segments
            ],
        }


@dataclass(frozen=True)
class TradingCalendarEntry:
    exchange: str
    exchange_trade_date: date
    is_open: bool
    prior_open_date: date
    next_open_date: date
    night_session_start: time | None
    source: str
    known_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "exchange", self.exchange.upper())
        object.__setattr__(self, "known_at", ensure_shanghai(self.known_at, "known_at"))
        if not self.source:
            raise ValueError("calendar source is required")


@dataclass(frozen=True)
class SessionAssignment:
    exchange_trade_date: date
    session_id: str
    session_kind: str
    segment_id: str
    session_open: datetime
    session_close: datetime
    segment_start: datetime
    segment_end: datetime
    bucket_anchor: datetime
    calendar_sha256: str


@dataclass(frozen=True)
class _SegmentWindow:
    exchange_trade_date: date
    session_id: str
    session_kind: str
    segment_id: str
    session_open: datetime
    session_close: datetime
    segment_start: datetime
    segment_end: datetime
    bucket_anchor: datetime


def _combine(day: date, value: time) -> datetime:
    return datetime.combine(day, value, tzinfo=SHANGHAI_TZ)


class SessionCalendar:
    """Map one-minute intervals to explicit exchange sessions and segments."""

    def __init__(
        self,
        *,
        exchange: str,
        entries: tuple[TradingCalendarEntry, ...],
        sessions: tuple[SessionSpec, ...],
        calendar_sha256: str,
    ) -> None:
        self.exchange = exchange.upper()
        self.entries = tuple(entries)
        self.sessions = tuple(sessions)
        self.calendar_sha256 = calendar_sha256
        if not self.entries:
            raise SessionError("trading calendar is empty", code="MISSING_CALENDAR")
        if not self.sessions:
            raise SessionError("session templates are empty", code="MISSING_SESSION_TEMPLATE")
        if not calendar_sha256:
            raise SessionError("calendar_sha256 is required", code="MISSING_CALENDAR_HASH")

        dates = [entry.exchange_trade_date for entry in self.entries]
        if len(dates) != len(set(dates)):
            raise SessionError("duplicate exchange trade dates", code="DUPLICATE_CALENDAR_DATE")
        if dates != sorted(dates):
            raise SessionError("calendar entries must be ordered", code="OUT_OF_ORDER_CALENDAR")
        if any(entry.exchange != self.exchange for entry in self.entries):
            raise SessionError("calendar exchange mismatch", code="CALENDAR_EXCHANGE_MISMATCH")

        self._windows_by_date: dict[date, list[_SegmentWindow]] = {}
        for entry in self.entries:
            if entry.is_open:
                self._add_entry_windows(entry)

    def _add_entry_windows(self, entry: TradingCalendarEntry) -> None:
        for session in self.sessions:
            if session.is_night and entry.night_session_start is None:
                continue
            raw_segments: list[tuple[SessionSegment, datetime, datetime, datetime]] = []
            for segment in session.segments:
                if session.is_night:
                    assert entry.night_session_start is not None
                    start_day = entry.prior_open_date
                    if segment.start < entry.night_session_start:
                        start_day += timedelta(days=1)
                else:
                    start_day = entry.exchange_trade_date

                segment_start = _combine(start_day, segment.start)
                end_day = start_day
                if segment.end <= segment.start:
                    end_day += timedelta(days=1)
                segment_end = _combine(end_day, segment.end)

                anchor_day = start_day
                if segment.bucket_anchor < segment.start and segment.end > segment.start:
                    anchor_day += timedelta(days=1)
                bucket_anchor = _combine(anchor_day, segment.bucket_anchor)
                if not (segment_start <= bucket_anchor < segment_end):
                    raise SessionError(
                        f"bucket anchor is outside segment {segment.segment_id}",
                        code="INVALID_BUCKET_ANCHOR",
                    )
                raw_segments.append((segment, segment_start, segment_end, bucket_anchor))

            session_open = min(item[1] for item in raw_segments)
            session_close = max(item[2] for item in raw_segments)
            if entry.known_at > session_open:
                raise SessionError(
                    f"calendar known_at is later than {session.session_id} open",
                    code="CALENDAR_KNOWN_LATE",
                )
            full_session_id = f"{entry.exchange_trade_date:%Y%m%d}:{session.session_id}"
            for segment, segment_start, segment_end, bucket_anchor in raw_segments:
                window = _SegmentWindow(
                    exchange_trade_date=entry.exchange_trade_date,
                    session_id=full_session_id,
                    session_kind=session.session_id,
                    segment_id=segment.segment_id,
                    session_open=session_open,
                    session_close=session_close,
                    segment_start=segment_start,
                    segment_end=segment_end,
                    bucket_anchor=bucket_anchor,
                )
                current_day = segment_start.date()
                while current_day <= segment_end.date():
                    self._windows_by_date.setdefault(current_day, []).append(window)
                    current_day += timedelta(days=1)

    def assign_bar(self, bar_start: datetime, bar_end: datetime) -> SessionAssignment:
        start = ensure_shanghai(bar_start, "bar_start")
        end = ensure_shanghai(bar_end, "bar_end")
        if end - start != ONE_MINUTE:
            raise SessionError(
                "only exact one-minute bars can be assigned",
                code="INVALID_BAR_DURATION",
            )
        if any((start.second, start.microsecond, end.second, end.microsecond)):
            raise SessionError("bar timestamps must be minute-aligned", code="UNALIGNED_BAR")

        matches: list[_SegmentWindow] = []
        seen: set[tuple[str, str]] = set()
        for lookup_day in {start.date(), end.date()}:
            for window in self._windows_by_date.get(lookup_day, ()):  # pragma: no branch
                key = (window.session_id, window.segment_id)
                if key in seen:
                    continue
                seen.add(key)
                if start >= window.segment_start and end <= window.segment_end:
                    matches.append(window)
        if not matches:
            raise SessionError(
                f"bar {start.isoformat()} -> {end.isoformat()} is outside configured session segments",
                code="OUTSIDE_SESSION",
            )
        if len(matches) != 1:
            raise SessionError(
                f"bar maps to multiple session segments: {matches}",
                code="AMBIGUOUS_SESSION",
            )
        window = matches[0]
        return SessionAssignment(
            exchange_trade_date=window.exchange_trade_date,
            session_id=window.session_id,
            session_kind=window.session_kind,
            segment_id=window.segment_id,
            session_open=window.session_open,
            session_close=window.session_close,
            segment_start=window.segment_start,
            segment_end=window.segment_end,
            bucket_anchor=window.bucket_anchor,
            calendar_sha256=self.calendar_sha256,
        )


__all__ = [
    "SHANGHAI_TZ",
    "SessionAssignment",
    "SessionCalendar",
    "SessionError",
    "SessionSegment",
    "SessionSpec",
    "TradingCalendarEntry",
    "ensure_shanghai",
]
