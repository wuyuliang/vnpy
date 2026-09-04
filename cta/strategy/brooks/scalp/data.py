"""Fail-closed RB/CU one-minute normalization and causal aggregation."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta
import re
from typing import Any

import numpy as np
import pandas as pd

from .metadata import BlockedMetadataError, DailyTradingSpec, InstrumentSpec
from .session import SessionCalendar, SessionError, ensure_shanghai


CANONICAL_BAR_COLUMNS = (
    "source_calendar_date",
    "exchange_trade_date",
    "session_id",
    "session_kind",
    "segment_id",
    "bar_start",
    "bar_end",
    "root_symbol",
    "vt_symbol",
    "contract_code",
    "exchange",
    "open",
    "high",
    "low",
    "close",
    "volume",
    "turnover",
    "open_interest",
    "pre_settlement",
    "limit_up",
    "limit_down",
    "limit_source",
    "limit_known_at",
    "source_path",
    "source_row",
    "calendar_sha256",
    "session_open",
    "session_close",
    "segment_start",
    "segment_end",
    "bucket_anchor",
)


class DataQualityError(ValueError):
    """Data failure that must stop the run rather than drop a row."""

    def __init__(self, message: str, *, code: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class MinuteBar:
    source_calendar_date: date
    exchange_trade_date: date
    session_id: str
    session_kind: str
    segment_id: str
    bar_start: datetime
    bar_end: datetime
    root_symbol: str
    vt_symbol: str
    contract_code: str
    exchange: str
    open: float
    high: float
    low: float
    close: float
    volume: float
    turnover: float
    open_interest: float
    pre_settlement: float
    limit_up: float
    limit_down: float
    limit_source: str
    limit_known_at: datetime
    source_path: str
    source_row: int
    calendar_sha256: str
    session_open: datetime
    session_close: datetime
    segment_start: datetime
    segment_end: datetime
    bucket_anchor: datetime

    @classmethod
    def from_mapping(cls, value: Mapping[str, Any]) -> MinuteBar:
        payload = {name: value[name] for name in CANONICAL_BAR_COLUMNS}
        for name in (
            "bar_start",
            "bar_end",
            "limit_known_at",
            "session_open",
            "session_close",
            "segment_start",
            "segment_end",
            "bucket_anchor",
        ):
            payload[name] = _aware_datetime(payload[name], name)
        for name in ("source_calendar_date", "exchange_trade_date"):
            payload[name] = pd.Timestamp(payload[name]).date()
        for name in (
            "open",
            "high",
            "low",
            "close",
            "volume",
            "turnover",
            "open_interest",
            "pre_settlement",
            "limit_up",
            "limit_down",
        ):
            payload[name] = float(payload[name])
        payload["source_row"] = int(payload["source_row"])
        return cls(**payload)

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        for name in (
            "bar_start",
            "bar_end",
            "limit_known_at",
            "session_open",
            "session_close",
            "segment_start",
            "segment_end",
            "bucket_anchor",
        ):
            payload[name] = payload[name].isoformat()
        for name in ("source_calendar_date", "exchange_trade_date"):
            payload[name] = payload[name].isoformat()
        return payload


@dataclass(frozen=True)
class ContractMapping:
    root_symbol: str
    contract_code: str
    effective_session: str
    session_open: datetime
    decision_asof: datetime
    source: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_symbol": self.root_symbol,
            "contract_code": self.contract_code,
            "effective_session": self.effective_session,
            "session_open": self.session_open.isoformat(),
            "decision_asof": self.decision_asof.isoformat(),
            "source": self.source,
        }


def normalize_source_bars(
    source: pd.DataFrame,
    spec: InstrumentSpec,
    calendar: SessionCalendar,
    *,
    daily_specs: Mapping[tuple[str, date], DailyTradingSpec],
    source_path: str = "",
) -> pd.DataFrame:
    """Normalize supported RB/CU source frames without guessing missing metadata."""
    if source.empty:
        return pd.DataFrame(columns=CANONICAL_BAR_COLUMNS)
    schema = _detect_schema(source)
    timestamp_column = "datetime" if schema == "RB" else "trade_time"
    contract_column = "ts_code" if schema == "RB" else (
        "contract_code" if "contract_code" in source else "ts_code"
    )
    activity_columns = (
        ("volume", "turnover", "open_interest")
        if schema == "RB"
        else ("vol", "amount", "oi")
    )
    required = {timestamp_column, contract_column, *CANONICAL_OHLC, *activity_columns}
    missing = sorted(required.difference(source.columns))
    if missing:
        raise DataQualityError(
            f"{schema} source is missing columns: {missing}", code="MISSING_SOURCE_COLUMN"
        )

    raw_times = pd.to_datetime(source[timestamp_column], errors="coerce")
    if raw_times.isna().any():
        raise DataQualityError("source contains invalid timestamps", code="INVALID_TIMESTAMP")
    if raw_times.duplicated().any():
        raise DataQualityError("source contains duplicate timestamps", code="DUPLICATE_BAR")
    if not raw_times.is_monotonic_increasing:
        raise DataQualityError("source bars are out of order", code="OUT_OF_ORDER_BAR")
    aware_end = _localize_series(raw_times)

    contracts = source[contract_column].astype(str).str.strip().map(_canonical_contract)
    if contracts.eq("").any():
        raise DataQualityError("source contains an unknown contract", code="UNKNOWN_CONTRACT")
    if any(_root_from_contract(value) != spec.root_symbol for value in contracts):
        raise DataQualityError("contract root does not match InstrumentSpec", code="UNKNOWN_CONTRACT")

    numeric = source.loc[:, list(CANONICAL_OHLC) + list(activity_columns)].apply(
        pd.to_numeric, errors="coerce"
    )
    if not np.isfinite(numeric.to_numpy(dtype=float)).all():
        raise DataQualityError("source contains non-finite numbers", code="NONFINITE_BAR")
    first_bar_end_times = {
        (
            datetime.combine(date(2000, 1, 1), segment.start)
            + timedelta(minutes=1)
        ).time()
        for session in spec.sessions
        for segment in session.segments[:1]
    }
    zero_ohlc = numeric.loc[:, list(CANONICAL_OHLC)].eq(0).all(axis=1)
    opening_activity = (
        zero_ohlc
        & aware_end.dt.time.isin(first_bar_end_times)
        & numeric[activity_columns[0]].gt(0)
        & numeric[activity_columns[1]].gt(0)
    )
    implied_price = numeric[activity_columns[1]] / (
        numeric[activity_columns[0]] * spec.contract_size
    )
    tick_units = implied_price / spec.price_tick
    aligned = pd.Series(
        np.isclose(tick_units, np.rint(tick_units), rtol=0.0, atol=1e-9),
        index=numeric.index,
    )
    repairable = opening_activity & aligned & implied_price.gt(0)
    if repairable.any():
        repaired = (
            np.rint(tick_units.loc[repairable]) * spec.price_tick
        ).to_numpy()[:, None]
        numeric.loc[repairable, list(CANONICAL_OHLC)] = np.repeat(
            repaired,
            len(CANONICAL_OHLC),
            axis=1,
        )
    _validate_ohlc(numeric)
    zero_volume_sentinel = numeric[activity_columns[0]].eq(0) & numeric[
        activity_columns[1]
    ].lt(0)
    numeric.loc[zero_volume_sentinel, activity_columns[1]] = 0.0
    if (numeric.loc[:, list(activity_columns)] < 0).any().any():
        raise DataQualityError("volume/turnover/open_interest must be nonnegative", code="NEGATIVE_ACTIVITY")

    numeric_values = {
        column: numeric[column].to_numpy()
        for column in numeric.columns
    }
    contract_values = contracts.to_numpy()
    aware_values = aware_end.array
    source_dates = (
        pd.to_datetime(source["trade_date"]).dt.date.to_numpy()
        if schema == "CU" and "trade_date" in source
        else None
    )
    rows: list[dict[str, Any]] = []
    for position, (aware_value, contract) in enumerate(
        zip(aware_values, contract_values, strict=True)
    ):
        bar_end = pd.Timestamp(aware_value).to_pydatetime()
        bar_start = bar_end - timedelta(minutes=1)
        try:
            assignment = calendar.assign_bar(bar_start, bar_end)
        except SessionError as exc:
            raise DataQualityError(str(exc), code=exc.code) from exc
        daily_key = (contract, assignment.exchange_trade_date)
        daily = daily_specs.get(daily_key)
        if daily is None:
            raise BlockedMetadataError(
                f"daily trading spec is missing for {contract} {assignment.exchange_trade_date}"
            )
        if daily.known_at > assignment.session_open:
            raise BlockedMetadataError(
                f"daily trading spec known_at is later than session open for {contract}"
            )
        source_date = (
            source_dates[position]
            if source_dates is not None
            else bar_end.date()
        )
        exchange = spec.exchange
        vt_symbol = f"{contract.rsplit('.', 1)[0]}.{exchange}"
        rows.append(
            {
                "source_calendar_date": source_date,
                "exchange_trade_date": assignment.exchange_trade_date,
                "session_id": assignment.session_id,
                "session_kind": assignment.session_kind,
                "segment_id": assignment.segment_id,
                "bar_start": bar_start,
                "bar_end": bar_end,
                "root_symbol": spec.root_symbol,
                "vt_symbol": vt_symbol,
                "contract_code": contract,
                "exchange": exchange,
                "open": float(numeric_values["open"][position]),
                "high": float(numeric_values["high"][position]),
                "low": float(numeric_values["low"][position]),
                "close": float(numeric_values["close"][position]),
                "volume": float(numeric_values[activity_columns[0]][position]),
                "turnover": float(numeric_values[activity_columns[1]][position]),
                "open_interest": float(
                    numeric_values[activity_columns[2]][position]
                ),
                "pre_settlement": daily.pre_settlement,
                "limit_up": daily.limit_up,
                "limit_down": daily.limit_down,
                "limit_source": daily.source,
                "limit_known_at": daily.known_at,
                "source_path": source_path,
                "source_row": position,
                "calendar_sha256": assignment.calendar_sha256,
                "session_open": assignment.session_open,
                "session_close": assignment.session_close,
                "segment_start": assignment.segment_start,
                "segment_end": assignment.segment_end,
                "bucket_anchor": assignment.bucket_anchor,
            }
        )
    result = pd.DataFrame(rows, columns=CANONICAL_BAR_COLUMNS)
    _validate_normalized(result)
    return result


CANONICAL_OHLC = ("open", "high", "low", "close")


def aggregate_completed_bars(
    minute_bars: pd.DataFrame,
    interval_minutes: int,
    calendar: SessionCalendar,
) -> pd.DataFrame:
    """Aggregate 5/30-minute bars while preserving normalized audit metadata."""
    del calendar
    if interval_minutes not in (5, 30):
        raise ValueError("only 5-minute and 30-minute aggregation is supported")
    if minute_bars.empty:
        return pd.DataFrame()
    required = set(CANONICAL_BAR_COLUMNS)
    missing = sorted(required.difference(minute_bars.columns))
    if missing:
        raise DataQualityError(f"normalized bars missing columns: {missing}", code="INVALID_NORMALIZED_BAR")
    _validate_normalized(minute_bars)

    frame = minute_bars.copy().sort_values("bar_end", kind="stable")
    elapsed = (frame["bar_start"] - frame["bucket_anchor"]).dt.total_seconds() // 60
    frame["_bucket"] = (elapsed // interval_minutes).astype(int)
    keys = ["session_id", "segment_id", "_bucket"]
    invariant = (
        "exchange_trade_date",
        "session_id",
        "session_kind",
        "segment_id",
        "root_symbol",
        "vt_symbol",
        "contract_code",
        "exchange",
        "pre_settlement",
        "limit_up",
        "limit_down",
        "limit_source",
        "limit_known_at",
        "calendar_sha256",
        "session_open",
        "session_close",
        "segment_start",
        "segment_end",
        "bucket_anchor",
    )
    named_aggregations: dict[str, tuple[str, Any]] = {
        f"_invariant_{column}": (column, "first")
        for column in invariant
        if column not in keys
    }
    named_aggregations.update(
        {
            "source_calendar_date": ("source_calendar_date", "last"),
            "first_bar_start": ("bar_start", "first"),
            "last_bar_end": ("bar_end", "last"),
            "open": ("open", "first"),
            "high": ("high", "max"),
            "low": ("low", "min"),
            "close": ("close", "last"),
            "volume": ("volume", "sum"),
            "turnover": ("turnover", "sum"),
            "open_interest": ("open_interest", "last"),
            "source_max_bar_end": ("bar_end", "max"),
            "source_path": (
                "source_path",
                lambda values: "|".join(sorted(set(values.astype(str)))),
            ),
            "source_row_first": ("source_row", "min"),
            "source_row_last": ("source_row", "max"),
            "bar_count": ("bar_end", "size"),
            "span_start": ("bar_end", "min"),
            "span_end": ("bar_end", "max"),
        }
    )
    groups = frame.groupby(keys, sort=False, dropna=False)
    aggregated = groups.agg(**named_aggregations)
    bucket_anchor = aggregated["_invariant_bucket_anchor"]
    bucket_number = aggregated.index.get_level_values("_bucket").to_numpy()
    expected_start = bucket_anchor + pd.to_timedelta(
        bucket_number * interval_minutes,
        unit="min",
    )
    expected_end = expected_start + pd.Timedelta(minutes=interval_minutes)
    # Normalized bars are unique. Count plus endpoint span is therefore
    # equivalent to comparing every expected minute in the old implementation.
    complete = (
        aggregated["bar_count"].eq(interval_minutes)
        & aggregated["span_end"].sub(aggregated["span_start"]).eq(
            pd.Timedelta(minutes=interval_minutes - 1)
        )
        & aggregated["first_bar_start"].eq(expected_start)
        & aggregated["last_bar_end"].eq(expected_end)
    )
    invariant_counts = groups[list(invariant)].nunique(dropna=False)
    changed_rows = invariant_counts.loc[complete].gt(1)
    if changed_rows.any(axis=None):
        bucket_key = changed_rows.index[changed_rows.any(axis=1)][0]
        changed = changed_rows.columns[changed_rows.loc[bucket_key]].tolist()
        raise DataQualityError(
            f"bucket invariant changed inside interval {bucket_key}: {changed}",
            code="BUCKET_METADATA_CHANGED",
        )
    aggregated = aggregated.loc[complete].copy()
    if aggregated.empty:
        return pd.DataFrame()
    expected_start = expected_start.loc[complete]
    expected_end = expected_end.loc[complete]
    indexed = aggregated.reset_index()
    result = pd.DataFrame()
    for column in invariant:
        result[column] = (
            indexed[column]
            if column in keys
            else indexed[f"_invariant_{column}"]
        )
    result["source_calendar_date"] = indexed["source_calendar_date"]
    result["bar_start"] = expected_start.reset_index(drop=True)
    result["bar_end"] = expected_end.reset_index(drop=True)
    for column in ("open", "high", "low", "close", "volume", "turnover", "open_interest"):
        result[column] = indexed[column].astype(float)
    result["source_max_bar_end"] = indexed["source_max_bar_end"]
    result["source_path"] = indexed["source_path"]
    result["source_row_first"] = indexed["source_row_first"].astype(int)
    result["source_row_last"] = indexed["source_row_last"].astype(int)
    result["interval_minutes"] = interval_minutes
    return result.reset_index(drop=True)


def validate_contract_mappings(mappings: tuple[ContractMapping, ...]) -> None:
    seen: set[tuple[str, str]] = set()
    prior_session: datetime | None = None
    for mapping in mappings:
        session_open = ensure_shanghai(mapping.session_open, "session_open")
        decision = ensure_shanghai(mapping.decision_asof, "decision_asof")
        latest_decision = (
            session_open + timedelta(minutes=1)
            if mapping.source == "local_session_first_bar"
            else session_open
        )
        if decision > latest_decision:
            raise DataQualityError(
                f"mapping decision is later than session open: {mapping.effective_session}",
                code="NON_CAUSAL_CONTRACT_MAPPING",
            )
        key = (mapping.root_symbol.upper(), mapping.effective_session)
        if key in seen:
            raise DataQualityError("duplicate effective contract mapping", code="DUPLICATE_CONTRACT_MAPPING")
        if prior_session is not None and session_open < prior_session:
            raise DataQualityError("contract mappings are out of order", code="OUT_OF_ORDER_CONTRACT_MAPPING")
        if not mapping.source:
            raise DataQualityError("mapping source is missing", code="MISSING_MAPPING_SOURCE")
        seen.add(key)
        prior_session = session_open


def apply_contract_roll_freeze(bars: pd.DataFrame) -> pd.DataFrame:
    """Mark the full first session after a contract switch; reject in-session switches."""
    required = {"bar_end", "session_id", "contract_code"}
    missing = sorted(required.difference(bars.columns))
    if missing:
        raise DataQualityError(f"roll bars missing columns: {missing}", code="INVALID_ROLL_INPUT")
    result = bars.copy()
    if not result["bar_end"].is_monotonic_increasing:
        raise DataQualityError("roll bars are out of order", code="OUT_OF_ORDER_BAR")
    session_contracts = result.groupby("session_id", sort=False)["contract_code"].nunique()
    if (session_contracts > 1).any():
        raise DataQualityError(
            "contract changed within a session", code="INTRASESSION_CONTRACT_SWITCH"
        )
    result["roll_freeze"] = False
    result["roll_reason"] = None
    prior_contract: str | None = None
    for _, indexes in result.groupby("session_id", sort=False).groups.items():
        index_list = list(indexes)
        contract = str(result.loc[index_list[0], "contract_code"])
        if prior_contract is not None and contract != prior_contract:
            result.loc[index_list, "roll_freeze"] = True
            result.loc[index_list, "roll_reason"] = "contract_switch"
        prior_contract = contract
    return result


def _detect_schema(source: pd.DataFrame) -> str:
    if "datetime" in source:
        return "RB"
    if "trade_time" in source:
        return "CU"
    raise DataQualityError("unsupported minute source schema", code="UNKNOWN_SOURCE_SCHEMA")


def _localize_series(values: pd.Series) -> pd.Series:
    if values.dt.tz is None:
        return values.dt.tz_localize("Asia/Shanghai", ambiguous="raise", nonexistent="raise")
    return values.dt.tz_convert("Asia/Shanghai")


def _canonical_contract(value: str) -> str:
    return value.strip().upper()


def _root_from_contract(contract: str) -> str:
    match = re.match(r"^([A-Z]+)\d+\.", contract)
    return match.group(1) if match else ""


def _validate_ohlc(frame: pd.DataFrame) -> None:
    open_ = frame["open"]
    high = frame["high"]
    low = frame["low"]
    close = frame["close"]
    valid = (
        (low > 0)
        & (open_ > 0)
        & (close > 0)
        & (high > 0)
        & (low <= np.minimum(open_, close))
        & (np.maximum(open_, close) <= high)
    )
    if not bool(valid.all()):
        raise DataQualityError("source contains invalid OHLC", code="INVALID_OHLC")


def _validate_normalized(frame: pd.DataFrame) -> None:
    keys = frame[["contract_code", "bar_end"]]
    if keys.duplicated().any():
        raise DataQualityError("duplicate contract/bar_end", code="DUPLICATE_BAR")
    if not frame["bar_end"].is_monotonic_increasing:
        raise DataQualityError("bars are out of order", code="OUT_OF_ORDER_BAR")
    _validate_ohlc(frame.loc[:, CANONICAL_OHLC])
    if (frame[["volume", "turnover", "open_interest"]] < 0).any().any():
        raise DataQualityError("negative activity", code="NEGATIVE_ACTIVITY")
    for segment_key, segment in frame.groupby(
        ["session_id", "segment_id"], sort=False
    ):
        differences = segment["bar_end"].diff().dropna().dt.total_seconds() / 60.0
        excessive = differences.loc[differences - 1 > 2]
        if not excessive.empty:
            gap_end = segment.loc[excessive.index[0], "bar_end"]
            raise DataQualityError(
                "more than two consecutive one-minute bars are missing in "
                f"{segment_key} before {gap_end}: {excessive.iloc[0] - 1:.0f}",
                code="SESSION_GAP_EXCEEDED",
            )
    for name in ("bar_start", "bar_end", "limit_known_at"):
        for value in frame[name]:
            _aware_datetime(value, name)


def _aware_datetime(value: object, field_name: str) -> datetime:
    timestamp = pd.Timestamp(value)
    if timestamp.tzinfo is None:
        raise DataQualityError(f"{field_name} must be timezone aware", code="NAIVE_DATETIME")
    return ensure_shanghai(timestamp.to_pydatetime(), field_name)


__all__ = [
    "CANONICAL_BAR_COLUMNS",
    "ContractMapping",
    "DataQualityError",
    "MinuteBar",
    "aggregate_completed_bars",
    "apply_contract_roll_freeze",
    "normalize_source_bars",
    "validate_contract_mappings",
]
