"""Effective-dated execution metadata with strict point-in-time visibility."""
from __future__ import annotations

from collections.abc import Callable, Iterable, Mapping
from dataclasses import asdict, dataclass
from datetime import date, datetime
import hashlib
import json
import math
from types import MappingProxyType

import pandas as pd

from .sessions import SessionSpec


class BlockedMetadataError(RuntimeError):
    def __init__(self, message: str, reason_code: str = "BLOCKED_METADATA") -> None:
        super().__init__(message)
        self.reason_code = reason_code


@dataclass(frozen=True)
class InstrumentSpec:
    root_symbol: str
    exchange: str
    contract_size: float
    price_tick: float
    lot_step: int
    sessions: tuple[SessionSpec, ...]
    effective_from: date
    effective_to: date | None
    source: str
    known_at: datetime

    def __post_init__(self) -> None:
        _aware(self.known_at, "instrument.known_at")
        if not all(math.isfinite(value) and value > 0 for value in (
            self.contract_size,
            self.price_tick,
        )) or self.lot_step <= 0:
            raise ValueError("instrument mechanics must be finite and positive")
        if not self.root_symbol or not self.exchange or not self.sessions or not self.source:
            raise ValueError("instrument identity, sessions, and source are required")
        if self.effective_to is not None and self.effective_to <= self.effective_from:
            raise ValueError("instrument effective window is invalid")


@dataclass(frozen=True)
class DailyTradingSpec:
    contract_code: str
    exchange_trade_date: date
    pre_settlement: float
    limit_up: float
    limit_down: float
    margin_rate_long: float
    margin_rate_short: float
    fee_schedule_id: str
    effective_from: datetime
    effective_to: datetime
    source: str
    known_at: datetime

    def __post_init__(self) -> None:
        _window(self.effective_from, self.effective_to, self.known_at, "daily")
        values = (
            self.pre_settlement,
            self.limit_up,
            self.limit_down,
            self.margin_rate_long,
            self.margin_rate_short,
        )
        if not all(math.isfinite(value) for value in values):
            raise ValueError("daily mechanics must be finite")
        if not self.limit_down < self.pre_settlement < self.limit_up:
            raise ValueError("daily price limits must enclose pre_settlement")
        if min(self.margin_rate_long, self.margin_rate_short) <= 0:
            raise ValueError("margin rates must be positive")
        if not self.contract_code or not self.fee_schedule_id or not self.source:
            raise ValueError("daily identity, fee schedule, and source are required")


@dataclass(frozen=True)
class ContractLifecycleSpec:
    contract_code: str
    listed_on: date
    last_trade_date: date
    delivery_start: date | None
    delivery_end: date | None
    client_delivery_eligible: bool
    effective_from: datetime
    effective_to: datetime | None
    source: str
    known_at: datetime

    def __post_init__(self) -> None:
        _window(self.effective_from, self.effective_to, self.known_at, "lifecycle")
        if self.last_trade_date < self.listed_on:
            raise ValueError("last_trade_date precedes listed_on")


@dataclass(frozen=True)
class OrderCapabilitySpec:
    exchange: str
    gateway: str
    root_symbol: str | None
    contract_code: str | None
    order_type: str
    supported: bool
    max_order_size: int | None
    effective_from: datetime
    effective_to: datetime | None
    source: str
    known_at: datetime

    def __post_init__(self) -> None:
        _window(self.effective_from, self.effective_to, self.known_at, "capability")
        if self.max_order_size is not None and self.max_order_size <= 0:
            raise ValueError("max_order_size must be positive")


@dataclass(frozen=True)
class TradingStatusSpec:
    contract_code: str
    exchange_trade_date: date
    tradable: bool
    suspended: bool
    close_only: bool
    max_position: int | None
    status_reason: str
    effective_from: datetime
    effective_to: datetime
    source: str
    known_at: datetime

    def __post_init__(self) -> None:
        _window(self.effective_from, self.effective_to, self.known_at, "status")


