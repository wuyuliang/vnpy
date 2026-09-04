"""Causal actual-contract replay for cycle_v1 plans and completed minute bars."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import date
import math
from typing import Any

import pandas as pd

from ..config import BrooksCycleConfig
from ..core.execution.order_state import OrderFill
from ..core.execution.planner import round_for_order
from ..core.risk.portfolio import PortfolioRiskSnapshot
from ..core.strategy import BrooksCycleV1Core, StrategyInput
from ..core.types import (
    CycleSnapshot,
    EventKey,
    MarketCycle,
    OrderPlan,
    RangeSubtype,
)
from ..instruments.metadata import BlockedMetadataError
from .data_loader import LoadedSymbol
from .engine_adapter import ActualContractLedger, ConservativeBarMatcher
from .execution_metadata import BACKTEST_GATEWAY
from .scanner import build_symbol_replay_frames
from .timeframes import TimeframeSet


CANDIDATE_COLUMNS = (
    "candidate_id", "symbol", "contract_code", "setup", "direction", "cycle",
    "signal_time", "active_time", "entry", "stop", "target", "trade_mode",
)
PLAN_COLUMNS = (
    *CANDIDATE_COLUMNS, "order_id", "quantity",
    "expires_at", "metadata_hash",
)
ORDER_COLUMNS = (
    "order_id", "candidate_id", "contract_code", "status", "reason",
)
FILL_COLUMNS = (
    "order_id", "candidate_id", "contract_code", "fill_kind", "fill_time",
    "quantity", "price", "reference_price", "reason",
)
TRADE_COLUMNS = (
    "candidate_id", "contract_code", "direction", "quantity", "entry_time",
    "exit_time", "entry_price", "exit_price", "gross_pnl", "fees", "slippage",
    "net_pnl", "symbol", "sector", "setup", "cycle", "net_r", "mfe_r",
    "mae_r", "holding_bars", "turnover",
)
DAILY_EQUITY_COLUMNS = (
    "date", "equity", "cash", "unrealized_pnl", "margin_used",
    "margin_utilization", "open_risk",
)
REJECTION_COLUMNS = (
    "root_symbol",
    "feature_asof",
    "reason_code",
    "candidate_id",
    "risk_budget",
    "loss_per_lot",
    "detail",
)
_SIGNAL_PRICE_LEVEL_COLUMNS = (
    "open",
    "high",
    "low",
    "close",
    "ema_fast",
    "ema_slow",
    "prior_high",
    "prior_low",
    "range_high",
    "range_low",
    "range_mid",
    "latest_confirmed_swing_high",
    "latest_confirmed_swing_low",
    "breakout_level",
)
_SIGNAL_DISTANCE_COLUMNS = ("atr", "bar_range", "body")
_SIGNAL_OBSTACLE_COLUMNS = (
    "confirmed_directional_swing_obstacles",
    "completed_higher_tf_obstacles",
)


@dataclass(frozen=True)
class SubmittedPlan:
    plan: OrderPlan
    metadata: Any
    features: dict[str, Any]
    root_symbol: str
    sector: str


@dataclass(frozen=True)
class ReplayArtifacts:
    candidates: pd.DataFrame
    plans: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    trades: pd.DataFrame
    daily_equity: pd.DataFrame
    rejections: pd.DataFrame


@dataclass
class _OpenTrade:
    submitted: SubmittedPlan
    stop: float
    target: float
    entry_bar_index: int
    initial_risk_cash: float
    maximum_favorable_price: float
    maximum_adverse_price: float


def execute_order_plans(
    *,
    submitted: tuple[SubmittedPlan, ...],
    minute_bars: pd.DataFrame,
    config: BrooksCycleConfig,
    initial_equity: float,
) -> ReplayArtifacts:
    """Execute predeclared plans; decisions remain frozen before later bar events."""
    bars = _validated_bars(minute_bars)
    engine = PlanExecutionEngine(config=config, initial_equity=initial_equity)
    for item in submitted:
        engine.submit(item)
    for bar_index, (_, bar) in enumerate(bars.iterrows()):
        engine.on_bar(bar, bar_index=bar_index)
    engine.finalize()
    return engine.artifacts()


class PlanExecutionEngine:
    """Stateful minute-event executor shared by static tests and strategy replay."""

    def __init__(self, *, config: BrooksCycleConfig, initial_equity: float) -> None:
        self.config = config
        self.initial_equity = initial_equity
        self.matcher = ConservativeBarMatcher()
        self.ledger = ActualContractLedger(initial_cash=initial_equity)
        self.pending: dict[str, SubmittedPlan] = {}
        self.open_trades: dict[str, _OpenTrade] = {}
        self.submitted: list[SubmittedPlan] = []
        self.order_rows: list[dict[str, Any]] = []
        self.fill_rows: list[dict[str, Any]] = []
        self.trade_rows: list[dict[str, Any]] = []
        self.daily_rows: dict[object, dict[str, Any]] = {}
        self.last_bars: dict[str, tuple[pd.Series, int]] = {}
        self.marks: dict[str, float] = {}
        self.day_start_equity: dict[object, float] = {}
        self.high_watermark = initial_equity

    def submit(self, item: SubmittedPlan) -> None:
        order_id = item.plan.order_id
        if order_id in self.pending or any(
            existing.plan.order_id == order_id for existing in self.submitted
        ):
            raise ValueError(f"duplicate replay order id: {order_id}")
        self.pending[order_id] = item
        self.submitted.append(item)
        self.order_rows.append(_order_row(item, "ACTIVE", ""))

    def has_root_exposure(self, root_symbol: str) -> bool:
        return any(
            item.root_symbol == root_symbol for item in self.pending.values()
        ) or any(
            active.submitted.root_symbol == root_symbol
            for active in self.open_trades.values()
        )

    def current_root_position(self, root_symbol: str) -> int:
        return sum(
            self.ledger.positions[contract].quantity
            for contract, active in self.open_trades.items()
            if active.submitted.root_symbol == root_symbol
        )

    def cancel_root_pending(self, root_symbol: str, *, reason: str) -> None:
        if not reason:
            raise ValueError("pending cancellation reason is required")
        for order_id, item in list(self.pending.items()):
            if item.root_symbol != root_symbol:
                continue
            self.order_rows.append(_order_row(item, "CANCELLED", reason))
            del self.pending[order_id]

    def force_close_root(
        self,
        root_symbol: str,
        *,
        bar: pd.Series,
        bar_index: int,
        reason: str,
    ) -> None:
        if not reason:
            raise ValueError("forced exit reason is required")
        event = _bar_event(bar)
        for contract, active in list(self.open_trades.items()):
            if active.submitted.root_symbol != root_symbol:
                continue
            if contract != str(bar["contract_code"]):
                raise ValueError("forced exit bar must be for the held actual contract")
            _update_excursions(active, bar)
            position = self.ledger.positions[contract]
            fill = _forced_exit_fill(
                position,
                bar,
                event,
                active.submitted.metadata,
            )
            trade = self.ledger.close(
                contract,
                fill,
                fee=_side_fee(active.submitted, position.quantity),
            )
            self.fill_rows.append(_fill_row(active.submitted, fill, "EXIT", reason))
            self.trade_rows.append(_trade_row(active, trade.to_dict(), bar_index))
            del self.open_trades[contract]
        self.marks[str(bar["contract_code"])] = float(bar["close"])
        self.daily_rows[bar["exchange_trade_date"]] = _equity_row(
            self.ledger,
            self.open_trades,
            self.marks,
            bar["exchange_trade_date"],
        )

    def portfolio_snapshot(self, *, trade_date: object) -> PortfolioRiskSnapshot:
        equity, open_risk, symbol_risk, sector_risk = self._marked_risk()
        self.day_start_equity.setdefault(trade_date, equity)
        self.high_watermark = max(self.high_watermark, equity)
        return PortfolioRiskSnapshot(
            equity=equity,
            symbol_open_risk=symbol_risk,
            sector_open_risk=sector_risk,
            total_open_risk=open_risk,
            margin_used_and_reserved=self.ledger.margin_reserved,
            day_start_adjusted_equity=self.day_start_equity[trade_date],
            adjusted_equity=equity,
            high_watermark_equity=self.high_watermark,
        )

    def _marked_risk(
        self,
    ) -> tuple[float, float, dict[str, float], dict[str, float]]:
        unrealized = 0.0
        symbol_risk: dict[str, float] = {}
        sector_risk: dict[str, float] = {}
        for contract, active in self.open_trades.items():
            position = self.ledger.positions[contract]
            mark = self.marks.get(contract, position.entry.price)
            unrealized += (
                position.direction
                * (mark - position.entry.price)
                * position.quantity
                * position.contract_multiplier
            )
            risk = position.quantity * (
                abs(mark - active.stop) * position.contract_multiplier
                + 0.5
                * float(active.submitted.metadata.stressed_round_trip_fee_cash)
            )
            root = active.submitted.root_symbol
            sector = active.submitted.sector
            symbol_risk[root] = symbol_risk.get(root, 0.0) + risk
            sector_risk[sector] = sector_risk.get(sector, 0.0) + risk
        return (
            self.ledger.cash + unrealized,
            sum(symbol_risk.values()),
            symbol_risk,
            sector_risk,
        )

    def on_bar(self, bar: pd.Series, *, bar_index: int) -> None:
        event = _bar_event(bar)
        contract = str(bar["contract_code"])
        self.last_bars[contract] = (bar.copy(), bar_index)
        self.marks[contract] = float(bar["close"])
        active = self.open_trades.get(contract)
        if active is not None:
            _update_excursions(active, bar)
            position = self.ledger.positions[contract]
            result = self.matcher.match_exit(
                direction=position.direction,
                quantity=position.quantity,
                stop=active.stop,
                target=active.target,
                bar=bar,
                event=event,
                metadata=active.submitted.metadata,
            )
            if result.fill is not None:
                trade = self.ledger.close(
                    contract,
                    result.fill,
                    fee=_side_fee(active.submitted, position.quantity),
                )
                self.fill_rows.append(
                    _fill_row(active.submitted, result.fill, "EXIT", result.reason)
                )
                self.trade_rows.append(
                    _trade_row(active, trade.to_dict(), bar_index)
                )
                del self.open_trades[contract]

        processed_oco: set[str] = set()
        for order_id, item in list(self.pending.items()):
            if order_id not in self.pending:
                continue
            plan = item.plan
            if event >= plan.expires_at:
                self.order_rows.append(_order_row(item, "EXPIRED", "ORDER_EXPIRED"))
                del self.pending[order_id]
                continue
            if contract != plan.candidate.contract_code or contract in self.ledger.positions:
                continue
            if plan.oco_group_id is not None:
                group_id = plan.oco_group_id
                if group_id in processed_oco:
                    continue
                processed_oco.add(group_id)
                peers = [
                    peer
                    for peer in self.pending.values()
                    if peer.plan.oco_group_id == group_id
                ]
                if len(peers) != 2:
                    continue
                matched = self.matcher.match_oco_entry(
                    tuple(peer.plan for peer in peers),
                    bar,
                    event,
                    item.metadata,
                    features=item.features,
                    config=self.config,
                )
                if matched.match.fill is None or matched.selected_order_id is None:
                    continue
                selected = next(
                    peer
                    for peer in peers
                    if peer.plan.order_id == matched.selected_order_id
                )
                self._open_entry(
                    selected,
                    matched.match,
                    contract=contract,
                    bar_index=bar_index,
                )
                del self.pending[selected.plan.order_id]
                for cancelled_id in matched.cancelled_order_ids:
                    cancelled = self.pending.pop(cancelled_id)
                    self.order_rows.append(
                        _order_row(
                            cancelled,
                            "CANCELLED",
                            f"OCO_PEER_FILLED:{selected.plan.order_id}",
                        )
                    )
                continue
            result = self.matcher.match_entry(
                plan,
                bar,
                event,
                item.metadata,
                features=item.features,
                config=self.config,
            )
            if result.fill is None:
                if result.reason in {
                    "BLOCKED_EXECUTION_METADATA",
                    "METADATA_CHANGED_REPLAN_REQUIRED",
                } or result.reason.startswith("GAP_REPRICE_"):
                    self.order_rows.append(_order_row(item, "CANCELLED", result.reason))
                    del self.pending[order_id]
                continue
            self._open_entry(item, result, contract=contract, bar_index=bar_index)
            del self.pending[order_id]

        self.daily_rows[bar["exchange_trade_date"]] = _equity_row(
            self.ledger,
            self.open_trades,
            self.marks,
            bar["exchange_trade_date"],
        )

    def _open_entry(
        self,
        item: SubmittedPlan,
        result: Any,
        *,
        contract: str,
        bar_index: int,
    ) -> None:
        if result.fill is None:
            raise ValueError("entry result requires a fill")
        plan = item.plan
        geometry = result.geometry or plan.geometry
        margin_rate = float(
            item.metadata.daily.margin_rate_long
            if plan.candidate.direction == 1
            else item.metadata.daily.margin_rate_short
        )
        self.ledger.open(
            plan,
            result.fill,
            contract_multiplier=float(item.metadata.contract_size),
            margin_rate=margin_rate,
            fee=_side_fee(item, result.fill.quantity),
        )
        risk_cash = result.fill.quantity * (
            abs(result.fill.price - geometry.stop)
            * float(item.metadata.contract_size)
            + float(item.metadata.stressed_round_trip_fee_cash)
        )
        self.open_trades[contract] = _OpenTrade(
            submitted=item,
            stop=geometry.stop,
            target=geometry.target,
            entry_bar_index=bar_index,
            initial_risk_cash=risk_cash,
            maximum_favorable_price=result.fill.price,
            maximum_adverse_price=result.fill.price,
        )
        self.fill_rows.append(_fill_row(item, result.fill, "ENTRY", result.reason))
        self.order_rows.append(_order_row(item, "FILLED", result.reason))

    def finalize(self) -> None:
        for contract, active in list(self.open_trades.items()):
            if contract not in self.last_bars:
                continue
            bar, bar_index = self.last_bars[contract]
            event = _bar_event(bar)
            position = self.ledger.positions[contract]
            fill = _forced_exit_fill(position, bar, event, active.submitted.metadata)
            trade = self.ledger.close(
                contract,
                fill,
                fee=_side_fee(active.submitted, position.quantity),
            )
            self.fill_rows.append(
                _fill_row(active.submitted, fill, "EXIT", "INTERVAL_END")
            )
            self.trade_rows.append(_trade_row(active, trade.to_dict(), bar_index))
            del self.open_trades[contract]
            self.daily_rows[bar["exchange_trade_date"]] = _equity_row(
                self.ledger,
                self.open_trades,
                self.marks,
                bar["exchange_trade_date"],
            )

    def artifacts(self) -> ReplayArtifacts:
        return ReplayArtifacts(
            candidates=pd.DataFrame(
                [_candidate_row(item) for item in self.submitted],
                columns=CANDIDATE_COLUMNS,
            ),
            plans=pd.DataFrame(
                [_plan_row(item) for item in self.submitted], columns=PLAN_COLUMNS
            ),
            orders=pd.DataFrame(self.order_rows, columns=ORDER_COLUMNS),
            fills=pd.DataFrame(self.fill_rows, columns=FILL_COLUMNS),
            trades=pd.DataFrame(self.trade_rows, columns=TRADE_COLUMNS),
            daily_equity=pd.DataFrame(
                self.daily_rows.values(), columns=DAILY_EQUITY_COLUMNS
            ).sort_values(
                "date", kind="stable"
            ).reset_index(drop=True),
            rejections=pd.DataFrame(columns=REJECTION_COLUMNS),
        )


def replay_cycle_strategy(
    symbols: tuple[LoadedSymbol, ...],
    *,
    metadata_store: Any,
    universe_daily: pd.DataFrame,
    config: BrooksCycleConfig,
    timeframes: TimeframeSet,
    start: date,
    end: date,
    initial_equity: float,
    replay_frames: Mapping[str, Any] | None = None,
) -> ReplayArtifacts:
    """Drive the shared decision core after each completed short-timeframe bar."""
    if end < start or not symbols:
        raise ValueError("cycle replay requires symbols and an ordered date range")
    engine = PlanExecutionEngine(config=config, initial_equity=initial_equity)
    core = BrooksCycleV1Core(config)
    decision_frames: dict[str, pd.DataFrame] = {}
    frame_sets: dict[str, Any] = {}
    minute_parts: list[pd.DataFrame] = []
    for item in symbols:
        frames = (
            replay_frames.get(item.root_symbol)
            if replay_frames is not None
            else None
        )
        if frames is None:
            frames = build_symbol_replay_frames(
                item,
                config=config,
                timeframes=timeframes,
            )
        frame_sets[item.root_symbol] = frames
        decision_frames[item.root_symbol] = _build_decision_frame(
            item,
            frames.short,
            frames.medium,
            frames.long,
            universe_daily,
        )
        minutes = item.minute_bars.loc[
            item.minute_bars["exchange_trade_date"].map(
                lambda value: start <= value <= end
            )
        ].copy().sort_values("bar_end", kind="stable").reset_index(drop=True)
        minutes["_root_symbol"] = item.root_symbol
        minute_parts.append(minutes)
    all_minutes = pd.concat(minute_parts, ignore_index=True).sort_values(
        ["bar_end", "_root_symbol"], kind="stable"
    ).reset_index(drop=True)
    by_root = {item.root_symbol: item for item in symbols}
    decision_lookup = {
        root: {
            (pd.Timestamp(row.feature_asof), int(row.feature_sequence)): row
            for row in frame.itertuples(index=False)
            if start <= row.exchange_trade_date <= end
        }
        for root, frame in decision_frames.items()
    }
    candidate_rows: list[dict[str, Any]] = []
    rejection_rows: list[dict[str, Any]] = []
    active_contracts: dict[str, str] = {}

    for global_index, (_, bar) in enumerate(all_minutes.iterrows()):
        root = str(bar["_root_symbol"])
        contract = str(bar["contract_code"])
        _handle_contract_transition(
            engine,
            root_symbol=root,
            contract_code=contract,
            active_contracts=active_contracts,
        )
        engine.on_bar(bar, bar_index=global_index)
        key = (pd.Timestamp(bar["bar_end"]), int(bar["feature_sequence"]))
        decision_row = decision_lookup[root].get(key)
        if decision_row is None or not bool(decision_row.eligible):
            continue
        try:
            small_cycle = _cycle_snapshot(decision_row)
            medium_cycle = _cycle_snapshot(decision_row, prefix="medium_")
            large_cycle = _cycle_snapshot(decision_row, prefix="long_")
        except (TypeError, ValueError):
            rejection_rows.append(
                _rejection(root, key[0], "INCOMPLETE_TIMEFRAME_CONTEXT")
            )
            continue
        feature_event = small_cycle.feature_event
        active_event = EventKey(
            (
                pd.Timestamp(bar["bar_end"]) + pd.Timedelta(minutes=1)
            ).to_pydatetime(),
            int(bar["feature_sequence"]) + 1,
        )
        expiry_timestamp = pd.Timestamp(feature_event.timestamp) + pd.Timedelta(
            minutes=config.setup.setup_expiry_bars * timeframes.short.minutes
        )
        if expiry_timestamp <= pd.Timestamp(active_event.timestamp):
            rejection_rows.append(
                _rejection(root, key[0], "EXPIRY_PRECEDES_NEXT_EVENT")
            )
            continue
        item = by_root[root]
        try:
            metadata = metadata_store.execution_snapshot(
                root_symbol=root,
                exchange=item.exchange,
                contract_code=str(bar["contract_code"]),
                exchange_trade_date=bar["exchange_trade_date"],
                gateway=BACKTEST_GATEWAY,
                order_types=("STOP",),
                decision_asof=feature_event.timestamp,
                order_event=active_event.timestamp,
            )
        except (BlockedMetadataError, LookupError, ValueError) as exc:
            rejection_rows.append(
                _rejection(
                    root,
                    key[0],
                    getattr(exc, "reason_code", "BLOCKED_METADATA"),
                )
            )
            continue
        frames = frame_sets[root]
        history = frames.short.loc[
            frames.short["contract_code"].eq(str(bar["contract_code"]))
            & (frames.short["feature_asof"] < key[0])
        ].tail(config.features.percentile_lookback)
        safety_blocks = (
            ("ROOT_EXPOSURE_ACTIVE",) if engine.has_root_exposure(root) else ()
        )
        execution_features = _raw_execution_features(dict(decision_row._asdict()))
        strategy_input = StrategyInput(
            vt_symbol=item.vt_symbol,
            root_symbol=root,
            sector=_sector(root),
            contract_code=str(bar["contract_code"]),
            features=execution_features,
            causal_history=history,
            medium_cycle=medium_cycle,
            large_cycle=large_cycle,
            metadata=metadata,
            portfolio=engine.portfolio_snapshot(
                trade_date=bar["exchange_trade_date"]
            ),
            active_event=active_event,
            expiry_event=EventKey(expiry_timestamp.to_pydatetime(), 0),
            small_cycle=small_cycle,
            current_symbol_position=engine.current_root_position(root),
            safety_blocks=safety_blocks,
        )
        decision = core.on_snapshot(strategy_input)
        for setup_decision in decision.setup_decisions:
            if setup_decision.accepted and setup_decision.candidate is not None:
                candidate_rows.append(
                    _candidate_from_core(root, setup_decision.candidate)
                )
        rejection_rows.extend(_decision_rejection_rows(root, key[0], decision))
        for plan in decision.order_plans:
            engine.submit(
                SubmittedPlan(
                    plan=plan,
                    metadata=metadata,
                    features=strategy_input.features,
                    root_symbol=root,
                    sector=strategy_input.sector,
                )
            )

    engine.finalize()
    executed = engine.artifacts()
    return ReplayArtifacts(
        candidates=pd.DataFrame(
            candidate_rows,
            columns=CANDIDATE_COLUMNS,
        ),
        plans=executed.plans,
        orders=executed.orders,
        fills=executed.fills,
        trades=executed.trades,
        daily_equity=executed.daily_equity,
        rejections=pd.DataFrame(
            rejection_rows,
            columns=REJECTION_COLUMNS,
        ),
    )


def _handle_contract_transition(
    engine: PlanExecutionEngine,
    *,
    root_symbol: str,
    contract_code: str,
    active_contracts: dict[str, str],
) -> None:
    """React only after the new mapping is observable; never exit retrospectively."""
    previous = active_contracts.get(root_symbol)
    if previous is None:
        active_contracts[root_symbol] = contract_code
        return
    if previous == contract_code:
        return
    engine.cancel_root_pending(root_symbol, reason="ROLL_MAPPING_CHANGED")
    if engine.current_root_position(root_symbol) != 0:
        raise BlockedMetadataError(
            "BLOCKED_ROLL_EXECUTION_BAR: active-contract data cannot price the "
            f"old {previous} position after {contract_code} became observable"
        )
    active_contracts[root_symbol] = contract_code


def _raw_execution_features(features: Mapping[str, Any]) -> dict[str, Any]:
    """Map adjusted signal price coordinates to the active actual contract."""
    result = dict(features)
    scale = float(result.get("adjustment_scale", 1.0))
    offset = float(result.get("adjustment_offset", 0.0))
    if not math.isfinite(scale) or scale <= 0 or not math.isfinite(offset):
        raise ValueError("signal adjustment scale/offset is invalid")

    def raw_level(value: object) -> float:
        return (float(value) - offset) / scale

    for column in _SIGNAL_PRICE_LEVEL_COLUMNS:
        value = result.get(column)
        if value is not None and not pd.isna(value):
            result[column] = raw_level(value)
    for column in _SIGNAL_DISTANCE_COLUMNS:
        value = result.get(column)
        if value is not None and not pd.isna(value):
            result[column] = float(value) / scale
    for column in _SIGNAL_OBSTACLE_COLUMNS:
        groups = result.get(column)
        if not isinstance(groups, Mapping):
            continue
        result[column] = {
            direction: tuple(raw_level(value) for value in values)
            for direction, values in groups.items()
        }
    return result


def empty_replay_artifacts() -> ReplayArtifacts:
    return ReplayArtifacts(
        candidates=pd.DataFrame(columns=CANDIDATE_COLUMNS),
        plans=pd.DataFrame(columns=PLAN_COLUMNS),
        orders=pd.DataFrame(columns=ORDER_COLUMNS),
        fills=pd.DataFrame(columns=FILL_COLUMNS),
        trades=pd.DataFrame(columns=TRADE_COLUMNS),
        daily_equity=pd.DataFrame(columns=DAILY_EQUITY_COLUMNS),
        rejections=pd.DataFrame(columns=REJECTION_COLUMNS),
    )


def build_metadata_coverage_requests(
    symbols: tuple[LoadedSymbol, ...],
    *,
    start: date,
    end: date,
) -> list[dict[str, object]]:
    """Create one execution-mechanics audit request per actual contract day."""
    requests: list[dict[str, object]] = []
    for item in symbols:
        selected = item.minute_bars.loc[
            item.minute_bars["exchange_trade_date"].map(
                lambda value: start <= value <= end
            )
        ].sort_values("bar_end", kind="stable")
        for (contract, trade_date), group in selected.groupby(
            ["contract_code", "exchange_trade_date"], sort=True
        ):
            timestamps = pd.to_datetime(group["bar_end"])
            decision_asof = timestamps.iloc[0].to_pydatetime()
            order_event = (
                timestamps.iloc[1].to_pydatetime()
                if len(timestamps) > 1
                else (timestamps.iloc[0] + pd.Timedelta(microseconds=1)).to_pydatetime()
            )
            requests.append(
                {
                    "root_symbol": item.root_symbol,
                    "exchange": item.exchange,
                    "contract_code": str(contract),
                    "exchange_trade_date": trade_date,
                    "gateway": BACKTEST_GATEWAY,
                    "order_types": ("STOP",),
                    "decision_asof": decision_asof,
                    "order_event": order_event,
                }
            )
    return requests


def _validated_bars(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "bar_end",
        "feature_sequence",
        "open",
        "high",
        "low",
        "close",
        "contract_code",
        "exchange_trade_date",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(f"minute replay bars are missing: {','.join(missing)}")
    result = frame.copy().sort_values("bar_end", kind="stable").reset_index(drop=True)
    timestamps = pd.to_datetime(result["bar_end"])
    if timestamps.dt.tz is None:
        raise ValueError("minute replay bars require timezone-aware bar_end")
    if result.duplicated(["contract_code", "bar_end"]).any():
        raise ValueError("contract_code and bar_end must be unique")
    return result


def _build_decision_frame(
    item: LoadedSymbol,
    short: pd.DataFrame,
    medium: pd.DataFrame,
    long: pd.DataFrame,
    universe_daily: pd.DataFrame,
) -> pd.DataFrame:
    result = short.copy().sort_values("feature_asof", kind="stable")
    for prefix, higher in (("medium_", medium), ("long_", long)):
        columns = [
            "contract_code",
            "feature_asof",
            "feature_sequence",
            "cycle",
            "direction",
            "strength",
            "confidence",
            "bull_pressure",
            "bear_pressure",
            "range_subtype",
            "evidence",
        ]
        right = higher.loc[:, columns].rename(
            columns={column: f"{prefix}{column}" for column in columns[1:]}
        )
        result = pd.merge_asof(
            result.sort_values("feature_asof", kind="stable"),
            right.sort_values(f"{prefix}feature_asof", kind="stable"),
            left_on="feature_asof",
            right_on=f"{prefix}feature_asof",
            by="contract_code",
            direction="backward",
            allow_exact_matches=True,
        )
    result["root_symbol"] = item.root_symbol
    eligibility_columns = [
        "root_symbol",
        "exchange_trade_date",
        "contract_code",
        "ema_asof_trade_date",
        "ema_asof_bar_end",
        "ema1",
        "ema3",
        "ema5",
        "eligible",
        "reason_code",
    ]
    available = [column for column in eligibility_columns if column in universe_daily]
    return result.merge(
        universe_daily.loc[:, available],
        on=["root_symbol", "exchange_trade_date", "contract_code"],
        how="left",
        validate="many_to_one",
    ).sort_values("feature_asof", kind="stable").reset_index(drop=True)


def _cycle_snapshot(row: Any, *, prefix: str = "") -> CycleSnapshot:
    event = EventKey(
        pd.Timestamp(getattr(row, f"{prefix}feature_asof")).to_pydatetime(),
        int(getattr(row, f"{prefix}feature_sequence")),
    )
    subtype_value = getattr(row, f"{prefix}range_subtype")
    subtype = None
    if subtype_value is not None and not pd.isna(subtype_value):
        subtype = RangeSubtype(str(subtype_value))
    evidence = getattr(row, f"{prefix}evidence")
    return CycleSnapshot(
        cycle=MarketCycle(str(getattr(row, f"{prefix}cycle"))),
        direction=int(getattr(row, f"{prefix}direction")),
        strength=float(getattr(row, f"{prefix}strength")),
        confidence=float(getattr(row, f"{prefix}confidence")),
        bull_pressure=float(getattr(row, f"{prefix}bull_pressure")),
        bear_pressure=float(getattr(row, f"{prefix}bear_pressure")),
        range_subtype=subtype,
        feature_event=event,
        evidence=evidence if isinstance(evidence, dict) else {},
    )


def _sector(root_symbol: str) -> str:
    return {"CU": "BASE_METAL", "RB": "BLACK"}.get(root_symbol, "OTHER")


def _rejection(
    root_symbol: str,
    feature_asof: object,
    reason: str,
    *,
    candidate_id: str = "",
    risk_budget: float | None = None,
    loss_per_lot: float | None = None,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "root_symbol": root_symbol,
        "feature_asof": feature_asof,
        "reason_code": reason,
        "candidate_id": candidate_id,
        "risk_budget": risk_budget,
        "loss_per_lot": loss_per_lot,
        "detail": detail,
    }


def _decision_rejection_rows(
    root_symbol: str,
    feature_asof: object,
    decision: Any,
) -> list[dict[str, Any]]:
    audits = {
        audit.rejection_index: audit for audit in decision.rejection_audits
    }
    if len(audits) != len(decision.rejection_audits):
        raise ValueError("rejection audit indexes must be unique")
    rows: list[dict[str, Any]] = []
    for index, reason in enumerate(decision.rejections):
        audit = audits.pop(index, None)
        if audit is None:
            rows.append(_rejection(root_symbol, feature_asof, reason))
            continue
        if audit.reason_code != reason:
            raise ValueError("rejection audit reason does not match its indexed row")
        rows.append(
            _rejection(
                root_symbol,
                feature_asof,
                reason,
                candidate_id=audit.candidate_id,
                risk_budget=audit.risk_budget,
                loss_per_lot=audit.loss_per_lot,
                detail=audit.detail,
            )
        )
    if audits:
        raise ValueError("rejection audit index is outside the rejection list")
    return rows


def _candidate_from_core(root_symbol: str, candidate: Any) -> dict[str, Any]:
    return {
        "candidate_id": candidate.candidate_id,
        "symbol": root_symbol,
        "contract_code": candidate.contract_code,
        "setup": candidate.setup_type.value,
        "direction": candidate.direction,
        "cycle": candidate.context_cycle.value,
        "signal_time": candidate.signal_event.timestamp,
        "active_time": candidate.active_event.timestamp,
        "entry": candidate.trigger_price,
        "stop": candidate.initial_stop,
        "target": candidate.target_price,
        "trade_mode": candidate.trade_mode.value,
    }


def _bar_event(bar: pd.Series) -> EventKey:
    return EventKey(
        pd.Timestamp(bar["bar_end"]).to_pydatetime(),
        int(bar["feature_sequence"]),
    )


def _side_fee(item: SubmittedPlan, quantity: int) -> float:
    return 0.5 * float(item.metadata.stressed_round_trip_fee_cash) * quantity


def _forced_exit_fill(position: Any, bar: pd.Series, event: EventKey, metadata: Any) -> OrderFill:
    direction = -position.direction
    exit_ticks = max(
        0.0,
        float(metadata.stressed_round_trip_slippage_ticks)
        - float(metadata.stressed_entry_slippage_ticks),
    )
    reference = float(bar["close"])
    price = round_for_order(
        reference + direction * exit_ticks * float(metadata.price_tick),
        "SELL" if position.direction == 1 else "BUY",
        "stop",
        "protective_stop",
        float(metadata.price_tick),
    )
    return OrderFill(event, position.quantity, price, reference)


def _update_excursions(active: _OpenTrade, bar: pd.Series) -> None:
    direction = active.submitted.plan.candidate.direction
    if direction == 1:
        active.maximum_favorable_price = max(
            active.maximum_favorable_price, float(bar["high"])
        )
        active.maximum_adverse_price = min(
            active.maximum_adverse_price, float(bar["low"])
        )
    else:
        active.maximum_favorable_price = min(
            active.maximum_favorable_price, float(bar["low"])
        )
        active.maximum_adverse_price = max(
            active.maximum_adverse_price, float(bar["high"])
        )


def _equity_row(
    ledger: ActualContractLedger,
    open_trades: dict[str, _OpenTrade],
    marks: dict[str, float],
    trade_date: object,
) -> dict[str, Any]:
    unrealized = 0.0
    open_risk = 0.0
    for contract, active in open_trades.items():
        position = ledger.positions[contract]
        mark = marks.get(contract, position.entry.price)
        unrealized += (
            position.direction
            * (mark - position.entry.price)
            * position.quantity
            * position.contract_multiplier
        )
        open_risk += (
            abs(mark - active.stop)
            * position.quantity
            * position.contract_multiplier
        )
    equity = ledger.cash + unrealized
    return {
        "date": trade_date,
        "equity": equity,
        "cash": ledger.cash,
        "unrealized_pnl": unrealized,
        "margin_used": ledger.margin_reserved,
        "margin_utilization": ledger.margin_reserved / equity if equity > 0 else 0.0,
        "open_risk": open_risk / equity if equity > 0 else 0.0,
    }


def _candidate_row(item: SubmittedPlan) -> dict[str, Any]:
    return _candidate_from_core(item.root_symbol, item.plan.candidate)


def _plan_row(item: SubmittedPlan) -> dict[str, Any]:
    plan = item.plan
    return {
        **_candidate_row(item),
        "order_id": plan.order_id,
        "entry": plan.geometry.entry,
        "stop": plan.geometry.stop,
        "target": plan.geometry.target,
        "quantity": plan.quantity,
        "expires_at": plan.expires_at.timestamp,
        "metadata_hash": plan.metadata_hash,
    }


def _order_row(item: SubmittedPlan, status: str, reason: str) -> dict[str, Any]:
    return {
        "order_id": item.plan.order_id,
        "candidate_id": item.plan.candidate.candidate_id,
        "contract_code": item.plan.candidate.contract_code,
        "status": status,
        "reason": reason,
    }


def _fill_row(
    item: SubmittedPlan,
    fill: OrderFill,
    fill_kind: str,
    reason: str,
) -> dict[str, Any]:
    return {
        "order_id": item.plan.order_id,
        "candidate_id": item.plan.candidate.candidate_id,
        "contract_code": item.plan.candidate.contract_code,
        "fill_kind": fill_kind,
        "fill_time": fill.event.timestamp,
        "quantity": fill.quantity,
        "price": fill.price,
        "reference_price": fill.reference_price,
        "reason": reason,
    }


def _trade_row(
    active: _OpenTrade,
    trade: dict[str, Any],
    exit_bar_index: int,
) -> dict[str, Any]:
    plan = active.submitted.plan
    direction = plan.candidate.direction
    entry = float(trade["entry_price"])
    risk_price = abs(entry - active.stop)
    mfe_price = direction * (active.maximum_favorable_price - entry)
    mae_price = direction * (active.maximum_adverse_price - entry)
    return {
        **trade,
        "symbol": active.submitted.root_symbol,
        "sector": active.submitted.sector,
        "setup": plan.candidate.setup_type.value,
        "cycle": plan.candidate.context_cycle.value,
        "net_r": float(trade["net_pnl"]) / active.initial_risk_cash,
        "mfe_r": mfe_price / risk_price if risk_price > 0 else 0.0,
        "mae_r": mae_price / risk_price if risk_price > 0 else 0.0,
        "holding_bars": max(1, exit_bar_index - active.entry_bar_index),
        "turnover": (
            float(trade["entry_price"]) + float(trade["exit_price"])
        )
        * int(trade["quantity"])
        * float(active.submitted.metadata.contract_size),
    }


__all__ = [
    "PlanExecutionEngine",
    "ReplayArtifacts",
    "SubmittedPlan",
    "build_metadata_coverage_requests",
    "empty_replay_artifacts",
    "execute_order_plans",
    "replay_cycle_strategy",
]
