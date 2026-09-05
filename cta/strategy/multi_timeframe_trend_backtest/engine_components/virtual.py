"""Chase-high shadow order and position handling."""
from __future__ import annotations

from dataclasses import dataclass, field
import math
from typing import Any

import pandas as pd

from cta.config.replay_common import BaseReplayConfig
from cta.strategy.brooks.cycle_v1.backtest.execution_metadata import BACKTEST_GATEWAY
from cta.strategy.brooks.cycle_v1.instruments.metadata import BlockedMetadataError
from cta.strategy.multi_timeframe_trend_rules import advance_trailing_stop

from .audit import _portfolio_risk_event, _rejection


@dataclass
class _VirtualOrder:
    root_symbol: str
    candidate_id: str
    contract_code: str
    direction: int
    trigger: float
    stop_price: float
    expires_at: pd.Timestamp
    active_at: pd.Timestamp
    tick_size: float


@dataclass
class _VirtualPosition:
    root_symbol: str
    candidate_id: str
    contract_code: str
    direction: int
    entry_time: pd.Timestamp
    entry_price: float
    stop_price: float
    initial_risk: float
    tick_size: float
    last_structure_known_at: object | None = None


@dataclass
class _ChaseHighState:
    """Rolling record of virtual chase-high trades at portfolio level."""

    outcomes: list[float] = field(default_factory=list)

    def record(self, realized_r: float) -> None:
        if math.isfinite(realized_r):
            self.outcomes.append(float(realized_r))

    def window(self, lookback: int) -> list[float]:
        return self.outcomes[-int(lookback) :]

    def prior_r(self, lookback: int) -> float:
        if not self.outcomes:
            return math.nan
        return float(sum(self.window(lookback)))


def _cancel_virtual_on_roll(
    root_symbol: str,
    active_contract: str,
    virtual_orders: dict[str, _VirtualOrder],
    virtual_positions: dict[str, _VirtualPosition],
) -> bool:
    """Discard shadow exposure tied to a contract that is no longer active."""
    cancelled = False
    order = virtual_orders.get(root_symbol)
    if order is not None and order.contract_code != active_contract:
        virtual_orders.pop(root_symbol, None)
        cancelled = True
    position = virtual_positions.get(root_symbol)
    if position is not None and position.contract_code != active_contract:
        virtual_positions.pop(root_symbol, None)
        cancelled = True
    return cancelled


def _advance_virtual_book(
    virtual_orders: dict[str, _VirtualOrder],
    virtual_positions: dict[str, _VirtualPosition],
    bars_at_event: dict[str, Any],
    contexts: dict[str, dict[pd.Timestamp, dict[str, Any]]],
    timestamp: pd.Timestamp,
    chase_state: _ChaseHighState,
    config: BaseReplayConfig,
    event_rows: list[dict[str, Any]],
) -> None:
    """Advance shared chase-high shadow orders and positions."""
    _advance_virtual_positions(
        virtual_positions,
        bars_at_event,
        contexts,
        timestamp,
        chase_state,
        config,
        event_rows,
    )
    for root_symbol in sorted(virtual_orders):
        bar = bars_at_event.get(root_symbol)
        order = virtual_orders[root_symbol]
        if bar is None:
            continue
        if timestamp > order.expires_at:
            virtual_orders.pop(root_symbol, None)
            continue
        if timestamp < order.active_at:
            continue
        fill = _virtual_entry_price(order, bar)
        if not math.isfinite(fill):
            continue
        risk = abs(fill - order.stop_price)
        virtual_orders.pop(root_symbol, None)
        if risk <= 0:
            continue
        virtual_positions[root_symbol] = _VirtualPosition(
            root_symbol=root_symbol,
            candidate_id=order.candidate_id,
            contract_code=order.contract_code,
            direction=order.direction,
            entry_time=timestamp,
            entry_price=fill,
            stop_price=order.stop_price,
            initial_risk=risk,
            tick_size=order.tick_size,
        )


def _handle_chase_high_candidate(
    candidate: dict[str, Any],
    *,
    root_symbol: str,
    exchange: str,
    metadata_store: Any,
    chase_state: _ChaseHighState,
    chase_gate_candidates: set[str],
    virtual_orders: dict[str, _VirtualOrder],
    virtual_positions: dict[str, _VirtualPosition],
    config: BaseReplayConfig,
    event_rows: list[dict[str, Any]],
) -> tuple[bool, str]:
    """Handle one chase-high candidate; return handled and effective reason."""
    reason = str(candidate.get("filtered_reason", "") or "").strip()
    is_chase = (
        reason == "ENTRY_RANGE_POSITION_TOO_HIGH"
        and int(candidate.get("chase_high_candidate", 0) or 0) == 1
        and (
            config.chase_high_virtual_enabled
            or config.chase_high_entry_enabled
        )
    )
    if not is_chase:
        return False, reason
    allowed, detail = _chase_high_gate_open(chase_state, config)
    if allowed:
        event_rows.append(
            _portfolio_risk_event(
                pd.Timestamp(candidate["signal_time"]),
                "CHASE_HIGH_GATE_OPEN",
                detail=f"symbol={root_symbol};{detail}",
            )
        )
        chase_gate_candidates.add(str(candidate["candidate_id"]))
        return False, ""
    if not config.chase_high_virtual_enabled:
        event_rows.append(_rejection(root_symbol, candidate, reason))
        return True, reason
    tick = _virtual_tick_size(
        candidate,
        root_symbol=root_symbol,
        exchange=exchange,
        metadata_store=metadata_store,
    )
    if (
        math.isfinite(tick)
        and tick > 0
        and root_symbol not in virtual_orders
        and root_symbol not in virtual_positions
    ):
        virtual_orders[root_symbol] = _VirtualOrder(
            root_symbol=root_symbol,
            candidate_id=str(candidate["candidate_id"]),
            contract_code=str(candidate["contract_code"]),
            direction=int(candidate["direction"]),
            trigger=float(candidate["trigger"]),
            stop_price=float(candidate["stop_price"]),
            expires_at=pd.Timestamp(candidate["expires_at"]),
            active_at=pd.Timestamp(candidate["active_time"]),
            tick_size=tick,
        )
    event_rows.append(
        _rejection(
            root_symbol,
            candidate,
            reason,
            detail=f"virtual=1;{detail}",
        )
    )
    return True, reason


