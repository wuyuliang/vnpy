"""Single-writer shared-account coordinator for multiple vn.py CTA shells."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
import math
import threading
from typing import Any

import pandas as pd

from .data import MinuteBar
from .engine import (
    BrokerOrderEvent,
    BrokerTradeEvent,
    EngineSnapshot,
    ExitReason,
    Offset,
    OrderIntent,
    OrderStatus,
    OrderType,
    ScalpEngine,
    TradePlan,
)
from .metadata import FeeMarginSpec, InstrumentSpec
from .risk_policy import (
    AccountSnapshot,
    RiskEvaluationContext,
    RiskSnapshot,
    RuleOnlyRiskAdapter,
    RuleRiskDecision,
    calculate_order_size,
)
from .session import SessionCalendar, SessionError
from .setups import SetupCandidate


@dataclass(frozen=True)
class PortfolioSnapshot:
    initial_equity: float
    marked_equity: float
    margin_used_and_reserved: float
    margin_usage: float
    available_funds: float
    portfolio_open_risk: float
    symbol_open_risk: dict[str, float]
    engines: dict[str, EngineSnapshot]
    pending_watermarks: tuple[str, ...]
    stale_symbols: tuple[str, ...]
    risk_decisions: tuple[dict[str, Any], ...]

    def to_dict(self) -> dict[str, Any]:
        return {
            "initial_equity": self.initial_equity,
            "marked_equity": self.marked_equity,
            "margin_used_and_reserved": self.margin_used_and_reserved,
            "margin_usage": self.margin_usage,
            "available_funds": self.available_funds,
            "portfolio_open_risk": self.portfolio_open_risk,
            "symbol_open_risk": dict(self.symbol_open_risk),
            "engines": {
                symbol: snapshot.to_dict() for symbol, snapshot in self.engines.items()
            },
            "pending_watermarks": list(self.pending_watermarks),
            "stale_symbols": list(self.stale_symbols),
            "risk_decisions": list(self.risk_decisions),
        }


@dataclass(frozen=True)
class _ApprovedEntryRisk:
    risk_snapshot: RiskSnapshot
    direction: int
    target_price: float
    max_symbol_open_risk_pct: float
    max_portfolio_open_risk_pct: float
    max_entry_margin_usage_pct: float


class BrooksScalpPortfolioCoordinator:
    """Own all symbol engines and serialize shared risk/account decisions."""

    def __init__(
        self,
        *,
        initial_equity: float = 200_000.0,
        mode: str = "historical",
        stale_tolerance_seconds: int = 90,
        risk_adapter: RuleOnlyRiskAdapter | None = None,
    ) -> None:
        if initial_equity <= 0:
            raise ValueError("initial_equity must be positive")
        if mode not in {"historical", "live"}:
            raise ValueError("mode must be historical or live")
        if mode == "live" and risk_adapter is None:
            raise ValueError("live coordinator requires a fail-closed risk adapter")
        self.initial_equity = float(initial_equity)
        self.mode = mode
        self.stale_tolerance_seconds = int(stale_tolerance_seconds)
        self.risk_adapter = risk_adapter
        self._engines: dict[str, ScalpEngine] = {}
        self._calendars: dict[str, SessionCalendar] = {}
        self._pending: dict[datetime, dict[str, MinuteBar]] = {}
        self._first_received: dict[datetime, datetime] = {}
        self._stale_symbols: set[str] = set()
        self._risk_decisions: list[dict[str, Any]] = []
        self._approved_entries: dict[str, _ApprovedEntryRisk] = {}
        self._latest_risk_snapshots: dict[str, RiskSnapshot] = {}
        self._lock = threading.RLock()

    def register_symbol(
        self,
        symbol: str,
        engine: ScalpEngine,
        calendar: SessionCalendar,
    ) -> None:
        with self._lock:
            if symbol in self._engines:
                raise ValueError(f"symbol is already registered: {symbol}")
            self._engines[symbol] = engine
            self._calendars[symbol] = calendar

    def update_symbol_metadata(
        self,
        symbol: str,
        instrument: InstrumentSpec,
        fee_spec: FeeMarginSpec,
        calendar: SessionCalendar,
    ) -> None:
        with self._lock:
            if symbol not in self._engines:
                raise ValueError(f"unregistered symbol: {symbol}")
            self._engines[symbol].switch_contract_metadata(instrument, fee_spec)
            self._calendars[symbol] = calendar

    def deactivate_symbol(self, symbol: str) -> None:
        """Stop waiting for a historically exhausted symbol while retaining its ledger."""
        with self._lock:
            if self.mode != "historical":
                raise ValueError("symbols can only be deactivated in historical mode")
            if symbol not in self._engines:
                raise ValueError(f"unregistered symbol: {symbol}")
            self._calendars.pop(symbol, None)

    def cancel_entries_and_flatten(
        self,
        reason: ExitReason = ExitReason.EXCHANGE_KILL_SWITCH,
    ) -> list[OrderIntent]:
        with self._lock:
            intents: list[OrderIntent] = []
            for engine in self._engines.values():
                intents.extend(engine.request_exit(reason))
            return intents

    def update_risk_snapshot(self, symbol: str, snapshot: RiskSnapshot) -> None:
        """Publish the latest completed-bar risk state for trigger-time checks."""
        with self._lock:
            if symbol not in self._engines:
                raise ValueError(f"unregistered symbol: {symbol}")
            self._latest_risk_snapshots[symbol] = snapshot

    def expected_active_symbols(
        self,
        bar_start: datetime,
        bar_end: datetime,
    ) -> tuple[str, ...]:
        active: list[str] = []
        for symbol in sorted(self._calendars):
            try:
                self._calendars[symbol].assign_bar(bar_start, bar_end)
            except SessionError as exc:
                if exc.code == "OUTSIDE_SESSION":
                    continue
                raise
            active.append(symbol)
        return tuple(active)

    def on_symbol_bar(
        self,
        symbol: str,
        bar: MinuteBar,
        *,
        received_at: datetime | None = None,
    ) -> list[OrderIntent]:
        with self._lock:
            if symbol not in self._engines:
                raise ValueError(f"unregistered symbol: {symbol}")
            if bar.vt_symbol != symbol:
                raise ValueError("bar symbol does not match coordinator key")
            expected = self.expected_active_symbols(bar.bar_start, bar.bar_end)
            if symbol not in expected:
                raise ValueError(f"bar arrived outside the configured active session: {symbol}")
            bucket = self._pending.setdefault(bar.bar_end, {})
            if symbol in bucket:
                raise ValueError(f"duplicate symbol bar at watermark: {symbol} {bar.bar_end}")
            bucket[symbol] = bar
            arrived = received_at or bar.bar_end
            self._first_received.setdefault(bar.bar_end, arrived)
            if set(bucket) != set(expected):
                return []
            return self._process_watermark(bar.bar_end, expected, allow_entries=True)

    def flush_stale(self, now: datetime) -> list[OrderIntent]:
        """In live mode, process received exits but block all entries after timeout."""
        with self._lock:
            if self.mode != "live":
                if self._pending:
                    raise ValueError("historical watermark has missing active bars")
                return []
            intents: list[OrderIntent] = []
            for watermark in sorted(tuple(self._pending)):
                first = self._first_received[watermark]
                if (now - first).total_seconds() <= self.stale_tolerance_seconds:
                    continue
                bars = self._pending[watermark]
                any_bar = next(iter(bars.values()))
                expected = self.expected_active_symbols(any_bar.bar_start, any_bar.bar_end)
                missing = set(expected).difference(bars)
                self._stale_symbols.update(missing)
                intents.extend(
                    self._process_watermark(
                        watermark,
                        tuple(sorted(bars)),
                        allow_entries=False,
                    )
                )
            return intents

    def submit_candidate(
        self,
        candidate: SetupCandidate,
        plan: TradePlan,
        risk_snapshot: RiskSnapshot,
        risk_inputs: dict[str, Any],
    ) -> tuple[OrderIntent | None, RuleRiskDecision]:
        """Size and approve one candidate against the shared marked account."""
        with self._lock:
            engine = self._engines[candidate.symbol]
            account = self._account_snapshot()
            sizing_candidate = {
                **candidate.to_dict(),
                "trigger_price": plan.trigger_price,
                "net_stop_loss_per_contract": plan.net_stop_loss_per_contract,
                "margin_rate": (
                    engine.fee_spec.margin_rate_long
                    if candidate.direction > 0
                    else engine.fee_spec.margin_rate_short
                ),
            }
            account_config = (
                self.risk_adapter.config.account if self.risk_adapter is not None else None
            )
            size = calculate_order_size(
                sizing_candidate,
                account,
                engine.instrument,
                risk_snapshot,
                max_symbol_open_risk_pct=(
                    account_config.max_symbol_open_risk_pct
                    if account_config is not None
                    else 0.0025
                ),
                max_portfolio_open_risk_pct=(
                    account_config.max_portfolio_open_risk_pct
                    if account_config is not None
                    else 0.0050
                ),
                max_entry_margin_usage_pct=(
                    account_config.max_entry_margin_usage_pct
                    if account_config is not None
                    else 0.30
                ),
            )
            context = RiskEvaluationContext(
                now=candidate.setup_bar_end,
                exchange_trade_date=pd.Timestamp(
                    risk_inputs.get("exchange_trade_date", candidate.setup_bar_end)
                ).date(),
                account=account,
                risk=risk_snapshot,
            )
            payload = {**sizing_candidate, **risk_inputs, "offset": "open"}
            if self.risk_adapter is None:
                decision = RuleRiskDecision(
                    size.quantity > 0,
                    size.quantity,
                    "ok" if size.quantity > 0 else size.reason,
                )
            else:
                decision = self.risk_adapter.evaluate_entry(
                    payload,
                    context,
                    proposed_quantity=size.quantity,
                )
            self._risk_decisions.append(
                {
                    "candidate_id": candidate.candidate_id,
                    "symbol": candidate.symbol,
                    "phase": "signal",
                    **decision.to_dict(),
                    "risk_budget": size.risk_budget,
                    "planned_open_risk": (
                        decision.quantity * plan.net_stop_loss_per_contract
                    ),
                    "net_stop_loss_per_contract": plan.net_stop_loss_per_contract,
                }
            )
            if not decision.allowed or decision.quantity <= 0:
                return None, decision
            self._latest_risk_snapshots[candidate.symbol] = risk_snapshot
            intent = engine.arm_candidate(
                candidate,
                quantity=decision.quantity,
                target_price=plan.target_price,
                net_stop_loss_per_contract=plan.net_stop_loss_per_contract,
            )
            self._approved_entries[intent.order_id] = _ApprovedEntryRisk(
                risk_snapshot=risk_snapshot,
                direction=candidate.direction,
                target_price=plan.target_price,
                max_symbol_open_risk_pct=(
                    account_config.max_symbol_open_risk_pct
                    if account_config is not None
                    else 0.0025
                ),
                max_portfolio_open_risk_pct=(
                    account_config.max_portfolio_open_risk_pct
                    if account_config is not None
                    else 0.0050
                ),
                max_entry_margin_usage_pct=(
                    account_config.max_entry_margin_usage_pct
                    if account_config is not None
                    else 0.30
                ),
            )
            return intent, decision

    def on_order(self, symbol: str, event: BrokerOrderEvent) -> list[OrderIntent]:
        with self._lock:
            return self._engines[symbol].on_order(event)

    def on_trade(self, symbol: str, event: BrokerTradeEvent) -> list[OrderIntent]:
        with self._lock:
            engine = self._engines[symbol]
            fill_count = len(engine.fills)
            intents = engine.on_trade(event)
            if len(engine.fills) == fill_count:
                return intents
            if event.offset is not Offset.OPEN:
                return intents
            approval = self._approved_entries.get(event.order_id)
            if approval is None:
                reduction = engine.request_immediate_exit(
                    ExitReason.ENTRY_RISK_REDUCTION
                )
                self._risk_decisions.append(
                    {
                        "candidate_id": event.candidate_id,
                        "symbol": symbol,
                        "phase": "post_fill",
                        "allowed": False,
                        "quantity": event.quantity,
                        "reason": "missing_entry_approval",
                        "risk_budget": 0.0,
                        "planned_open_risk": self._account_snapshot().portfolio_open_risk,
                        "reduction_submitted": any(
                            intent.order_type is OrderType.MARKET_EXIT
                            for intent in reduction
                        ),
                    }
                )
                intents.extend(reduction)
                return intents

            account = self._account_snapshot()
            symbol_risk = float(account.symbol_open_risk.get(symbol, 0.0))
            symbol_budget = account.marked_equity * approval.max_symbol_open_risk_pct
            portfolio_budget = (
                account.marked_equity * approval.max_portfolio_open_risk_pct
            )
            breached = (
                symbol_risk > symbol_budget + 1e-8
                or account.portfolio_open_risk > portfolio_budget + 1e-8
            )
            self._risk_decisions.append(
                {
                    "candidate_id": event.candidate_id,
                    "symbol": symbol,
                    "phase": "post_fill",
                    "allowed": not breached,
                    "quantity": event.quantity,
                    "reason": "entry_risk_breach" if breached else "ok",
                    "risk_budget": min(symbol_budget, portfolio_budget),
                    "planned_open_risk": max(
                        symbol_risk,
                        account.portfolio_open_risk,
                    ),
                    "net_stop_loss_per_contract": (
                        engine.position.initial_net_stop_loss_per_contract
                        if engine.position is not None
                        else 0.0
                    ),
                    "reduction_submitted": False,
                }
            )
            if breached:
                reduction = engine.request_immediate_exit(
                    ExitReason.ENTRY_RISK_REDUCTION
                )
                intents.extend(reduction)
                self._risk_decisions[-1]["reduction_submitted"] = any(
                    intent.order_type is OrderType.MARKET_EXIT
                    for intent in reduction
                )
            order = next(
                (value for value in engine.orders if value.order_id == event.order_id),
                None,
            )
            if order is None or order.remaining_quantity == 0:
                self._approved_entries.pop(event.order_id, None)
            return intents

    def snapshot(self) -> PortfolioSnapshot:
        with self._lock:
            snapshots = {
                symbol: engine.snapshot() for symbol, engine in sorted(self._engines.items())
            }
            pnl = sum(
                snapshot.marked_equity - self._engines[symbol].initial_cash
                for symbol, snapshot in snapshots.items()
            )
            marked = self.initial_equity + pnl
            margin = sum(
                snapshot.position_margin + snapshot.working_order_margin_reserve
                for snapshot in snapshots.values()
            )
            symbol_risk = self._symbol_open_risk(snapshots)
            return PortfolioSnapshot(
                initial_equity=self.initial_equity,
                marked_equity=marked,
                margin_used_and_reserved=margin,
                margin_usage=margin / marked if marked > 0 else float("inf"),
                available_funds=marked - margin,
                portfolio_open_risk=sum(symbol_risk.values()),
                symbol_open_risk=symbol_risk,
                engines=snapshots,
                pending_watermarks=tuple(
                    pd.Timestamp(value).isoformat() for value in sorted(self._pending)
                ),
                stale_symbols=tuple(sorted(self._stale_symbols)),
                risk_decisions=tuple(self._risk_decisions),
            )

    def account_state(self) -> dict[str, Any]:
        """Return O(symbols + active orders) state without copying ledger history."""
        with self._lock:
            pnl = sum(
                engine.marked_equity - engine.initial_cash
                for engine in self._engines.values()
            )
            marked = self.initial_equity + pnl
            margin = sum(
                engine.position_margin + engine.working_order_margin_reserve
                for engine in self._engines.values()
            )
            symbol_risk = self._current_symbol_open_risk()
            return {
                "initial_equity": self.initial_equity,
                "marked_equity": marked,
                "margin_used_and_reserved": margin,
                "margin_usage": margin / marked if marked > 0 else float("inf"),
                "available_funds": marked - margin,
                "portfolio_open_risk": sum(symbol_risk.values()),
                "symbol_open_risk": symbol_risk,
                "engines": {
                    symbol: {
                        "marked_equity": engine.marked_equity,
                        "position_margin": engine.position_margin,
                        "working_order_margin_reserve": engine.working_order_margin_reserve,
                        "margin_mark_price": engine.margin_mark_price,
                        "fill_count": len(engine.fills),
                        "closed_trade_count": len(engine.closed_trades),
                        "has_position": engine.position is not None,
                        "forced_flat_failed": engine.forced_flat_failed,
                    }
                    for symbol, engine in self._engines.items()
                },
            }

    def fills_since(self, symbol: str, index: int) -> tuple[Any, ...]:
        with self._lock:
            return tuple(self._engines[symbol].fills[index:])

    def instrument_spec(self, symbol: str) -> InstrumentSpec:
        with self._lock:
            return self._engines[symbol].instrument

    def closed_trades_since(self, symbol: str, index: int) -> tuple[Any, ...]:
        with self._lock:
            return tuple(self._engines[symbol].closed_trades[index:])

    def _process_watermark(
        self,
        watermark: datetime,
        symbols: tuple[str, ...],
        *,
        allow_entries: bool,
    ) -> list[OrderIntent]:
        bars = self._pending[watermark]
        intents: list[OrderIntent] = []
        for symbol in sorted(symbols):
            intents.extend(self._engines[symbol].on_bar(bars[symbol], allow_entries=False))
        if allow_entries and not self._stale_symbols:
            for symbol in self._entry_order(symbols):
                self._resize_triggered_entry(symbol, bars[symbol])
                intents.extend(self._engines[symbol].on_entry_bar(bars[symbol]))
        self._prune_approved_entries()
        del self._pending[watermark]
        self._first_received.pop(watermark, None)
        return intents

    def _entry_order(self, symbols: tuple[str, ...]) -> tuple[str, ...]:
        sortable: list[tuple[datetime, str, str]] = []
        far_future = datetime.max.replace(tzinfo=timezone.utc)
        for symbol in symbols:
            entries = [
                order
                for order in self._engines[symbol].orders
                if order.order_type is OrderType.ENTRY_STOP
                and order.status
                in {
                    OrderStatus.WORKING,
                    OrderStatus.SUBMITTED,
                    OrderStatus.PARTIALLY_FILLED,
                }
            ]
            if entries:
                first = min(entries, key=lambda order: (order.eligible_after, order.candidate_id))
                sortable.append((first.eligible_after, symbol, first.candidate_id))
            else:
                sortable.append((far_future, symbol, ""))
        return tuple(symbol for _, symbol, _ in sorted(sortable))

    def _resize_triggered_entry(self, symbol: str, bar: MinuteBar) -> None:
        engine = self._engines[symbol]
        preview = engine.preview_entry_trigger(bar)
        if preview is None:
            return
        approval = self._approved_entries.get(preview.order_id)
        if approval is None:
            return
        current_risk = self._latest_risk_snapshots.get(symbol, approval.risk_snapshot)
        trigger_risk_stopped = (
            current_risk.daily_locked
            or current_risk.portfolio_stopped
            or current_risk.symbol_stopped
        )
        risk_config = self.risk_adapter.config.risk if self.risk_adapter is not None else None
        if risk_config is not None:
            trigger_risk_stopped = trigger_risk_stopped or (
                current_risk.drawdown_pct >= risk_config.drawdown_stop_pct
                or current_risk.trade_date_return <= -risk_config.daily_hard_loss_pct
                or current_risk.portfolio_consecutive_losses
                >= risk_config.portfolio_stop_after
                or current_risk.symbol_consecutive_losses >= risk_config.symbol_stop_after
                or (
                    current_risk.cooldown_until is not None
                    and bar.bar_start < current_risk.cooldown_until
                )
            )

        account = self._account_snapshot()
        symbol_risk = dict(account.symbol_open_risk)
        symbol_risk[symbol] = max(
            0.0,
            symbol_risk.get(symbol, 0.0) - preview.reserved_open_risk,
        )
        adjusted_account = AccountSnapshot(
            marked_equity=account.marked_equity,
            margin_used_and_reserved=max(
                0.0,
                account.margin_used_and_reserved - engine.working_order_margin_reserve,
            ),
            portfolio_open_risk=max(
                0.0,
                account.portfolio_open_risk - preview.reserved_open_risk,
            ),
            symbol_open_risk=symbol_risk,
        )
        margin_rate = (
            engine.fee_spec.margin_rate_long
            if approval.direction > 0
            else engine.fee_spec.margin_rate_short
        )
        size = calculate_order_size(
            {
                "symbol": symbol,
                "trigger_price": max(
                    preview.fill_price,
                    bar.close,
                    bar.pre_settlement,
                ),
                "net_stop_loss_per_contract": preview.net_stop_loss_per_contract,
                "margin_rate": margin_rate,
            },
            adjusted_account,
            engine.instrument,
            current_risk,
            max_symbol_open_risk_pct=approval.max_symbol_open_risk_pct,
            max_portfolio_open_risk_pct=approval.max_portfolio_open_risk_pct,
            max_entry_margin_usage_pct=approval.max_entry_margin_usage_pct,
        )
        target_ahead = approval.direction * (
            approval.target_price - preview.fill_price
        ) > 0
        quantity = min(preview.remaining_quantity, size.quantity)
        if risk_config is not None and not trigger_risk_stopped:
            if current_risk.session_return <= -risk_config.session_soft_loss_pct:
                quantity = math.floor(quantity * 0.5)
            if current_risk.drawdown_pct >= risk_config.drawdown_half_pct:
                quantity = math.floor(quantity * 0.5)
        if trigger_risk_stopped or not target_ahead:
            quantity = 0
        engine.resize_entry_order(
            preview.order_id,
            quantity,
            net_stop_loss_per_contract=preview.net_stop_loss_per_contract,
        )
        if quantity == 0:
            self._approved_entries.pop(preview.order_id, None)
        self._risk_decisions.append(
            {
                "candidate_id": next(
                    order.candidate_id
                    for order in engine.orders
                    if order.order_id == preview.order_id
                ),
                "symbol": symbol,
                "phase": "trigger",
                "allowed": quantity > 0,
                "quantity": quantity,
                "reason": (
                    "ok"
                    if quantity > 0
                    else "trigger_risk_stop"
                    if trigger_risk_stopped
                    else "gap_beyond_target"
                    if not target_ahead
                    else size.reason
                ),
                "signal_quantity": preview.remaining_quantity,
                "trigger_fill_price": preview.fill_price,
                "net_stop_loss_per_contract": preview.net_stop_loss_per_contract,
                "risk_budget": size.risk_budget,
                "planned_open_risk": (
                    quantity * preview.net_stop_loss_per_contract
                ),
            }
        )

    def _prune_approved_entries(self) -> None:
        active_ids = {
            order.order_id
            for engine in self._engines.values()
            for order in engine.orders
            if order.order_type is OrderType.ENTRY_STOP
            and order.status
            in {
                OrderStatus.CREATED,
                OrderStatus.RISK_APPROVED,
                OrderStatus.WORKING,
                OrderStatus.SUBMITTED,
                OrderStatus.PARTIALLY_FILLED,
                OrderStatus.CANCEL_PENDING,
            }
        }
        for order_id in set(self._approved_entries).difference(active_ids):
            self._approved_entries.pop(order_id, None)

    def _account_snapshot(self) -> AccountSnapshot:
        snapshot = self.account_state()
        return AccountSnapshot(
            marked_equity=snapshot["marked_equity"],
            margin_used_and_reserved=snapshot["margin_used_and_reserved"],
            portfolio_open_risk=snapshot["portfolio_open_risk"],
            symbol_open_risk=snapshot["symbol_open_risk"],
        )

    def _current_symbol_open_risk(self) -> dict[str, float]:
        result: dict[str, float] = {}
        for symbol, engine in self._engines.items():
            risk = 0.0
            position = engine.position
            if position is not None:
                risk += (
                    position.initial_net_stop_loss_per_contract
                    * position.quantity
                )
            for order in engine.orders:
                if order.order_type is not OrderType.ENTRY_STOP or order.remaining_quantity <= 0:
                    continue
                if order.status not in {
                    OrderStatus.CREATED,
                    OrderStatus.RISK_APPROVED,
                    OrderStatus.WORKING,
                    OrderStatus.SUBMITTED,
                    OrderStatus.PARTIALLY_FILLED,
                    OrderStatus.CANCEL_PENDING,
                }:
                    continue
                stop = order.structural_stop if order.structural_stop is not None else order.price
                per_contract = order.net_stop_loss_per_contract
                if per_contract is None:
                    per_contract = (
                        abs(order.price - float(stop))
                        * engine.instrument.contract_size
                    )
                risk += per_contract * order.remaining_quantity
            result[symbol] = risk
        return result

    def _symbol_open_risk(
        self,
        snapshots: dict[str, EngineSnapshot],
    ) -> dict[str, float]:
        result: dict[str, float] = {}
        for symbol, snapshot in snapshots.items():
            risk = 0.0
            position = snapshot.position
            if position is not None:
                risk += (
                    position.initial_net_stop_loss_per_contract
                    * position.quantity
                )
            for order in snapshot.orders:
                if order.order_type is not OrderType.ENTRY_STOP or order.remaining_quantity <= 0:
                    continue
                stop = order.structural_stop if order.structural_stop is not None else order.price
                per_contract = order.net_stop_loss_per_contract
                if per_contract is None:
                    per_contract = (
                        abs(order.price - float(stop))
                        * self._engines[symbol].instrument.contract_size
                    )
                risk += per_contract * order.remaining_quantity
            result[symbol] = risk
        return result


__all__ = ["BrooksScalpPortfolioCoordinator", "PortfolioSnapshot"]
