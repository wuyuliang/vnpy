"""Versioned instrument, daily limit, fee, and margin metadata contracts."""

from __future__ import annotations

import math
import re
from dataclasses import dataclass
from datetime import date, datetime
from pathlib import Path
from typing import Any

import pandas as pd

from .session import SHANGHAI_TZ, SessionSpec, ensure_shanghai


DEFAULT_META_ROOT = Path(__file__).with_name("meta")


class BlockedMetadataError(RuntimeError):
    """A recognizable fail-closed outcome for unavailable research metadata."""

    status = "BLOCKED_METADATA"

    def __init__(self, message: str) -> None:
        super().__init__(f"{self.status}: {message}")


def _positive(value: float, field_name: str) -> None:
    if not math.isfinite(value) or value <= 0:
        raise ValueError(f"{field_name} must be finite and positive")


def _nonnegative(value: float, field_name: str) -> None:
    if not math.isfinite(value) or value < 0:
        raise ValueError(f"{field_name} must be finite and nonnegative")


def _iso(value: date | datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


@dataclass(frozen=True)
class InstrumentSpec:
    root_symbol: str
    exchange: str
    contract_size: float
    price_tick: float
    lot_step: int
    slippage_ticks_base: float
    sessions: tuple[SessionSpec, ...]
    effective_from: date
    effective_to: date | None

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_symbol", self.root_symbol.upper())
        object.__setattr__(self, "exchange", self.exchange.upper())
        _positive(self.contract_size, "contract_size")
        _positive(self.price_tick, "price_tick")
        _nonnegative(self.slippage_ticks_base, "slippage_ticks_base")
        if self.lot_step <= 0:
            raise ValueError("lot_step must be positive")
        if not self.sessions:
            raise ValueError("sessions must not be empty")
        if self.effective_to is not None and self.effective_to < self.effective_from:
            raise ValueError("effective_to precedes effective_from")

    def to_dict(self) -> dict[str, Any]:
        return {
            "root_symbol": self.root_symbol,
            "exchange": self.exchange,
            "contract_size": self.contract_size,
            "price_tick": self.price_tick,
            "lot_step": self.lot_step,
            "slippage_ticks_base": self.slippage_ticks_base,
            "sessions": [session.to_dict() for session in self.sessions],
            "effective_from": self.effective_from.isoformat(),
            "effective_to": _iso(self.effective_to),
        }


@dataclass(frozen=True)
class FeeMarginSpec:
    schedule_id: str
    root_symbol: str
    contract_code: str | None
    margin_rate_long: float
    margin_rate_short: float
    open_fee_rate: float
    close_fee_rate: float
    close_today_fee_rate: float
    fee_per_lot_open: float
    fee_per_lot_close: float
    fee_per_lot_close_today: float
    effective_from: datetime
    effective_to: datetime | None
    source: str
    known_at: datetime

    def __post_init__(self) -> None:
        object.__setattr__(self, "root_symbol", self.root_symbol.upper())
        object.__setattr__(self, "effective_from", ensure_shanghai(self.effective_from, "effective_from"))
        if self.effective_to is not None:
            object.__setattr__(self, "effective_to", ensure_shanghai(self.effective_to, "effective_to"))
        object.__setattr__(self, "known_at", ensure_shanghai(self.known_at, "known_at"))
        if not self.schedule_id or not self.source:
            raise ValueError("schedule_id and source are required")
        _positive(self.margin_rate_long, "margin_rate_long")
        _positive(self.margin_rate_short, "margin_rate_short")
        for field_name in (
            "open_fee_rate",
            "close_fee_rate",
            "close_today_fee_rate",
            "fee_per_lot_open",
            "fee_per_lot_close",
            "fee_per_lot_close_today",
        ):
            _nonnegative(float(getattr(self, field_name)), field_name)
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("effective_to must follow effective_from")

    def to_dict(self) -> dict[str, Any]:
        return {
            "schedule_id": self.schedule_id,
            "root_symbol": self.root_symbol,
            "contract_code": self.contract_code,
            "margin_rate_long": self.margin_rate_long,
            "margin_rate_short": self.margin_rate_short,
            "open_fee_rate": self.open_fee_rate,
            "close_fee_rate": self.close_fee_rate,
            "close_today_fee_rate": self.close_today_fee_rate,
            "fee_per_lot_open": self.fee_per_lot_open,
            "fee_per_lot_close": self.fee_per_lot_close,
            "fee_per_lot_close_today": self.fee_per_lot_close_today,
            "effective_from": self.effective_from.isoformat(),
            "effective_to": _iso(self.effective_to),
            "source": self.source,
            "known_at": self.known_at.isoformat(),
        }


@dataclass(frozen=True)
class DailyTradingSpec:
    contract_code: str
    exchange_trade_date: date
    pre_settlement: float
    limit_up: float
    limit_down: float
    fee_margin_schedule_id: str
    source: str
    known_at: datetime
    contract_size: float | None = None
    price_tick: float | None = None

    def __post_init__(self) -> None:
        object.__setattr__(self, "known_at", ensure_shanghai(self.known_at, "known_at"))
        if not self.contract_code or not self.fee_margin_schedule_id or not self.source:
            raise ValueError("daily trading metadata identifiers and source are required")
        _positive(self.pre_settlement, "pre_settlement")
        _positive(self.limit_up, "limit_up")
        _positive(self.limit_down, "limit_down")
        if not self.limit_down < self.pre_settlement < self.limit_up:
            raise ValueError("daily limits must bracket pre_settlement")
        if (self.contract_size is None) != (self.price_tick is None):
            raise ValueError("daily contract_size and price_tick must appear together")
        if self.contract_size is not None:
            _positive(self.contract_size, "daily.contract_size")
            _positive(self.price_tick, "daily.price_tick")

    def to_dict(self) -> dict[str, Any]:
        return {
            "contract_code": self.contract_code,
            "exchange_trade_date": self.exchange_trade_date.isoformat(),
            "pre_settlement": self.pre_settlement,
            "limit_up": self.limit_up,
            "limit_down": self.limit_down,
            "fee_margin_schedule_id": self.fee_margin_schedule_id,
            "source": self.source,
            "known_at": self.known_at.isoformat(),
            "contract_size": self.contract_size,
            "price_tick": self.price_tick,
        }


@dataclass(frozen=True)
class SettlementReference:
    contract_code: str
    exchange_trade_date: date
    price: float
    known_at: datetime
    source: str

    def __post_init__(self) -> None:
        object.__setattr__(self, "known_at", ensure_shanghai(self.known_at, "known_at"))
        if not self.contract_code or not self.source:
            raise ValueError("settlement reference identifiers and source are required")
        _positive(self.price, "settlement reference price")


def _parse_datetime(value: object, field_name: str) -> datetime:
    if pd.isna(value) or str(value).strip() == "":
        raise BlockedMetadataError(f"{field_name} is missing")
    parsed = pd.Timestamp(value)
    if parsed.tzinfo is None:
        raise BlockedMetadataError(f"{field_name} must be timezone-aware")
    return parsed.tz_convert(SHANGHAI_TZ).to_pydatetime()


def _optional_datetime(value: object, field_name: str) -> datetime | None:
    if pd.isna(value) or str(value).strip() == "":
        return None
    return _parse_datetime(value, field_name)


def _official_daily_reference_time(
    row: pd.Series,
    *,
    trade_date: date,
    price_column: str,
    known_at_column: str,
) -> object:
    source = str(row.get("source", "")).strip()
    source_reference = str(row.get("source_url_or_file", "")).strip()
    if (
        source != "SHFE_KX_JS_RULE_DERIVED"
        or "shfe.com.cn/data/tradedata/future/dailydata/" not in source_reference
    ):
        raise BlockedMetadataError(f"contract_daily.{known_at_column} is missing")
    if price_column == "pre_settlement":
        matches = re.findall(r"/js(\d{8})\.dat(?:\||$)", source_reference)
        if len(matches) != 1:
            raise BlockedMetadataError(
                "SHFE pre-settlement reference has no unique official JS date"
            )
        source_date = datetime.strptime(matches[0], "%Y%m%d").date()
        return datetime.combine(
            source_date,
            datetime.min.time().replace(hour=15, minute=30),
            tzinfo=SHANGHAI_TZ,
        )
    if price_column == "settlement":
        return datetime.combine(
            trade_date,
            datetime.min.time().replace(hour=15, minute=30),
            tzinfo=SHANGHAI_TZ,
        )
    raise BlockedMetadataError(f"unsupported daily reference: {price_column}")


class MetadataBundle:
    """Verified canonical CSV metadata with fail-closed coverage queries."""

    def __init__(self, root: Path, frames: dict[str, pd.DataFrame], manifest: dict[str, Any]) -> None:
        self.root = root
        self.frames = frames
        self.manifest = manifest

    @classmethod
    def load(cls, root: str | Path = DEFAULT_META_ROOT) -> MetadataBundle:
        from .metadata_importer import CANONICAL_FILENAMES, verify_manifest

        path = Path(root)
        manifest = verify_manifest(path)
        frames = {filename: pd.read_csv(path / filename) for filename in CANONICAL_FILENAMES}
        if any(frame.empty for frame in frames.values()):
            raise BlockedMetadataError("canonical metadata contains an empty required CSV")
        return cls(path, frames, manifest)

    def daily_spec(self, contract_code: str, trade_date: date) -> DailyTradingSpec:
        frame = self.frames["contract_daily.csv"]
        dates = pd.to_datetime(frame["exchange_trade_date"], errors="coerce").dt.date
        matched = frame.loc[
            (frame["contract_code"].astype(str) == contract_code) & (dates == trade_date)
        ]
        if len(matched) != 1:
            raise BlockedMetadataError(
                f"expected one contract_daily row for {contract_code} {trade_date}, got {len(matched)}"
            )
        row = matched.iloc[0]
        schedule_id = str(row.get("fee_margin_schedule_id", "")).strip()
        if not schedule_id or schedule_id.lower() == "nan":
            raise BlockedMetadataError(
                f"fee_margin_schedule_id is missing for {contract_code} {trade_date}"
            )
        try:
            return DailyTradingSpec(
                contract_code=contract_code,
                exchange_trade_date=trade_date,
                pre_settlement=float(row["pre_settlement"]),
                limit_up=float(row["limit_up"]),
                limit_down=float(row["limit_down"]),
                fee_margin_schedule_id=schedule_id,
                source=str(row["source"]),
                known_at=_parse_datetime(row["known_at"], "contract_daily.known_at"),
                contract_size=_optional_positive(
                    row.get("contract_size"),
                    "contract_daily.contract_size",
                ),
                price_tick=_optional_positive(
                    row.get("price_tick"),
                    "contract_daily.price_tick",
                ),
            )
        except (TypeError, ValueError) as exc:
            raise BlockedMetadataError(
                f"invalid contract_daily row for {contract_code} {trade_date}: {exc}"
            ) from exc

    def pre_settlement_reference(
        self,
        contract_code: str,
        trade_date: date,
    ) -> SettlementReference:
        return self._settlement_reference(
            contract_code,
            trade_date,
            price_column="pre_settlement",
            known_at_column="pre_settlement_known_at",
        )

    def settlement_reference(
        self,
        contract_code: str,
        trade_date: date,
    ) -> SettlementReference:
        return self._settlement_reference(
            contract_code,
            trade_date,
            price_column="settlement",
            known_at_column="settlement_known_at",
        )

    def _settlement_reference(
        self,
        contract_code: str,
        trade_date: date,
        *,
        price_column: str,
        known_at_column: str,
    ) -> SettlementReference:
        frame = self.frames["contract_daily.csv"]
        dates = pd.to_datetime(frame["exchange_trade_date"], errors="coerce").dt.date
        matched = frame.loc[
            (frame["contract_code"].astype(str) == contract_code)
            & (dates == trade_date)
        ]
        if len(matched) != 1:
            raise BlockedMetadataError(
                f"expected one contract_daily row for {contract_code} "
                f"{trade_date}, got {len(matched)}"
            )
        row = matched.iloc[0]
        try:
            known_at = row.get(known_at_column)
            if pd.isna(known_at) or str(known_at).strip() == "":
                known_at = _official_daily_reference_time(
                    row,
                    trade_date=trade_date,
                    price_column=price_column,
                    known_at_column=known_at_column,
                )
            return SettlementReference(
                contract_code=contract_code,
                exchange_trade_date=trade_date,
                price=float(row[price_column]),
                known_at=_parse_datetime(
                    known_at, f"contract_daily.{known_at_column}"
                ),
                source=str(row["source"]),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise BlockedMetadataError(
                f"invalid {price_column} reference for {contract_code} "
                f"{trade_date}: {exc}"
            ) from exc
    def fee_margin_spec(
        self,
        schedule_id: str,
        contract_code: str,
        as_of: datetime,
    ) -> FeeMarginSpec:
        instant = ensure_shanghai(as_of, "as_of")
        frame = self.frames["fee_margin_schedule.csv"].copy()
        frame = frame.loc[frame["schedule_id"].astype(str) == schedule_id]
        if frame.empty:
            raise BlockedMetadataError(f"fee schedule {schedule_id} is missing")

        frame["_from"] = frame["effective_from"].map(
            lambda value: _parse_datetime(value, "fee_margin.effective_from")
        )
        frame["_to"] = frame["effective_to"].map(
            lambda value: _optional_datetime(value, "fee_margin.effective_to")
        )
        contract_values = frame["contract_code"].fillna("").astype(str)
        frame = frame.loc[
            contract_values.isin(("", contract_code))
            & frame["_from"].map(lambda value: value <= instant)
            & frame["_to"].map(lambda value: value is None or instant < value)
        ].copy()
        if frame.empty:
            raise BlockedMetadataError(
                f"fee schedule {schedule_id} does not cover {contract_code} at {instant.isoformat()}"
            )
        frame["_specific"] = frame["contract_code"].fillna("").astype(str).eq(contract_code)
        frame = frame.loc[frame["_specific"].eq(frame["_specific"].max())]
        if len(frame) != 1:
            raise BlockedMetadataError(
                f"overlapping fee schedule {schedule_id} for {contract_code} "
                f"at {instant.isoformat()}"
            )
        row = frame.iloc[0]
        try:
            spec = FeeMarginSpec(
                schedule_id=schedule_id,
                root_symbol=str(row["root_symbol"]),
                contract_code=(
                    contract_code if str(row.get("contract_code", "")).strip() == contract_code else None
                ),
                margin_rate_long=float(row["margin_rate_long"]),
                margin_rate_short=float(row["margin_rate_short"]),
                open_fee_rate=float(row["open_fee_rate"]),
                close_fee_rate=float(row["close_fee_rate"]),
                close_today_fee_rate=float(row["close_today_fee_rate"]),
                fee_per_lot_open=float(row["fee_per_lot_open"]),
                fee_per_lot_close=float(row["fee_per_lot_close"]),
                fee_per_lot_close_today=float(row["fee_per_lot_close_today"]),
                effective_from=row["_from"],
                effective_to=row["_to"],
                source=str(row["source"]),
                known_at=_parse_datetime(row["known_at"], "fee_margin.known_at"),
            )
        except (TypeError, ValueError) as exc:
            raise BlockedMetadataError(f"invalid fee schedule {schedule_id}: {exc}") from exc
        if spec.known_at > spec.effective_from:
            raise BlockedMetadataError(
                f"fee schedule {schedule_id} known_at is later than effective_from"
            )
        return spec

    def require_coverage(
        self,
        *,
        contract_codes: tuple[str, ...],
        trade_dates: tuple[date, ...],
        session_opens: dict[tuple[str, date], datetime],
    ) -> None:
        contracts = self.frames["contract_specs.csv"]
        calendar = self.frames["exchange_calendar.csv"]
        contract_codes_in_file = set(contracts["contract_code"].astype(str))

        for contract_code in contract_codes:
            if contract_code not in contract_codes_in_file:
                raise BlockedMetadataError(f"contract spec is missing for {contract_code}")
            contract_rows = contracts.loc[contracts["contract_code"].astype(str) == contract_code]
            if len(contract_rows) != 1:
                raise BlockedMetadataError(f"contract spec is ambiguous for {contract_code}")
            contract_row = contract_rows.iloc[0]
            exchange = str(contract_row["exchange"]).upper()

            for trade_date in trade_dates:
                key = (contract_code, trade_date)
                if key not in session_opens:
                    raise BlockedMetadataError(f"session_open is missing for {contract_code} {trade_date}")
                session_open = ensure_shanghai(session_opens[key], "session_open")
                calendar_dates = pd.to_datetime(
                    calendar["exchange_trade_date"], errors="coerce"
                ).dt.date
                calendar_rows = calendar.loc[
                    (calendar["exchange"].astype(str).str.upper() == exchange)
                    & (calendar_dates == trade_date)
                    & calendar["is_open"].map(_as_bool)
                ]
                if len(calendar_rows) != 1:
                    raise BlockedMetadataError(
                        f"open exchange calendar row is missing for {exchange} {trade_date}"
                    )
                calendar_known = _parse_datetime(
                    calendar_rows.iloc[0]["known_at"], "exchange_calendar.known_at"
                )
                contract_known = _parse_datetime(
                    contract_row["known_at"], "contract_specs.known_at"
                )
                if calendar_known > session_open or contract_known > session_open:
                    raise BlockedMetadataError(
                        f"calendar/contract known_at is later than session open for {contract_code} {trade_date}"
                    )

                daily = self.daily_spec(contract_code, trade_date)
                if daily.known_at > session_open:
                    raise BlockedMetadataError(
                        f"contract_daily known_at is later than session open for {contract_code} {trade_date}"
                    )
                fee = self.fee_margin_spec(
                    daily.fee_margin_schedule_id, contract_code, session_open
                )
                if fee.known_at > session_open:
                    raise BlockedMetadataError(
                        f"fee_margin known_at is later than session open for {contract_code} {trade_date}"
                    )


def _as_bool(value: object) -> bool:
    if isinstance(value, bool):
        return value
    return str(value).strip().lower() in {"1", "true", "yes"}


def _optional_positive(value: object, field_name: str) -> float | None:
    if value is None or pd.isna(value) or str(value).strip() == "":
        return None
    parsed = float(value)
    _positive(parsed, field_name)
    return parsed


__all__ = [
    "DEFAULT_META_ROOT",
    "BlockedMetadataError",
    "DailyTradingSpec",
    "FeeMarginSpec",
    "InstrumentSpec",
    "MetadataBundle",
]