def _chase_high_gate_open(
    state: _ChaseHighState,
    config: BaseReplayConfig,
) -> tuple[bool, str]:
    """Return whether real chase-high entries are currently allowed, and why.

    Two independent switches. ``chase_high_entry_enabled`` decides whether a real
    chase-high entry is ever taken; ``chase_high_virtual_enabled`` only decides
    whether the shadow record keeps being maintained. Keeping the shadow on while
    entries are off is the intended default: the record shows whether chasing
    would be paying, at no risk.

    With no virtual history yet the gate stays shut: the strategy has no evidence
    that chasing pays, and the range-position rule is the tested default.
    """
    if not config.chase_high_entry_enabled:
        return False, "entry_disabled"
    window = state.window(config.chase_high_lookback)
    if not window:
        return False, "no_history"
    if len(window) < int(config.chase_high_min_samples):
        # K=5/K=8 are both negative in the validation sets, so short windows
        # are not enough evidence to reopen chase-high entries.
        return False, f"insufficient_samples;n={len(window)}"
    prior = float(sum(window))
    detail = f"prior_r={prior:.4g};n={len(window)}"
    return prior > float(config.chase_high_min_prior_r), detail


def _virtual_tick_size(
    candidate: dict[str, Any],
    *,
    root_symbol: str,
    exchange: str,
    metadata_store: Any,
) -> float:
    """Resolve the price tick for a virtual order, or NaN when unavailable.

    A virtual order that cannot be priced is simply not tracked; the shadow
    record is a diagnostic and must never block or crash a real run.
    """
    signal_time = pd.Timestamp(candidate["signal_time"])
    try:
        metadata = metadata_store.execution_snapshot(
            root_symbol=root_symbol,
            exchange=exchange,
            contract_code=str(candidate["contract_code"]),
            exchange_trade_date=candidate.get(
                "exchange_trade_date", signal_time.date()
            ),
            gateway=BACKTEST_GATEWAY,
            order_types=("STOP",),
            decision_asof=signal_time.to_pydatetime(),
            order_event=pd.Timestamp(candidate["active_time"]).to_pydatetime(),
        )
    except (LookupError, TypeError, ValueError, BlockedMetadataError):
        return math.nan
    try:
        return float(metadata.price_tick)
    except (AttributeError, TypeError, ValueError):
        return math.nan


def _virtual_entry_price(order: _VirtualOrder, bar: pd.Series) -> float:
    """Fill a virtual stop order the way a real one fills: at the worse of
    trigger and the bar open once the trigger is touched."""
    high = float(bar["high"])
    low = float(bar["low"])
    open_price = float(bar["open"])
    if order.direction > 0:
        if high < order.trigger:
            return math.nan
        return max(order.trigger, open_price)
    if low > order.trigger:
        return math.nan
    return min(order.trigger, open_price)


def _advance_virtual_positions(
    virtual_positions: dict[str, _VirtualPosition],
    bars_at_event: dict[str, pd.Series],
    contexts: dict[str, dict[pd.Timestamp, dict[str, Any]]],
    timestamp: pd.Timestamp,
    chase_state: _ChaseHighState,
    config: BaseReplayConfig,
    event_rows: list[dict[str, Any]],
) -> None:
    """Mark virtual chase-high positions to market and close the stopped ones."""
    for root_symbol in sorted(virtual_positions):
        bar = bars_at_event.get(root_symbol)
        if bar is None:
            continue
        position = virtual_positions[root_symbol]
        direction = position.direction
        if direction > 0:
            touched = float(bar["low"]) <= position.stop_price
            fill = min(float(bar["open"]), position.stop_price)
        else:
            touched = float(bar["high"]) >= position.stop_price
            fill = max(float(bar["open"]), position.stop_price)
        if touched:
            realized = direction * (fill - position.entry_price) / position.initial_risk
            chase_state.record(realized)
            event_rows.append(
                _portfolio_risk_event(
                    timestamp,
                    "CHASE_HIGH_VIRTUAL_CLOSED",
                    detail=(
                        f"symbol={root_symbol};candidate={position.candidate_id};"
                        f"realized_r={realized:.6g};"
                        f"prior_r={chase_state.prior_r(config.chase_high_lookback):.6g}"
                    ),
                )
            )
            virtual_positions.pop(root_symbol, None)
            continue
        context_row = contexts.get(root_symbol, {}).get(timestamp)
        if context_row is None:
            continue
        kind = "low" if direction > 0 else "high"
        swing = context_row.get(f"latest_swing_{kind}")
        atr_value = context_row.get("atr14")
        known_at = context_row.get(f"latest_swing_{kind}_known_at")
        if any(pd.isna(value) for value in (swing, atr_value, known_at)):
            continue
        last_known = position.last_structure_known_at
        if last_known is not None and pd.Timestamp(known_at) <= pd.Timestamp(last_known):
            continue
        position.stop_price = advance_trailing_stop(
            current_stop=position.stop_price,
            direction=direction,
            confirmed_swing=float(swing),
            atr_value=float(atr_value),
            buffer_atr=config.trailing_buffer_atr,
            tick_size=position.tick_size,
        )
        position.last_structure_known_at = known_at