@dataclass(frozen=True)
class ExecutionCostSpec:
    fee_schedule_id: str
    contract_code: str | None
    stressed_round_trip_fee_cash: float
    stressed_entry_slippage_ticks: float
    stressed_round_trip_slippage_ticks: float
    effective_from: datetime
    effective_to: datetime | None
    source: str
    known_at: datetime
    open_fee_rate: float = 0.0
    close_fee_rate: float = 0.0
    close_today_fee_rate: float = 0.0
    fee_per_lot_open: float = 0.0
    fee_per_lot_close: float = 0.0
    fee_per_lot_close_today: float = 0.0
    fee_stress_multiplier: float = 1.0

    def __post_init__(self) -> None:
        _window(self.effective_from, self.effective_to, self.known_at, "cost")
        values = (
            self.stressed_round_trip_fee_cash,
            self.stressed_entry_slippage_ticks,
            self.stressed_round_trip_slippage_ticks,
            self.open_fee_rate,
            self.close_fee_rate,
            self.close_today_fee_rate,
            self.fee_per_lot_open,
            self.fee_per_lot_close,
            self.fee_per_lot_close_today,
        )
        if not all(math.isfinite(value) and value >= 0 for value in values):
            raise ValueError("execution costs must be finite and nonnegative")
        if self.stressed_entry_slippage_ticks > self.stressed_round_trip_slippage_ticks:
            raise ValueError("entry slippage cannot exceed round-trip slippage")
        if not math.isfinite(self.fee_stress_multiplier) or self.fee_stress_multiplier <= 0:
            raise ValueError("fee stress multiplier must be finite and positive")
        if not self.fee_schedule_id or not self.source:
            raise ValueError("execution cost schedule and source are required")


@dataclass(frozen=True)
class ExecutionMetadataSnapshot:
    instrument: InstrumentSpec
    daily: DailyTradingSpec
    lifecycle: ContractLifecycleSpec
    capability: Mapping[str, OrderCapabilitySpec]
    status: TradingStatusSpec
    contract_size: float
    price_tick: float
    limit_up: float
    limit_down: float
    stressed_round_trip_fee_cash: float
    stressed_entry_slippage_ticks: float
    stressed_round_trip_slippage_ticks: float
    assembled_at: datetime
    order_event: datetime
    metadata_hash: str
    fee_source: str
    fee_effective_from: datetime
    fee_known_at: datetime
    open_fee_rate: float = 0.0
    close_fee_rate: float = 0.0
    close_today_fee_rate: float = 0.0
    fee_per_lot_open: float = 0.0
    fee_per_lot_close: float = 0.0
    fee_per_lot_close_today: float = 0.0
    fee_stress_multiplier: float = 1.0

    def __post_init__(self) -> None:
        object.__setattr__(self, "capability", MappingProxyType(dict(self.capability)))

    @property
    def round_trip_cost_price(self) -> float:
        return (
            self.stressed_round_trip_fee_cash / self.contract_size
            + self.stressed_round_trip_slippage_ticks * self.price_tick
        )


