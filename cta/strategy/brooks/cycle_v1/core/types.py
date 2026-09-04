"""Stable, auditable contracts for Brooks cycle v1."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from enum import Enum
import hashlib
import json
import math
from types import MappingProxyType
from typing import Any


class MarketCycle(str, Enum):
    STRONG_BULL_BREAKOUT = "STRONG_BULL_BREAKOUT"
    BULL_TIGHT_CHANNEL = "BULL_TIGHT_CHANNEL"
    BULL_BROAD_CHANNEL = "BULL_BROAD_CHANNEL"
    TRADING_RANGE = "TRADING_RANGE"
    BEAR_BROAD_CHANNEL = "BEAR_BROAD_CHANNEL"
    BEAR_TIGHT_CHANNEL = "BEAR_TIGHT_CHANNEL"
    STRONG_BEAR_BREAKOUT = "STRONG_BEAR_BREAKOUT"
    TRANSITION = "TRANSITION"
    UNAVAILABLE = "UNAVAILABLE"


class RangeSubtype(str, Enum):
    BROAD = "BROAD"
    TIGHT_BREAKOUT_MODE = "TIGHT_BREAKOUT_MODE"
    UNTRADEABLE_TIGHT = "UNTRADEABLE_TIGHT"


class AlwaysIn(str, Enum):
    LONG = "ALWAYS_IN_LONG"
    NEUTRAL = "NEUTRAL"
    SHORT = "ALWAYS_IN_SHORT"


class TradeMode(str, Enum):
    SCALP = "SCALP"
    SWING = "SWING"
    NO_TRADE = "NO_TRADE"


class SetupType(str, Enum):
    BREAKOUT = "BREAKOUT"
    FIRST_PULLBACK = "FIRST_PULLBACK"
    H1 = "H1"
    H2 = "H2"
    WEDGE_PULLBACK = "WEDGE_PULLBACK"
    EMA_PULLBACK = "EMA_PULLBACK"
    FAILED_BREAKOUT = "FAILED_BREAKOUT"
    RANGE_REVERSAL = "RANGE_REVERSAL"
    TREND_RESUMPTION = "TREND_RESUMPTION"


V0_ENABLED_SETUPS = frozenset(
    {SetupType.BREAKOUT, SetupType.H1, SetupType.H2, SetupType.FAILED_BREAKOUT}
)


class OrderStatus(str, Enum):
    SIGNAL = "SIGNAL"
    PLANNED = "PLANNED"
    ACTIVE = "ACTIVE"
    PARTIAL = "PARTIAL"
    FILLED = "FILLED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"
    REJECTED = "REJECTED"


@dataclass(frozen=True, order=True)
class EventKey:
    timestamp: datetime
    sequence: int

    def __post_init__(self) -> None:
        if self.timestamp.tzinfo is None or self.timestamp.utcoffset() is None:
            raise ValueError("event timestamp must be timezone-aware")
        if self.sequence < 0:
            raise ValueError("event sequence must be nonnegative")


def assert_event_order(
    feature: EventKey,
    known: EventKey,
    decision: EventKey,
    active: EventKey,
    fill: EventKey | None = None,
) -> None:
    valid = feature <= known <= decision < active
    if fill is not None:
        valid = valid and active <= fill
    if not valid:
        raise ValueError(
            "event ordering requires feature <= known <= decision < active <= fill"
        )


@dataclass(frozen=True)
class CycleSnapshot:
    cycle: MarketCycle
    direction: int
    strength: float
    confidence: float
    bull_pressure: float
    bear_pressure: float
    range_subtype: RangeSubtype | None
    feature_event: EventKey
    evidence: Mapping[str, float | str] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.direction not in (-1, 0, 1):
            raise ValueError("direction must be -1, 0, or 1")
        if not 0 <= self.confidence <= 1:
            raise ValueError("confidence must be in [0, 1]")
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))

    @classmethod
    def unavailable(cls, event: EventKey, reason: str) -> CycleSnapshot:
        return cls(
            cycle=MarketCycle.UNAVAILABLE,
            direction=0,
            strength=0.0,
            confidence=0.0,
            bull_pressure=math.nan,
            bear_pressure=math.nan,
            range_subtype=None,
            feature_event=event,
            evidence={"reason": reason},
        )


@dataclass(frozen=True)
class TrendSnapshot:
    direction: int
    strength: float
    confidence: float
    cycle: MarketCycle
    major_support: float | None
    major_resistance: float | None
    feature_event: EventKey


@dataclass(frozen=True)
class PlanGeometry:
    entry: float
    stop: float
    stop_distance: float
    target: float
    nearest_obstacle: float
    gross_space_R: float
    cost_R: float
    net_space_R: float
    net_reward_R: float
    near_price_limit: bool
    obstacle_kind: str


@dataclass(frozen=True)
class SetupCandidate:
    candidate_id: str
    vt_symbol: str
    contract_code: str
    setup_type: SetupType
    direction: int
    context_cycle: MarketCycle
    signal_event: EventKey
    known_event: EventKey
    decision_event: EventKey
    active_event: EventKey
    entry_type: str
    trigger_price: float
    initial_stop: float
    target_price: float | None
    trade_mode: TradeMode
    expected_holding_bars: tuple[int, int]
    evidence: Mapping[str, float | str]
    invalidation: Mapping[str, float | str]

    def __post_init__(self) -> None:
        if self.direction not in (-1, 1):
            raise ValueError("candidate direction must be -1 or 1")
        if not self.vt_symbol or not self.contract_code:
            raise ValueError("candidate symbol and real contract are required")
        assert_event_order(
            self.signal_event,
            self.known_event,
            self.decision_event,
            self.active_event,
        )
        low, high = self.expected_holding_bars
        if low < 0 or high < low:
            raise ValueError("invalid expected holding bars")
        object.__setattr__(self, "evidence", MappingProxyType(dict(self.evidence)))
        object.__setattr__(self, "invalidation", MappingProxyType(dict(self.invalidation)))

    @classmethod
    def create(cls, *, rule_version: str, **values: Any) -> SetupCandidate:
        candidate = cls(candidate_id="", **values)
        return candidate.with_stable_id(rule_version)

    def with_stable_id(self, rule_version: str) -> SetupCandidate:
        if not rule_version:
            raise ValueError("rule_version is required")
        payload = {
            "rule_version": rule_version,
            "vt_symbol": self.vt_symbol,
            "contract_code": self.contract_code,
            "setup_type": self.setup_type.value,
            "direction": self.direction,
            "context_cycle": self.context_cycle.value,
            "signal_event": _event_payload(self.signal_event),
            "known_event": _event_payload(self.known_event),
            "decision_event": _event_payload(self.decision_event),
            "active_event": _event_payload(self.active_event),
            "entry_type": self.entry_type,
            "trigger_price": self.trigger_price,
            "initial_stop": self.initial_stop,
            "target_price": self.target_price,
            "trade_mode": self.trade_mode.value,
            "expected_holding_bars": self.expected_holding_bars,
            "evidence": dict(self.evidence),
            "invalidation": dict(self.invalidation),
        }
        encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"), default=str)
        return replace(self, candidate_id=hashlib.sha256(encoded.encode()).hexdigest()[:24])


@dataclass(frozen=True)
class OrderPlan:
    order_id: str
    candidate: SetupCandidate
    geometry: PlanGeometry
    quantity: int
    status: OrderStatus
    expires_at: EventKey
    metadata_hash: str
    rejection_reason: str = ""
    oco_group_id: str | None = None

    def __post_init__(self) -> None:
        if self.oco_group_id is not None and not self.oco_group_id.strip():
            raise ValueError("OCO group id must be nonempty when supplied")

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["candidate"]["setup_type"] = self.candidate.setup_type.value
        payload["candidate"]["context_cycle"] = self.candidate.context_cycle.value
        payload["candidate"]["trade_mode"] = self.candidate.trade_mode.value
        payload["status"] = self.status.value
        return payload


def _event_payload(event: EventKey) -> tuple[str, int]:
    return event.timestamp.isoformat(), event.sequence


__all__ = [
    "AlwaysIn",
    "CycleSnapshot",
    "EventKey",
    "MarketCycle",
    "OrderPlan",
    "OrderStatus",
    "PlanGeometry",
    "RangeSubtype",
    "SetupCandidate",
    "SetupType",
    "TradeMode",
    "TrendSnapshot",
    "V0_ENABLED_SETUPS",
    "assert_event_order",
]
