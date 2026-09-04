"""Point-in-time contract mapping, immutable adjustments, and two-leg rolls."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime
from enum import Enum
import math

import pandas as pd


@dataclass(frozen=True)
class ContractMapRecord:
    root_symbol: str
    exchange_trade_date: date
    contract_code: str
    mapping_known_at: datetime
    roll_reason: str

    def __post_init__(self) -> None:
        if self.mapping_known_at.tzinfo is None or self.mapping_known_at.utcoffset() is None:
            raise ValueError("mapping_known_at must be timezone-aware")
        if not self.root_symbol or not self.contract_code or not self.roll_reason:
            raise ValueError("mapping identity and roll reason are required")


@dataclass(frozen=True)
class AdjustmentEvent:
    root_symbol: str
    effective_from: datetime
    scale: float
    offset: float
    adjustment_known_at: datetime
    version: str

    def __post_init__(self) -> None:
        if not self.root_symbol or not self.version:
            raise ValueError("adjustment root and version are required")
        for name in ("effective_from", "adjustment_known_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"{name} must be timezone-aware")
        if not math.isfinite(self.scale) or self.scale == 0:
            raise ValueError("adjustment scale must be finite and nonzero")
        if not math.isfinite(self.offset):
            raise ValueError("adjustment offset must be finite")
        if self.adjustment_known_at > self.effective_from:
            raise ValueError("roll adjustment must be known by its effective event")

    def adjusted(self, raw_price: float) -> float:
        return raw_price * self.scale + self.offset

    def raw(self, adjusted_price: float) -> float:
        if not math.isfinite(self.scale) or self.scale == 0:
            raise ValueError("adjustment scale must be finite and nonzero")
        return (adjusted_price - self.offset) / self.scale


class PointInTimeContractMapper:
    def __init__(self, records: list[ContractMapRecord]) -> None:
        self.records = list(records)

    def resolve(
        self,
        root_symbol: str,
        exchange_trade_date: date,
        decision_asof: datetime,
    ) -> ContractMapRecord:
        matches = [
            item for item in self.records
            if item.root_symbol == root_symbol
            and item.exchange_trade_date == exchange_trade_date
            and item.mapping_known_at <= decision_asof
        ]
        if len(matches) != 1:
            raise LookupError(
                f"BLOCKED_CONTRACT_MAPPING: expected one known map, got {len(matches)}"
            )
        return matches[0]


def build_roll_adjustment(
    *,
    previous: AdjustmentEvent,
    old_raw_price: float,
    new_raw_price: float,
    effective_from: datetime,
    adjustment_known_at: datetime,
    version: str,
) -> AdjustmentEvent:
    """Create an append-only additive roll event from a known overlap quote."""
    if not all(math.isfinite(value) for value in (old_raw_price, new_raw_price)):
        raise ValueError("roll overlap prices must be finite")
    if adjustment_known_at > effective_from:
        raise ValueError("roll adjustment must be known by its effective event")
    adjusted_overlap = previous.adjusted(old_raw_price)
    offset = adjusted_overlap - new_raw_price * previous.scale
    return AdjustmentEvent(
        root_symbol=previous.root_symbol,
        effective_from=effective_from,
        scale=previous.scale,
        offset=offset,
        adjustment_known_at=adjustment_known_at,
        version=version,
    )


@dataclass(frozen=True)
class RollAdjustmentReference:
    root_symbol: str
    old_contract: str
    new_contract: str
    effective_from: datetime
    old_reference_price: float
    new_reference_price: float
    known_at: datetime
    source: str
    version: str

    def __post_init__(self) -> None:
        for name in ("effective_from", "known_at"):
            value = getattr(self, name)
            if value.tzinfo is None or value.utcoffset() is None:
                raise ValueError(f"roll reference {name} must be timezone-aware")
        if (
            not self.root_symbol
            or not self.old_contract
            or not self.new_contract
            or self.old_contract == self.new_contract
            or not self.source
            or not self.version
        ):
            raise ValueError("roll reference identity is invalid")
        if not all(
            math.isfinite(value) and value > 0
            for value in (self.old_reference_price, self.new_reference_price)
        ):
            raise ValueError("roll reference prices must be positive and finite")
        if self.known_at > self.effective_from:
            raise ValueError("roll reference must be known by its effective event")


def build_point_in_time_signal_bars(
    frame: pd.DataFrame,
    *,
    root_symbol: str,
    references: tuple[RollAdjustmentReference, ...],
) -> pd.DataFrame:
    """Attach append-only adjusted signal OHLC while preserving raw contract bars."""
    required = {"bar_end", "open", "high", "low", "close", "contract_code"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"signal adjustment input is missing: {','.join(missing)}")
    if not root_symbol:
        raise ValueError("signal adjustment root_symbol is required")
    if frame.empty:
        result = frame.copy()
        for column in (
            "signal_open",
            "signal_high",
            "signal_low",
            "signal_close",
            "adjustment_scale",
            "adjustment_offset",
            "adjustment_known_at",
            "adjustment_version",
            "adjustment_source",
        ):
            result[column] = pd.Series(dtype=object)
        return result

    result = frame.copy().sort_values("bar_end", kind="stable").reset_index(drop=True)
    timestamps = pd.to_datetime(result["bar_end"])
    if timestamps.dt.tz is None:
        raise ValueError("signal adjustment bar_end must be timezone-aware")
    if timestamps.duplicated().any():
        raise ValueError("signal adjustment bar_end must be unique")
    prices = result[["open", "high", "low", "close"]].apply(
        pd.to_numeric, errors="coerce"
    )
    if not prices.map(lambda value: math.isfinite(float(value)) and value > 0).all().all():
        raise ValueError("signal adjustment OHLC must be positive and finite")

    by_transition = {
        (
            reference.old_contract,
            reference.new_contract,
            pd.Timestamp(reference.effective_from),
        ): reference
        for reference in references
    }
    if len(by_transition) != len(references):
        raise ValueError("roll adjustment references must be unique")
    first_event = timestamps.iloc[0].to_pydatetime()
    adjustment = AdjustmentEvent(
        root_symbol=root_symbol,
        effective_from=first_event,
        scale=1.0,
        offset=0.0,
        adjustment_known_at=first_event,
        version=f"{root_symbol}:UNADJUSTED",
    )
    source = "INITIAL_RAW_CONTRACT"
    prior_contract = str(result.iloc[0]["contract_code"])
    observed: set[tuple[str, str, pd.Timestamp]] = set()
    rows: list[dict[str, object]] = []
    for row in result.itertuples(index=False):
        event = pd.Timestamp(row.bar_end)
        contract = str(row.contract_code)
        if contract != prior_contract:
            key = (prior_contract, contract, event)
            reference = by_transition.get(key)
            if reference is None:
                raise LookupError(
                    "BLOCKED_ROLL_ADJUSTMENT: missing point-in-time reference for "
                    f"{prior_contract}->{contract} at {event.isoformat()}"
                )
            if reference.root_symbol != root_symbol:
                raise ValueError("roll adjustment reference root mismatch")
            adjustment = build_roll_adjustment(
                previous=adjustment,
                old_raw_price=reference.old_reference_price,
                new_raw_price=reference.new_reference_price,
                effective_from=reference.effective_from,
                adjustment_known_at=reference.known_at,
                version=reference.version,
            )
            source = reference.source
            observed.add(key)
            prior_contract = contract
        if adjustment.adjustment_known_at > event.to_pydatetime():
            raise LookupError("BLOCKED_ROLL_ADJUSTMENT: adjustment was known late")
        rows.append(
            {
                "signal_open": adjustment.adjusted(float(row.open)),
                "signal_high": adjustment.adjusted(float(row.high)),
                "signal_low": adjustment.adjusted(float(row.low)),
                "signal_close": adjustment.adjusted(float(row.close)),
                "adjustment_scale": adjustment.scale,
                "adjustment_offset": adjustment.offset,
                "adjustment_known_at": adjustment.adjustment_known_at,
                "adjustment_version": adjustment.version,
                "adjustment_source": source,
            }
        )
    unused = set(by_transition).difference(observed)
    if unused:
        raise ValueError(f"roll adjustment references were not observed: {sorted(unused)}")
    return pd.concat([result, pd.DataFrame(rows)], axis=1)


class PointInTimeContinuousSeries:
    """Append-only adjusted signal prices; historical rows are never rewritten."""

    def __init__(self, root_symbol: str) -> None:
        if not root_symbol:
            raise ValueError("root_symbol is required")
        self.root_symbol = root_symbol
        self._rows: list[dict[str, object]] = []

    def append(
        self,
        *,
        bar_end: datetime,
        raw_price: float,
        mapping: ContractMapRecord,
        adjustment: AdjustmentEvent,
    ) -> None:
        if bar_end.tzinfo is None or bar_end.utcoffset() is None:
            raise ValueError("bar_end must be timezone-aware")
        if mapping.root_symbol != self.root_symbol or adjustment.root_symbol != self.root_symbol:
            raise ValueError("continuous-series root mismatch")
        if mapping.mapping_known_at > bar_end or adjustment.adjustment_known_at > bar_end:
            raise ValueError("mapping and adjustment must be known by bar_end")
        if adjustment.effective_from > bar_end:
            raise ValueError("adjustment is not yet effective")
        if self._rows and bar_end <= self._rows[-1]["bar_end"]:
            raise ValueError("continuous-series bars must append in order")
        if not math.isfinite(raw_price):
            raise ValueError("raw_price must be finite")
        self._rows.append(
            {
                "bar_end": bar_end,
                "raw_contract_price": float(raw_price),
                "adjusted_signal_price": adjustment.adjusted(float(raw_price)),
                "adjustment_scale": adjustment.scale,
                "adjustment_offset": adjustment.offset,
                "adjustment_known_at": adjustment.adjustment_known_at,
                "active_contract": mapping.contract_code,
                "mapping_known_at": mapping.mapping_known_at,
                "roll_reason": mapping.roll_reason,
                "adjustment_version": adjustment.version,
            }
        )

    def to_frame(self) -> pd.DataFrame:
        return pd.DataFrame(self._rows).copy(deep=True).reset_index(drop=True)


class RollState(str, Enum):
    HOLD_OLD = "HOLD_OLD"
    CLOSE_OLD_PENDING = "CLOSE_OLD_PENDING"
    CLOSE_OLD_FILLED = "CLOSE_OLD_FILLED"
    OPEN_NEW_PENDING = "OPEN_NEW_PENDING"
    ROLLED = "ROLLED"
    ROLL_BLOCKED = "ROLL_BLOCKED"


class RollLeg(str, Enum):
    CLOSE_OLD = "CLOSE_OLD"
    OPEN_NEW = "OPEN_NEW"


@dataclass(frozen=True)
class RollFill:
    leg: RollLeg
    contract_code: str
    quantity: int
    event: datetime
    price: float
    fee: float

    def __post_init__(self) -> None:
        if self.event.tzinfo is None or self.event.utcoffset() is None:
            raise ValueError("roll fill event must be timezone-aware")
        if (
            not self.contract_code
            or not isinstance(self.quantity, int)
            or isinstance(self.quantity, bool)
            or self.quantity <= 0
            or not math.isfinite(self.price)
            or self.price <= 0
            or not math.isfinite(self.fee)
            or self.fee < 0
        ):
            raise ValueError("invalid roll fill")


class RollTracker:
    def __init__(self, *, active_contract: str, active_quantity: int) -> None:
        if not active_contract or active_quantity <= 0:
            raise ValueError("active contract and positive quantity are required")
        self.active_contract = active_contract
        self.active_quantity = active_quantity
        self.target_contract: str | None = None
        self.state = RollState.HOLD_OLD
        self.block_reason = ""
        self.closed_quantity = 0
        self.opened_quantity = 0
        self.fills: list[RollFill] = []
        self._roll_quantity = 0
        self._last_fill_event: datetime | None = None

    @property
    def total_fee(self) -> float:
        return float(sum(fill.fee for fill in self.fills))

    def request_roll(self, target_contract: str) -> None:
        if self.state not in {RollState.HOLD_OLD, RollState.ROLLED}:
            raise ValueError("roll is already in progress")
        if not target_contract or target_contract == self.active_contract:
            raise ValueError("target contract must differ from active contract")
        self.target_contract = target_contract
        self._roll_quantity = self.active_quantity
        self.closed_quantity = 0
        self.opened_quantity = 0
        self.block_reason = ""
        self.state = RollState.CLOSE_OLD_PENDING

    def close_old_filled(
        self,
        *,
        quantity: int,
        event: datetime,
        price: float,
        fee: float,
    ) -> None:
        if self.state is not RollState.CLOSE_OLD_PENDING:
            raise ValueError("old contract close is not pending")
        if self.closed_quantity + quantity > self._roll_quantity:
            raise ValueError("old contract close exceeds planned roll quantity")
        self._record_fill(
            RollFill(RollLeg.CLOSE_OLD, self.active_contract, quantity, event, price, fee)
        )
        self.closed_quantity += quantity
        self.active_quantity -= quantity
        if self.closed_quantity == self._roll_quantity:
            self.state = RollState.CLOSE_OLD_FILLED
            self.state = RollState.OPEN_NEW_PENDING

    def open_new_filled(
        self,
        *,
        quantity: int,
        event: datetime,
        price: float,
        fee: float,
    ) -> None:
        if self.state is not RollState.OPEN_NEW_PENDING or self.target_contract is None:
            raise ValueError("old contract must be closed before opening the new contract")
        fill = RollFill(RollLeg.OPEN_NEW, self.target_contract, quantity, event, price, fee)
        if self.opened_quantity + quantity > self._roll_quantity:
            raise ValueError("new contract open exceeds planned roll quantity")
        self._record_fill(fill)
        self.opened_quantity += quantity
        if self.opened_quantity == self._roll_quantity:
            self.active_contract = self.target_contract
            self.active_quantity = self.opened_quantity
            self.target_contract = None
            self.state = RollState.ROLLED

    def block(self, reason: str) -> None:
        if not reason:
            raise ValueError("roll block reason is required")
        self.block_reason = reason
        self.state = RollState.ROLL_BLOCKED

    def _record_fill(self, fill: RollFill) -> None:
        if self._last_fill_event is not None and fill.event < self._last_fill_event:
            raise ValueError("roll fills must be chronological")
        self.fills.append(fill)
        self._last_fill_event = fill.event


__all__ = [
    "AdjustmentEvent", "ContractMapRecord", "PointInTimeContinuousSeries",
    "PointInTimeContractMapper", "RollAdjustmentReference", "RollFill", "RollLeg",
    "RollState", "RollTracker", "build_point_in_time_signal_bars",
    "build_roll_adjustment",
]