class MetadataStore:
    """In-memory repository used by CSV/database adapters without implicit defaults."""

    def __init__(
        self,
        *,
        instruments: Iterable[InstrumentSpec],
        daily_specs: Iterable[DailyTradingSpec],
        lifecycles: Iterable[ContractLifecycleSpec],
        capabilities: Iterable[OrderCapabilitySpec],
        statuses: Iterable[TradingStatusSpec],
        costs: Iterable[ExecutionCostSpec],
    ) -> None:
        self.instruments = list(instruments)
        self.daily_specs = list(daily_specs)
        self.lifecycles = list(lifecycles)
        self.capabilities = list(capabilities)
        self.statuses = list(statuses)
        self.costs = list(costs)

    def execution_snapshot(
        self,
        *,
        root_symbol: str,
        exchange: str,
        contract_code: str,
        exchange_trade_date: date,
        gateway: str,
        order_types: tuple[str, ...],
        decision_asof: datetime,
        order_event: datetime,
    ) -> ExecutionMetadataSnapshot:
        _aware(decision_asof, "decision_asof")
        _aware(order_event, "order_event")
        if order_event < decision_asof:
            raise ValueError("order_event must not precede decision_asof")
        instrument = _exactly_one(
            [
                item for item in self.instruments
                if item.root_symbol == root_symbol
                and item.exchange == exchange
                and item.effective_from <= exchange_trade_date
                and (item.effective_to is None or exchange_trade_date < item.effective_to)
                and item.known_at <= decision_asof
            ],
            "instrument",
            "BLOCKED_METADATA",
        )
        daily = _exactly_one(
            [
                item for item in self.daily_specs
                if item.contract_code == contract_code
                and item.exchange_trade_date == exchange_trade_date
                and _visible(
                    item.effective_from,
                    item.effective_to,
                    item.known_at,
                    order_event,
                    decision_asof,
                )
            ],
            "daily trading spec",
            "BLOCKED_METADATA",
        )
        lifecycle = _exactly_one(
            [
                item for item in self.lifecycles
                if item.contract_code == contract_code
                and item.listed_on <= exchange_trade_date <= item.last_trade_date
                and _visible(
                    item.effective_from,
                    item.effective_to,
                    item.known_at,
                    order_event,
                    decision_asof,
                )
            ],
            "contract lifecycle",
            "BLOCKED_LIFECYCLE",
        )
        status = _exactly_one(
            [
                item for item in self.statuses
                if item.contract_code == contract_code
                and item.exchange_trade_date == exchange_trade_date
                and _visible(
                    item.effective_from,
                    item.effective_to,
                    item.known_at,
                    order_event,
                    decision_asof,
                )
            ],
            "trading status",
            "BLOCKED_TRADING_STATUS",
        )
        if not status.tradable or status.suspended or status.close_only:
            raise BlockedMetadataError(
                f"contract is not openable: {status.status_reason}", "BLOCKED_TRADING_STATUS"
            )
        selected_capabilities = {
            order_type: self._capability(
                exchange,
                gateway,
                root_symbol,
                contract_code,
                order_type,
                order_event,
                decision_asof,
            )
            for order_type in order_types
        }
        cost = _select_specific_cost(
            self.costs,
            daily.fee_schedule_id,
            contract_code,
            order_event,
            decision_asof,
        )
        payload = {
            "instrument": asdict(instrument),
            "daily": asdict(daily),
            "lifecycle": asdict(lifecycle),
            "status": asdict(status),
            "capability": {key: asdict(value) for key, value in selected_capabilities.items()},
            "cost": asdict(cost),
            "assembled_at": decision_asof,
            "order_event": order_event,
        }
        digest = hashlib.sha256(
            json.dumps(payload, sort_keys=True, default=str, separators=(",", ":")).encode()
        ).hexdigest()
        return ExecutionMetadataSnapshot(
            instrument=instrument,
            daily=daily,
            lifecycle=lifecycle,
            capability=selected_capabilities,
            status=status,
            contract_size=instrument.contract_size,
            price_tick=instrument.price_tick,
            limit_up=daily.limit_up,
            limit_down=daily.limit_down,
            stressed_round_trip_fee_cash=cost.stressed_round_trip_fee_cash,
            stressed_entry_slippage_ticks=cost.stressed_entry_slippage_ticks,
            stressed_round_trip_slippage_ticks=cost.stressed_round_trip_slippage_ticks,
            assembled_at=decision_asof,
            order_event=order_event,
            metadata_hash=digest,
            fee_source=cost.source,
            fee_effective_from=cost.effective_from,
            fee_known_at=cost.known_at,
            open_fee_rate=cost.open_fee_rate,
            close_fee_rate=cost.close_fee_rate,
            close_today_fee_rate=cost.close_today_fee_rate,
            fee_per_lot_open=cost.fee_per_lot_open,
            fee_per_lot_close=cost.fee_per_lot_close,
            fee_per_lot_close_today=cost.fee_per_lot_close_today,
            fee_stress_multiplier=cost.fee_stress_multiplier,
        )

    def coverage_report(self, requests: Iterable[Mapping[str, object]]) -> pd.DataFrame:
        """Report whether every potential order event has a complete mechanics snapshot."""
        rows: list[dict[str, object]] = []
        for request_index, raw in enumerate(requests):
            request = dict(raw)
            contract = str(request.get("contract_code", ""))
            trade_date = request.get("exchange_trade_date")
            decision_asof = request.get("decision_asof")
            order_event = request.get("order_event")
            order_types = tuple(str(value) for value in request.get("order_types", ()))
            try:
                root_symbol = str(request["root_symbol"])
                exchange = str(request["exchange"])
                gateway = str(request["gateway"])
                if not isinstance(trade_date, date):
                    raise TypeError("exchange_trade_date must be a date")
                if not isinstance(decision_asof, datetime) or not isinstance(
                    order_event, datetime
                ):
                    raise TypeError("decision_asof and order_event must be datetimes")
                _aware(decision_asof, "decision_asof")
                _aware(order_event, "order_event")
                if order_event < decision_asof:
                    raise ValueError("order_event must not precede decision_asof")
            except (KeyError, TypeError, ValueError) as exc:
                rows.append(
                    _coverage_row(
                        request_index,
                        contract,
                        trade_date,
                        decision_asof,
                        order_event,
                        "execution_metadata",
                        False,
                        "BLOCKED_METADATA",
                        str(exc),
                    )
                )
                continue

            def select_instrument(
                root_symbol: str = root_symbol,
                exchange: str = exchange,
                trade_date: date = trade_date,
                decision_asof: datetime = decision_asof,
            ) -> InstrumentSpec:
                return _exactly_one(
                    [
                        item for item in self.instruments
                        if item.root_symbol == root_symbol
                        and item.exchange == exchange
                        and item.effective_from <= trade_date
                        and (item.effective_to is None or trade_date < item.effective_to)
                        and item.known_at <= decision_asof
                    ],
                    "instrument",
                    "BLOCKED_METADATA",
                )

            def select_daily(
                contract: str = contract,
                trade_date: date = trade_date,
                order_event: datetime = order_event,
                decision_asof: datetime = decision_asof,
            ) -> DailyTradingSpec:
                return _exactly_one(
                    [
                        item for item in self.daily_specs
                        if item.contract_code == contract
                        and item.exchange_trade_date == trade_date
                        and _visible(
                            item.effective_from,
                            item.effective_to,
                            item.known_at,
                            order_event,
                            decision_asof,
                        )
                    ],
                    "daily trading spec",
                    "BLOCKED_METADATA",
                )

            def select_lifecycle(
                contract: str = contract,
                trade_date: date = trade_date,
                order_event: datetime = order_event,
                decision_asof: datetime = decision_asof,
            ) -> ContractLifecycleSpec:
                return _exactly_one(
                    [
                        item for item in self.lifecycles
                        if item.contract_code == contract
                        and item.listed_on <= trade_date <= item.last_trade_date
                        and _visible(
                            item.effective_from,
                            item.effective_to,
                            item.known_at,
                            order_event,
                            decision_asof,
                        )
                    ],
                    "contract lifecycle",
                    "BLOCKED_LIFECYCLE",
                )

            def select_status(
                contract: str = contract,
                trade_date: date = trade_date,
                order_event: datetime = order_event,
                decision_asof: datetime = decision_asof,
            ) -> TradingStatusSpec:
                status = _exactly_one(
                    [
                        item for item in self.statuses
                        if item.contract_code == contract
                        and item.exchange_trade_date == trade_date
                        and _visible(
                            item.effective_from,
                            item.effective_to,
                            item.known_at,
                            order_event,
                            decision_asof,
                        )
                    ],
                    "trading status",
                    "BLOCKED_TRADING_STATUS",
                )
                if not status.tradable or status.suspended or status.close_only:
                    raise BlockedMetadataError(
                        f"contract is not openable: {status.status_reason}",
                        "BLOCKED_TRADING_STATUS",
                    )
                return status

            checks: list[tuple[str, str, Callable[[], object]]] = [
                ("instrument", "BLOCKED_METADATA", select_instrument),
                ("daily", "BLOCKED_METADATA", select_daily),
                ("lifecycle", "BLOCKED_LIFECYCLE", select_lifecycle),
                ("status", "BLOCKED_TRADING_STATUS", select_status),
                (
                    "cost",
                    "BLOCKED_METADATA",
                    lambda select_daily=select_daily,
                    contract=contract,
                    order_event=order_event,
                    decision_asof=decision_asof: _select_specific_cost(
                        self.costs,
                        select_daily().fee_schedule_id,
                        contract,
                        order_event,
                        decision_asof,
                    ),
                ),
            ]
            checks.extend(
                (
                    f"capability:{order_type}",
                    "BLOCKED_ORDER_CAPABILITY",
                    lambda order_type=order_type,
                    exchange=exchange,
                    gateway=gateway,
                    root_symbol=root_symbol,
                    contract=contract,
                    order_event=order_event,
                    decision_asof=decision_asof: self._capability(
                        exchange,
                        gateway,
                        root_symbol,
                        contract,
                        order_type,
                        order_event,
                        decision_asof,
                    ),
                )
                for order_type in order_types
            )
            for field, default_reason, check in checks:
                try:
                    check()
                except (BlockedMetadataError, TypeError, ValueError) as exc:
                    reason = (
                        exc.reason_code
                        if isinstance(exc, BlockedMetadataError)
                        else default_reason
                    )
                    rows.append(
                        _coverage_row(
                            request_index,
                            contract,
                            trade_date,
                            decision_asof,
                            order_event,
                            field,
                            False,
                            reason,
                            str(exc),
                        )
                    )
                else:
                    rows.append(
                        _coverage_row(
                            request_index,
                            contract,
                            trade_date,
                            decision_asof,
                            order_event,
                            field,
                            True,
                            "",
                            "",
                        )
                    )
        return pd.DataFrame(
            rows,
            columns=[
                "request_index", "contract_code", "exchange_trade_date", "decision_asof",
                "order_event", "field", "covered", "reason_code", "detail",
            ],
        )

    def _capability(
        self,
        exchange: str,
        gateway: str,
        root_symbol: str,
        contract_code: str,
        order_type: str,
        order_event: datetime,
        decision_asof: datetime,
    ) -> OrderCapabilitySpec:
        matches = [
            item for item in self.capabilities
            if item.exchange == exchange
            and item.gateway == gateway
            and item.order_type == order_type
            and item.root_symbol in (None, root_symbol)
            and item.contract_code in (None, contract_code)
            and _visible(
                item.effective_from,
                item.effective_to,
                item.known_at,
                order_event,
                decision_asof,
            )
        ]
        if not matches:
            raise BlockedMetadataError(
                f"order capability is missing for {order_type}", "BLOCKED_ORDER_CAPABILITY"
            )
        scores = [2 if item.contract_code else 1 if item.root_symbol else 0 for item in matches]
        selected = [
            item for item, score in zip(matches, scores, strict=True) if score == max(scores)
        ]
        if len(selected) != 1:
            raise BlockedMetadataError(
                f"overlapping order capability for {order_type}", "BLOCKED_ORDER_CAPABILITY"
            )
        if not selected[0].supported:
            raise BlockedMetadataError(
                f"order type {order_type} is unsupported", "BLOCKED_ORDER_CAPABILITY"
            )
        return selected[0]


def _select_specific_cost(
    costs: list[ExecutionCostSpec],
    schedule_id: str,
    contract_code: str,
    order_event: datetime,
    decision_asof: datetime,
) -> ExecutionCostSpec:
    matches = [
        item for item in costs
        if item.fee_schedule_id == schedule_id
        and item.contract_code in (None, contract_code)
        and _visible(
            item.effective_from,
            item.effective_to,
            item.known_at,
            order_event,
            decision_asof,
        )
    ]
    if not matches:
        raise BlockedMetadataError("execution cost is missing", "BLOCKED_METADATA")
    specific = [item for item in matches if item.contract_code == contract_code]
    selected = specific or matches
    return _exactly_one(selected, "execution cost", "BLOCKED_METADATA")


def _coverage_row(
    request_index: int,
    contract_code: str,
    exchange_trade_date: object,
    decision_asof: object,
    order_event: object,
    field: str,
    covered: bool,
    reason_code: str,
    detail: str,
) -> dict[str, object]:
    return {
        "request_index": request_index,
        "contract_code": contract_code,
        "exchange_trade_date": exchange_trade_date,
        "decision_asof": decision_asof,
        "order_event": order_event,
        "field": field,
        "covered": int(covered),
        "reason_code": reason_code,
        "detail": detail,
    }


def _exactly_one(items: list, label: str, reason_code: str):
    if len(items) != 1:
        raise BlockedMetadataError(
            f"expected one effective {label}, got {len(items)}", reason_code
        )
    return items[0]


def _visible(
    effective_from: datetime,
    effective_to: datetime | None,
    known_at: datetime,
    order_event: datetime,
    decision_asof: datetime,
) -> bool:
    return (
        effective_from <= order_event
        and (effective_to is None or order_event < effective_to)
        and known_at <= decision_asof
    )


def _aware(value: datetime, label: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{label} must be timezone-aware")


def _window(
    effective_from: datetime,
    effective_to: datetime | None,
    known_at: datetime,
    label: str,
) -> None:
    _aware(effective_from, f"{label}.effective_from")
    _aware(known_at, f"{label}.known_at")
    if effective_to is not None:
        _aware(effective_to, f"{label}.effective_to")
        if effective_to <= effective_from:
            raise ValueError(f"{label} has an invalid effective window")


__all__ = [
    "BlockedMetadataError",
    "ContractLifecycleSpec",
    "DailyTradingSpec",
    "ExecutionCostSpec",
    "ExecutionMetadataSnapshot",
    "InstrumentSpec",
    "MetadataStore",
    "OrderCapabilitySpec",
    "TradingStatusSpec",
]
