"""Causal actual-contract replay for the multi-timeframe trend strategy."""
from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date
import math
from typing import Any

import numpy as np
import pandas as pd

from cta.config.futures_sector_map import sector_for_root
from cta.config.multi_timeframe_trend_config import (
    MultiTimeframeTrendConfig,
    is_entry_window_blocked,
    minutes_of_day,
)
from cta.strategy.brooks.cycle_v1.backtest.execution_metadata import BACKTEST_GATEWAY
from cta.strategy.brooks.cycle_v1.instruments.metadata import BlockedMetadataError
from cta.strategy.multi_timeframe_trend_rules import (
    advance_trailing_stop,
    size_for_risk,
    two_r_target,
)
from .diagnostics import GateFailOpenDiagnostics, observe_gate


PLAN_COLUMNS = (
    "candidate_id", "order_id", "symbol", "contract_code", "setup",
    "direction", "signal_time", "active_time", "expires_at", "entry",
    "stop", "target", "quantity", "risk_budget", "loss_per_lot",
    "metadata_hash",
)
ORDER_COLUMNS = (
    "order_id", "candidate_id", "contract_code", "status", "reason", "event_time",
)
FILL_COLUMNS = (
    "order_id", "candidate_id", "contract_code", "fill_kind", "fill_time",
    "quantity", "price", "reference_price", "reason",
)
EXIT_LEG_COLUMNS = (
    "candidate_id", "contract_code", "symbol", "exit_sequence",
    "is_final_exit", "reason", "quantity", "entry_time", "exit_time",
    "entry_price", "exit_price",
    "reference_price", "gross_pnl", "open_fee", "close_fee", "fees",
    "slippage", "net_pnl", "turnover", "close_type", "entry_fee_rate",
    "exit_fee_rate", "entry_fixed_fee", "exit_fixed_fee",
    "fee_stress_multiplier", "entry_fee_schedule_id", "exit_fee_schedule_id",
    "entry_metadata_hash", "exit_metadata_hash", "contract_multiplier",
    "entry_fee_source", "exit_fee_source", "entry_fee_effective_from",
    "exit_fee_effective_from", "entry_fee_known_at", "exit_fee_known_at",
)
FORWARD_RETURN_HORIZONS = (5, 10, 20, 30)
TRADE_RETURN_COLUMNS = (
    "total_return",
    *(f"return_{minutes}min" for minutes in FORWARD_RETURN_HORIZONS),
)
MARKET_LIQUIDITY_WINDOWS = (5, 10, 20)
MARKET_LIQUIDITY_COLUMNS = tuple(
    column
    for days in MARKET_LIQUIDITY_WINDOWS
    for column in (
        f"prior_{days}d_avg_market_volume",
        f"prior_{days}d_avg_market_turnover",
    )
)
TRADE_COLUMNS = (
    "candidate_id", "contract_code", "direction", "quantity", "entry_time",
    "exit_time", "entry_price", "exit_price", "gross_pnl", *TRADE_RETURN_COLUMNS,
    *MARKET_LIQUIDITY_COLUMNS,
    "fees", "slippage", "net_pnl", "symbol", "sector", "setup", "cycle",
    "is_first_trade_in_trend_segment", "trigger_to_prior_5d_high_ratio",
    "net_r", "mfe_r", "mae_r", "holding_bars", "turnover", "exit_reason",
    "final_target", "target_exit_enabled",
    "had_overnight_reduction", "overnight_reduced_quantity",
    "base_quantity", "symbol_quantity_scale", "portfolio_quantity_scale",
    "quantity_scale", "position_scaling_reason",
    "symbol_recovery_deficit_at_entry",
    "portfolio_recovery_deficit_at_entry",
    "open_fee", "close_fee",
    "close_type", "entry_fee_rate", "exit_fee_rate", "entry_fixed_fee",
    "exit_fixed_fee", "fee_stress_multiplier", "entry_fee_schedule_id",
    "exit_fee_schedule_id", "entry_metadata_hash", "exit_metadata_hash",
    "contract_multiplier", "entry_fee_source", "exit_fee_source",
    "entry_fee_effective_from", "exit_fee_effective_from",
    "entry_fee_known_at", "exit_fee_known_at",
)
DAILY_EQUITY_COLUMNS = (
    "date", "equity", "cash", "unrealized_pnl", "margin_used",
    "margin_utilization", "open_risk", "peak_margin_utilization",
    "peak_overnight_margin_utilization", "peak_symbol_margin_utilization",
    "max_concurrent_positions",
)
REJECTION_COLUMNS = (
    "root_symbol", "feature_asof", "reason_code", "candidate_id",
    "risk_budget", "loss_per_lot", "detail",
)
SCALING_EVENT_COLUMNS = (
    "event_time", "sequence", "scope", "symbol", "event_type",
    "candidate_id", "net_pnl", "realized_cash", "high_water",
    "drawdown_fraction", "position_scale", "recovery_deficit",
)


def _observation_bars(
    primary: pd.DataFrame,
    supplemental: pd.DataFrame,
) -> pd.DataFrame:
    """Combine return-observation bars with primary rows taking precedence."""
    columns = ["bar_end", "contract_code", "close"]
    frames: list[pd.DataFrame] = []
    for priority, frame in enumerate((primary, supplemental)):
        if frame.empty:
            continue
        selected = frame.loc[:, columns].copy()
        selected["_source_priority"] = priority
        frames.append(selected)
    if not frames:
        return pd.DataFrame(columns=columns)

    result = pd.concat(frames, ignore_index=True).sort_values(
        ["contract_code", "bar_end", "_source_priority"],
        kind="stable",
    )
    return (
        result.drop_duplicates(["contract_code", "bar_end"], keep="first")
        .sort_values(["contract_code", "bar_end"], kind="stable")
        .drop(columns="_source_priority")
        .reset_index(drop=True)
    )


def _with_trade_returns(
    trades: pd.DataFrame,
    *,
    observation_bars_by_symbol: dict[str, pd.DataFrame],
    default_symbol: str | None = None,
) -> pd.DataFrame:
    """Return a copy of trades enriched with direction-adjusted returns."""
    result = trades.copy()
    for column in TRADE_RETURN_COLUMNS:
        result[column] = math.nan
    if result.empty:
        return result.reindex(columns=TRADE_COLUMNS)

    direction = pd.to_numeric(result["direction"], errors="coerce")
    entry_price = pd.to_numeric(result["entry_price"], errors="coerce")
    exit_price = pd.to_numeric(result["exit_price"], errors="coerce")
    result["total_return"] = (
        100.0 * direction * (exit_price - entry_price) / entry_price
    )

    lookup: dict[tuple[str, str], pd.DataFrame] = {}
    for symbol, bars in observation_bars_by_symbol.items():
        if bars.empty:
            continue
        for contract, contract_bars in bars.groupby("contract_code", sort=False):
            lookup[(str(symbol).strip(), str(contract).strip())] = (
                contract_bars.sort_values("bar_end", kind="stable").reset_index(
                    drop=True
                )
            )

    for position, (_, trade) in enumerate(result.iterrows()):
        symbol_value = trade.get("symbol", "")
        symbol = (
            ""
            if symbol_value is None or pd.isna(symbol_value)
            else str(symbol_value).strip()
        )
        if not symbol:
            symbol = str(default_symbol).strip() if default_symbol is not None else ""
        if not symbol:
            continue

        contract_value = trade["contract_code"]
        if contract_value is None or pd.isna(contract_value):
            continue
        contract_bars = lookup.get((symbol, str(contract_value).strip()))
        if contract_bars is None:
            continue

        entry_time = pd.Timestamp(trade["entry_time"])
        start = int(contract_bars["bar_end"].searchsorted(entry_time, side="right"))
        for horizon in FORWARD_RETURN_HORIZONS:
            offset = start + horizon - 1
            if offset >= len(contract_bars):
                continue
            observed_close = contract_bars.iloc[offset]["close"]
            if pd.isna(observed_close):
                continue
            observed_price = float(observed_close)
            if not math.isfinite(observed_price) or observed_price <= 0:
                continue
            column = f"return_{horizon}min"
            result.iat[position, result.columns.get_loc(column)] = (
                100.0
                * float(direction.iloc[position])
                * (observed_price - float(entry_price.iloc[position]))
                / float(entry_price.iloc[position])
            )
    return result.reindex(columns=TRADE_COLUMNS)


@dataclass(frozen=True)
class ReplayArtifacts:
    candidates: pd.DataFrame
    plans: pd.DataFrame
    orders: pd.DataFrame
    fills: pd.DataFrame
    exit_legs: pd.DataFrame
    trades: pd.DataFrame
    daily_equity: pd.DataFrame
    rejections: pd.DataFrame
    position_scaling_events: pd.DataFrame


@dataclass(frozen=True)
class PortfolioReplayInput:
    root_symbol: str
    exchange: str
    minute_bars: pd.DataFrame
    five_minute_context: pd.DataFrame
    candidates: pd.DataFrame
    sessions: tuple[Any, ...]
    roll_execution_bars: pd.DataFrame | None = None
    daily_context: pd.DataFrame | None = None


class _PlanRejected(ValueError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


@dataclass
class _PendingOrder:
    candidate: dict[str, Any]
    metadata: Any
    order_id: str
    quantity: int
    risk_budget: float
    loss_per_lot: float


@dataclass
class _Position:
    pending: _PendingOrder
    quantity: int
    initial_quantity: int
    entry_time: pd.Timestamp
    entry_price: float
    entry_reference: float
    stop: float
    target: float
    initial_risk_cash: float
    entry_bar_index: int
    maximum_favorable_price: float
    maximum_adverse_price: float
    current_metadata: Any
    base_quantity: int
    symbol_quantity_scale: float
    portfolio_quantity_scale: float
    quantity_scale: float
    position_scaling_reason: str
    symbol_recovery_deficit_at_entry: float
    portfolio_recovery_deficit_at_entry: float
    is_first_trade_in_trend_segment: int
    trigger_to_prior_5d_high_ratio: float
    opened_via_chase_gate: bool = False
    profit_floor_price: float = math.nan
    exit_legs: list[dict[str, Any]] = field(default_factory=list)


@dataclass(frozen=True)
class _PendingOrderDecision:
    status: str
    reason: str = ""
    match: tuple[Any, ...] | None = None


@dataclass(frozen=True)
class _CandidateFilterDecision:
    reason: str = ""
    detail: str = ""


@dataclass(frozen=True)
class _PositionExitDecision:
    reason: str = ""
    price: float = math.nan
    reference: float = math.nan
    metadata: Any = None
    pending_reason: str = ""
    limit_locked: bool = False


def _process_pending_order(
    pending: _PendingOrder,
    bar: Any,
    timestamp: pd.Timestamp,
    *,
    config: MultiTimeframeTrendConfig,
    equity: float,
) -> _PendingOrderDecision:
    """Evaluate one resting order without mutating account state."""
    expires_at = pd.Timestamp(pending.candidate["expires_at"])
    active_at = pd.Timestamp(pending.candidate["active_time"])
    if timestamp > expires_at:
        return _PendingOrderDecision("EXPIRED", "ORDER_EXPIRED")
    if timestamp < active_at or str(bar["contract_code"]) != str(
        pending.candidate["contract_code"]
    ):
        return _PendingOrderDecision("WAITING")
    blocked = _entry_blocked_at_match(active_at, timestamp, config)
    if blocked:
        return _PendingOrderDecision("BLOCKED", blocked)
    match = _match_entry(pending, bar, config=config, equity=equity)
    status = str(match[0])
    reason = str(match[1]) if status == "CANCELLED" else ""
    return _PendingOrderDecision(status, reason, match)


def _apply_candidate_filters(
    candidate: dict[str, Any],
    *,
    filled_bull_trend_ids: set[int],
    timestamp: pd.Timestamp,
    config: MultiTimeframeTrendConfig,
    checks: tuple[str, ...],
    stored_reason: str | None = None,
    cooldown_state: _SymbolLossCooldownState | None = None,
) -> _CandidateFilterDecision:
    """Apply shared candidate gates in the caller's existing priority order."""
    for check in checks:
        if check == "stored":
            reason = (
                str(candidate.get("filtered_reason", "") or "").strip()
                if stored_reason is None
                else str(stored_reason).strip()
            )
            if reason:
                return _CandidateFilterDecision(reason)
        elif check == "breakout":
            reason = _first_trend_entry_breakout_buffer_reason(
                candidate,
                filled_bull_trend_ids,
                config,
            )
            if reason:
                return _CandidateFilterDecision(reason)
        elif check == "cooldown":
            if cooldown_state is None:
                continue
            detail = _symbol_loss_cooldown_detail(
                cooldown_state,
                timestamp,
                config,
            )
            if detail:
                return _CandidateFilterDecision(
                    "SYMBOL_LOSS_COOLDOWN", detail
                )
        elif check == "session":
            minute = _minute_of_day(timestamp.timetz())
            if is_entry_window_blocked(
                minute,
                config.entry_blocked_session_windows,
            ):
                return _CandidateFilterDecision(
                    "ENTRY_SESSION_WINDOW_BLOCKED",
                    f"minute_of_day={minute}",
                )
        else:
            raise ValueError(f"unknown candidate filter: {check}")
    return _CandidateFilterDecision()


def _advance_open_position_context(
    position: _Position,
    context_row: dict[str, Any],
    *,
    config: MultiTimeframeTrendConfig,
) -> str:
    """Advance a position from shared higher-timeframe context."""
    daily_direction = int(context_row.get("daily_direction", 0) or 0)
    direction = int(position.pending.candidate["direction"])
    if daily_direction != direction:
        return "DAILY_DIRECTION_INVALID"
    _advance_position_stop(position, context_row, config=config)
    return ""


def _position_point_value(position: _Position) -> float:
    """每一个价格点对应的现金金额。"""
    return float(position.quantity) * float(
        position.current_metadata.contract_size
    )


def _price_for_unrealized_r(position: _Position, target_r: float) -> float:
    """把以 R 表示的浮盈换算成价格。"""
    risk = float(position.initial_risk_cash)
    value = _position_point_value(position)
    if not np.isfinite(risk) or risk <= 0 or value <= 0:
        return math.nan
    direction = int(position.pending.candidate["direction"])
    return float(position.entry_price) + direction * target_r * risk / value


def _peak_unrealized_r(position: _Position) -> float:
    """已确认的峰值浮盈（R）。极值由 ``_update_excursions`` 按 1 分钟 K 线的
    最高价（多头）/最低价（空头）维护。"""
    risk = float(position.initial_risk_cash)
    value = _position_point_value(position)
    if not np.isfinite(risk) or risk <= 0 or value <= 0:
        return math.nan
    direction = int(position.pending.candidate["direction"])
    move = direction * (
        float(position.maximum_favorable_price) - float(position.entry_price)
    )
    return move * value / risk


def _refresh_profit_floor(
    position: _Position,
    config: MultiTimeframeTrendConfig,
) -> None:
    """按当前峰值浮盈重算止盈地板，供**下一根** K 线使用。

    地板只上抬不下移：峰值本身单调不减，所以地板天然单调，但显式取 max 以防
    合约乘数或手数在减仓后变化时地板意外回落。
    """
    if not config.profit_floor_enabled:
        return
    peak_r = _peak_unrealized_r(position)
    if not np.isfinite(peak_r) or peak_r < float(config.profit_floor_arm_r):
        return
    giveback = max(
        float(config.profit_floor_giveback_r),
        float(config.profit_floor_giveback_pct) * peak_r,
    )
    floor_r = max(0.0, peak_r - giveback)
    price = _price_for_unrealized_r(position, floor_r)
    if not np.isfinite(price):
        return
    direction = int(position.pending.candidate["direction"])
    current = position.profit_floor_price
    if not np.isfinite(current):
        position.profit_floor_price = price
    elif direction > 0:
        position.profit_floor_price = max(current, price)
    else:
        position.profit_floor_price = min(current, price)


def _profit_floor_touched(position: _Position, bar: Any) -> bool:
    """地板是否被击穿。用上一根 K 线收盘后确认的地板，所以最早在设定峰值的
    下一分钟才可能触发。"""
    floor = float(position.profit_floor_price)
    if not np.isfinite(floor):
        return False
    direction = int(position.pending.candidate["direction"])
    if direction > 0:
        return float(bar["low"]) <= floor
    return float(bar["high"]) >= floor


def _profit_floor_exit(
    position: _Position,
    bar: Any,
    *,
    metadata: Any,
    config: MultiTimeframeTrendConfig,
) -> tuple[str, float, float]:
    """按市价单成交，在既有滑点模型之上再加 ``profit_floor_extra_slippage_ticks`` 档。

    地板被击穿时价格通常正在快速回落，按地板价原价成交是偏乐观的，
    额外一档滑点是刻意的保守处理。
    """
    if _protective_limit_locked(
        bar, metadata, int(position.pending.candidate["direction"])
    ):
        return "", math.nan, math.nan
    direction = int(position.pending.candidate["direction"])
    floor = float(position.profit_floor_price)
    reference = (
        min(float(bar["open"]), floor)
        if direction > 0
        else max(float(bar["open"]), floor)
    )
    price = _market_exit_price(position, reference, metadata)
    tick = float(metadata.price_tick)
    extra = int(config.profit_floor_extra_slippage_ticks)
    if extra > 0 and np.isfinite(tick) and tick > 0:
        price = _round_adverse(
            price - direction * extra * tick, tick, -direction
        )
    return "PROFIT_FLOOR", price, reference


def _manage_open_position(
    position: _Position,
    bar: Any,
    *,
    metadata_store: Any,
    root_symbol: str,
    exchange: str,
    pending_reason: str,
    protective_first: bool,
    config: MultiTimeframeTrendConfig,
) -> _PositionExitDecision:
    """Resolve pending and protective exits without mutating account state.

    止盈地板先于极值更新判定：地板来自**上一根** K 线收盘后确认的峰值，
    所以最早只能在设定峰值的下一分钟触发，不会用当根自己的最高价反过来平自己。
    """
    floor_touched = (
        config.profit_floor_enabled
        and pending_reason != "ROLL_MAPPING_CHANGED"
        and _profit_floor_touched(position, bar)
    )
    _update_excursions(position, bar)
    _refresh_profit_floor(position, config)
    protective_touched = (
        pending_reason != "ROLL_MAPPING_CHANGED"
        and _protective_exit_touched(position, bar)
    )
    if floor_touched:
        direction = int(position.pending.candidate["direction"])
        floor = float(position.profit_floor_price)
        stop = float(position.stop)
        # 两个价位同根都被触及时，先被打到的那个成交。多头地板在止损之上，
        # 价格是先穿地板再穿止损，按地板成交才是时序正确的。
        floor_first = (
            floor >= stop if direction > 0 else floor <= stop
        )
        if floor_first or not protective_touched:
            metadata = _exit_metadata_snapshot(
                metadata_store,
                root_symbol=root_symbol,
                exchange=exchange,
                position=position,
                bar=bar,
            )
            reason, price, reference = _profit_floor_exit(
                position, bar, metadata=metadata, config=config
            )
            if reason:
                return _PositionExitDecision(
                    reason, price, reference, metadata, pending_reason
                )
            # 涨跌停锁板：退化成待执行的市价平仓，下一根再试
            return _PositionExitDecision(
                metadata=metadata,
                pending_reason=pending_reason or "PROFIT_FLOOR",
                limit_locked=True,
            )
    if not pending_reason and not protective_touched:
        return _PositionExitDecision(pending_reason=pending_reason)
    metadata = _exit_metadata_snapshot(
        metadata_store,
        root_symbol=root_symbol,
        exchange=exchange,
        position=position,
        bar=bar,
    )
    effective_pending = pending_reason
    if protective_first and protective_touched:
        reason, price, reference = _protective_exit(
            position,
            bar,
            metadata=metadata,
        )
        if reason:
            return _PositionExitDecision(
                reason,
                price,
                reference,
                metadata,
                effective_pending,
            )
        if _protective_limit_locked(
            bar,
            metadata,
            int(position.pending.candidate["direction"]),
        ):
            effective_pending = "STOP"
            return _PositionExitDecision(
                metadata=metadata,
                pending_reason=effective_pending,
                limit_locked=True,
            )
    if effective_pending:
        if _protective_limit_locked(
            bar,
            metadata,
            int(position.pending.candidate["direction"]),
        ):
            return _PositionExitDecision(
                metadata=metadata,
                pending_reason=effective_pending,
                limit_locked=True,
            )
        reference = float(bar["open"])
        return _PositionExitDecision(
            effective_pending,
            _market_exit_price(position, reference, metadata),
            reference,
            metadata,
            effective_pending,
        )
    if protective_touched:
        reason, price, reference = _protective_exit(
            position,
            bar,
            metadata=metadata,
        )
        return _PositionExitDecision(
            reason,
            price,
            reference,
            metadata,
            effective_pending,
        )
    return _PositionExitDecision(
        metadata=metadata,
        pending_reason=effective_pending,
    )


@dataclass
class _IncrementalPortfolioEquity:
    """Cache per-position marked PnL components between portfolio events."""

    _components: dict[str, tuple[float, float]] = field(default_factory=dict)
    _dirty: bool = True
    _cached_cash: float = math.nan
    _cached_roots: tuple[str, ...] = ()
    _cached_equity: float = math.nan

    def update_position(
        self,
        root_symbol: str,
        position: _Position,
        mark: float,
    ) -> None:
        direction = int(position.pending.candidate["direction"])
        multiplier = float(position.pending.metadata.contract_size)
        fee_per_lot = float(
            position.pending.metadata.stressed_round_trip_fee_cash
        )
        components = (
            direction
            * (float(mark) - position.entry_price)
            * position.quantity
            * multiplier,
            fee_per_lot * position.quantity,
        )
        if self._components.get(root_symbol) != components:
            self._components[root_symbol] = components
            self._dirty = True

    def remove_position(self, root_symbol: str) -> None:
        if self._components.pop(root_symbol, None) is not None:
            self._dirty = True

    def marked_equity(
        self,
        cash: float,
        positions: dict[str, _Position],
    ) -> float:
        roots = tuple(positions)
        if (
            not self._dirty
            and cash == self._cached_cash
            and roots == self._cached_roots
        ):
            return self._cached_equity
        # Preserve the full calculation's position order and floating-point order.
        equity = cash
        for root_symbol in roots:
            marked_pnl, fees = self._components[root_symbol]
            equity += marked_pnl
            equity -= fees
        self._dirty = False
        self._cached_cash = cash
        self._cached_roots = roots
        self._cached_equity = equity
        return equity

    def reconcile(
        self,
        cash: float,
        positions: dict[str, _Position],
        marks: dict[str, float],
        *,
        trade_date: date,
    ) -> None:
        self._dirty = True
        incremental = self.marked_equity(cash, positions)
        expected = _portfolio_marked_equity(cash, positions, marks)
        tolerance = max(1e-9, abs(expected) * 1e-12)
        if abs(incremental - expected) > tolerance:
            raise RuntimeError(
                "incremental portfolio equity drift "
                f"on {trade_date}: incremental={incremental:.17g};"
                f"expected={expected:.17g};tolerance={tolerance:.17g}"
            )


@dataclass
class _SymbolScalingState:
    active: bool = False
    consecutive_losses: int = 0
    consecutive_loss_cash: float = 0.0
    recovery_deficit: float = 0.0


@dataclass
class _DrawdownScalingState:
    """Hysteresis band on realized-equity drawdown.

    The older portfolio scaler releases only once the whole loss has been earned
    back, which in practice never releases: on the 2026H1 run it triggered at
    1.23% drawdown on 01-26 and was still active on 06-03. This one enters above
    ``drawdown_scale_threshold`` and leaves below ``drawdown_scale_release``,
    so it recovers on its own and cannot latch.
    """

    high_water: float
    active: bool = False

    def drawdown(self, cash: float) -> float:
        if self.high_water <= 0:
            return 0.0
        return max(0.0, (self.high_water - cash) / self.high_water)

    def advance(self, cash: float, config: MultiTimeframeTrendConfig) -> bool:
        """Fold realized equity in; return True when the active flag changed."""
        self.high_water = max(self.high_water, float(cash))
        threshold = float(config.drawdown_scale_threshold)
        if threshold <= 0:
            changed = self.active
            self.active = False
            return changed
        level = self.drawdown(cash)
        was_active = self.active
        if not self.active and level > threshold:
            self.active = True
        elif self.active and level < float(config.drawdown_scale_release):
            self.active = False
        return self.active != was_active


@dataclass
class _PortfolioScalingState:
    high_water: float
    active: bool = False
    recovery_deficit: float = 0.0


@dataclass
class _VirtualOrder:
    root_symbol: str
    candidate_id: str
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


def _chase_high_gate_open(
    state: _ChaseHighState,
    config: MultiTimeframeTrendConfig,
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
        # 证据太薄。K=5/K=8 在两个数据集上都为负，说明窗口一短就不可信，
        # 一两笔虚拟单为正不足以重新放开追高。
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
    config: MultiTimeframeTrendConfig,
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


@dataclass
class _DailyCircuitState:
    trade_date: date | None = None
    realized_pnl: float = 0.0
    loss_streak: int = 0
    tripped: bool = False
    trip_reason: str = ""


@dataclass
class _SymbolLossCooldownState:
    last_loss_exit_time: pd.Timestamp | None = None
    cooldown_trigger_time: pd.Timestamp | None = None
    cooldown_release_time: pd.Timestamp | None = None


def _market_timestamp(value: Any) -> pd.Timestamp:
    timestamp = pd.Timestamp(value)
    if timestamp.tz is None:
        raise ValueError("symbol loss cooldown timestamp must be timezone-aware")
    return timestamp.tz_convert("Asia/Shanghai")


def _next_day_open_after(minimum_time: pd.Timestamp) -> pd.Timestamp:
    minimum_time = _market_timestamp(minimum_time)
    day_open = minimum_time.normalize() + pd.Timedelta(hours=9)
    if minimum_time <= day_open:
        return day_open
    return day_open + pd.Timedelta(days=1)


def _advance_daily_circuit(
    state: _DailyCircuitState,
    *,
    trade_date: Any,
    net_pnl: float,
    equity: float,
    config: MultiTimeframeTrendConfig,
) -> None:
    """Fold one closed trade into the exchange-trade-date circuit breaker."""
    if not config.daily_circuit_breaker_enabled:
        return
    current = pd.Timestamp(trade_date).date()
    if state.trade_date != current:
        state.trade_date = current
        state.realized_pnl = 0.0
        state.loss_streak = 0
        state.tripped = False
        state.trip_reason = ""
    state.realized_pnl += float(net_pnl)
    if float(net_pnl) < 0:
        state.loss_streak += 1
    else:
        state.loss_streak = 0
    risk_unit = float(equity) * float(config.risk_per_trade)
    reasons = []
    if (
        risk_unit > 0
        and state.realized_pnl
        <= -float(config.daily_circuit_breaker_loss_r) * risk_unit
    ):
        reasons.append("LOSS_R")
    if state.loss_streak >= int(config.daily_circuit_breaker_loss_streak):
        reasons.append("LOSS_STREAK")
    if reasons:
        state.tripped = True
        state.trip_reason = "+".join(reasons)


def _daily_circuit_blocked(
    state: _DailyCircuitState,
    trade_date: Any,
    config: MultiTimeframeTrendConfig,
) -> str:
    """Return a rejection detail when the circuit breaker blocks new entries."""
    if not config.daily_circuit_breaker_enabled or not state.tripped:
        return ""
    if state.trade_date != pd.Timestamp(trade_date).date():
        return ""
    return (
        f"reason={state.trip_reason};realized_pnl={state.realized_pnl:.12g};"
        f"loss_streak={state.loss_streak}"
    )


def _sector_exposure_blocked(
    root_symbol: str,
    sector_by_root: dict[str, str],
    positions: dict[str, "_Position"],
    pending: dict[str, Any],
    config: MultiTimeframeTrendConfig,
) -> str:
    """Return a rejection detail when the root's sector is already at its cap."""
    limit = int(config.max_positions_per_sector)
    sector = sector_by_root.get(root_symbol, "")
    if not sector:
        return ""
    active = {
        root
        for root in set(positions) | set(pending)
        if sector_by_root.get(root, "") == sector
    }
    active.discard(root_symbol)
    if len(active) < limit:
        return ""
    return f"sector={sector};active={len(active)};limit={limit}"


def _advance_symbol_loss_cooldown(
    state: _SymbolLossCooldownState,
    *,
    trade: dict[str, Any],
    config: MultiTimeframeTrendConfig,
) -> _SymbolLossCooldownState:
    if not config.symbol_loss_cooldown_enabled:
        return state

    exit_time = _market_timestamp(trade["exit_time"])
    if float(trade["net_pnl"]) >= 0:
        state.last_loss_exit_time = None
        state.cooldown_trigger_time = None
        state.cooldown_release_time = None
        return state

    previous_loss_exit_time = state.last_loss_exit_time
    if previous_loss_exit_time is not None:
        previous_loss_exit_time = _market_timestamp(previous_loss_exit_time)
    state.last_loss_exit_time = exit_time
    state.cooldown_trigger_time = None
    state.cooldown_release_time = None

    if previous_loss_exit_time is None:
        return state

    loss_interval = exit_time - previous_loss_exit_time
    pair_window = pd.Timedelta(hours=config.symbol_loss_pair_window_hours)
    if pd.Timedelta(0) <= loss_interval <= pair_window:
        state.cooldown_trigger_time = exit_time
        minimum_release_time = exit_time + pd.Timedelta(
            hours=config.symbol_loss_cooldown_hours
        )
        state.cooldown_release_time = _next_day_open_after(minimum_release_time)
    return state


def _symbol_loss_cooldown_detail(
    state: _SymbolLossCooldownState,
    timestamp: pd.Timestamp,
    config: MultiTimeframeTrendConfig,
) -> str:
    if not config.symbol_loss_cooldown_enabled:
        return ""

    timestamp = _market_timestamp(timestamp)
    if state.cooldown_trigger_time is None or state.cooldown_release_time is None:
        return ""
    trigger_time = _market_timestamp(state.cooldown_trigger_time)
    release_time = _market_timestamp(state.cooldown_release_time)
    if timestamp >= release_time:
        return ""
    return (
        f"second_loss_exit={trigger_time.isoformat()}; "
        f"release_at={release_time.isoformat()}"
    )


def _advance_symbol_scaling(
    state: _SymbolScalingState,
    net_pnl: float,
    config: MultiTimeframeTrendConfig,
) -> _SymbolScalingState:
    if state.active:
        state.recovery_deficit -= float(net_pnl)
        if state.recovery_deficit < 0:
            state.active = False
            state.consecutive_losses = 0
            state.consecutive_loss_cash = 0.0
            state.recovery_deficit = 0.0
        return state

    if net_pnl < 0:
        state.consecutive_losses += 1
        state.consecutive_loss_cash += abs(float(net_pnl))
        if state.consecutive_losses >= config.symbol_loss_streak:
            state.active = True
            state.recovery_deficit = state.consecutive_loss_cash
    else:
        state.consecutive_losses = 0
        state.consecutive_loss_cash = 0.0
    return state


def _advance_portfolio_scaling(
    state: _PortfolioScalingState,
    *,
    cash: float,
    net_pnl: float,
    config: MultiTimeframeTrendConfig,
) -> _PortfolioScalingState:
    if state.active:
        state.recovery_deficit -= float(net_pnl)
        if state.recovery_deficit < 0:
            state.active = False
            state.recovery_deficit = 0.0
            state.high_water = float(cash)
        return state

    state.high_water = max(state.high_water, float(cash))
    drawdown = (state.high_water - float(cash)) / state.high_water
    if drawdown > config.portfolio_drawdown_threshold:
        state.active = True
        state.recovery_deficit = state.high_water - float(cash)
    return state


def _scaling_event_type(
    *,
    was_active: bool,
    is_active: bool,
    net_pnl: float,
) -> str:
    if not was_active and is_active:
        return "TRIGGER"
    if was_active and not is_active:
        return "RECOVER"
    if was_active:
        return "EXTEND" if net_pnl < 0 else "PROGRESS"
    return ""


def _update_symbol_scaling_after_trade(
    *,
    symbol: str,
    trade: dict[str, Any],
    timestamp: pd.Timestamp,
    cash: float,
    symbol_state: _SymbolScalingState,
    config: MultiTimeframeTrendConfig,
    event_rows: list[dict[str, Any]],
) -> None:
    net_pnl = float(trade["net_pnl"])
    symbol_was_active = symbol_state.active
    _advance_symbol_scaling(symbol_state, net_pnl, config)
    symbol_event = _scaling_event_type(
        was_active=symbol_was_active,
        is_active=symbol_state.active,
        net_pnl=net_pnl,
    )
    if symbol_event:
        event_rows.append(
            {
                "event_time": timestamp,
                "sequence": len(event_rows),
                "scope": "SYMBOL",
                "symbol": symbol,
                "event_type": symbol_event,
                "candidate_id": trade["candidate_id"],
                "net_pnl": net_pnl,
                "realized_cash": cash,
                "high_water": math.nan,
                "drawdown_fraction": math.nan,
                "position_scale": (
                    config.symbol_position_scale if symbol_state.active else 1.0
                ),
                "recovery_deficit": symbol_state.recovery_deficit,
            }
        )



def _record_drawdown_scaling(
    state: _DrawdownScalingState,
    *,
    cash: float,
    timestamp: pd.Timestamp,
    candidate_id: str,
    net_pnl: float,
    config: MultiTimeframeTrendConfig,
    event_rows: list[dict[str, Any]],
) -> None:
    """Advance the drawdown band and audit any state change."""
    if not state.advance(cash, config):
        return
    event_rows.append(
        {
            "event_time": timestamp,
            "sequence": len(event_rows) + 1,
            "scope": "DRAWDOWN",
            "symbol": "",
            "event_type": "TRIGGER" if state.active else "RECOVER",
            "candidate_id": candidate_id,
            "net_pnl": net_pnl,
            "realized_cash": cash,
            "high_water": state.high_water,
            "drawdown_fraction": state.drawdown(cash),
            "position_scale": (
                config.drawdown_scale_factor if state.active else 1.0
            ),
            "recovery_deficit": max(0.0, state.high_water - cash),
        }
    )


def _update_portfolio_scaling_after_exit(
    *,
    candidate_id: str,
    net_pnl: float,
    timestamp: pd.Timestamp,
    cash: float,
    portfolio_state: _PortfolioScalingState,
    config: MultiTimeframeTrendConfig,
    event_rows: list[dict[str, Any]],
) -> None:
    portfolio_was_active = portfolio_state.active
    _advance_portfolio_scaling(
        portfolio_state,
        cash=cash,
        net_pnl=net_pnl,
        config=config,
    )
    portfolio_event = _scaling_event_type(
        was_active=portfolio_was_active,
        is_active=portfolio_state.active,
        net_pnl=net_pnl,
    )
    if portfolio_event:
        drawdown = (
            (portfolio_state.high_water - cash) / portfolio_state.high_water
            if portfolio_state.high_water > 0
            else math.nan
        )
        event_rows.append(
            {
                "event_time": timestamp,
                "sequence": len(event_rows),
                "scope": "PORTFOLIO",
                "symbol": "",
                "event_type": portfolio_event,
                "candidate_id": candidate_id,
                "net_pnl": net_pnl,
                "realized_cash": cash,
                "high_water": portfolio_state.high_water,
                "drawdown_fraction": drawdown,
                "position_scale": (
                    config.portfolio_position_scale
                    if portfolio_state.active
                    else 1.0
                ),
                "recovery_deficit": portfolio_state.recovery_deficit,
            }
        )


def _position_scaling_snapshot(
    *,
    base_quantity: int,
    symbol_state: _SymbolScalingState,
    portfolio_state: _PortfolioScalingState,
    config: MultiTimeframeTrendConfig,
    drawdown_state: "_DrawdownScalingState | None" = None,
) -> tuple[int, float, float, float, str]:
    symbol_factor = config.symbol_position_scale if symbol_state.active else 1.0
    portfolio_factor = (
        config.portfolio_position_scale if portfolio_state.active else 1.0
    )
    drawdown_active = bool(drawdown_state is not None and drawdown_state.active)
    drawdown_factor = config.drawdown_scale_factor if drawdown_active else 1.0
    quantity_scale = symbol_factor * portfolio_factor * drawdown_factor
    scaled_quantity = math.floor(base_quantity * quantity_scale)
    if (
        config.position_scale_min_one_lot
        and scaled_quantity < 1
        and base_quantity >= 1
        and quantity_scale > 0
    ):
        # 高价值合约（AG/AU/SC）常常本来就只有 1 手，向下取整会直接把它们减成 0 手
        # 而被拒单，等于在回撤里优先淘汰最赚钱的品种。
        scaled_quantity = 1
    reasons = []
    if symbol_state.active:
        reasons.append("SYMBOL")
    if portfolio_state.active:
        reasons.append("PORTFOLIO")
    if drawdown_active:
        reasons.append("DRAWDOWN")
    return (
        scaled_quantity,
        symbol_factor,
        portfolio_factor,
        quantity_scale,
        "+".join(reasons),
    )


def replay_trend_strategy(
    *,
    root_symbol: str,
    exchange: str,
    minute_bars: pd.DataFrame,
    roll_execution_bars: pd.DataFrame | None = None,
    five_minute_context: pd.DataFrame,
    candidates: pd.DataFrame,
    metadata_store: Any,
    config: MultiTimeframeTrendConfig,
    start: date,
    end: date,
    initial_equity: float,
) -> ReplayArtifacts:
    """Replay one root on minute events, with decisions frozen at 5-minute closes."""
    if end < start:
        raise ValueError("end precedes start")
    if not math.isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("initial_equity must be finite and positive")
    bars = _validated_minutes(minute_bars, start=start, end=end)
    roll_bars = _validated_roll_minutes(
        roll_execution_bars,
        start=start,
        end=end,
    )
    roll_lookup = _roll_bar_lookup(roll_bars)
    candidate_frame = _validated_candidates(candidates)
    context = _context_lookup(five_minute_context)
    by_signal = {
        key: group.to_dict("records")
        for key, group in candidate_frame.groupby("signal_time", sort=True)
    }

    cash = float(initial_equity)
    symbol_scaling = _SymbolScalingState()
    portfolio_scaling = _PortfolioScalingState(high_water=cash)
    drawdown_scaling = _DrawdownScalingState(high_water=cash)
    pending: _PendingOrder | None = None
    position: _Position | None = None
    filled_bull_trend_ids: set[int] = set()
    pending_market_exit = ""
    active_contract = ""
    plan_rows: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    exit_leg_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    rejection_rows: list[dict[str, Any]] = []
    scaling_event_rows: list[dict[str, Any]] = []
    daily_rows: dict[date, dict[str, Any]] = {}
    last_bar: pd.Series | None = None
    last_position_bar: pd.Series | None = None

    for bar_index, (_, bar) in enumerate(bars.iterrows()):
        timestamp = pd.Timestamp(bar["bar_end"])
        contract = str(bar["contract_code"])
        if active_contract and contract != active_contract:
            if pending is not None:
                order_rows.append(
                    _order_row(pending, "CANCELLED", "ROLL_MAPPING_CHANGED", timestamp)
                )
                pending = None
            if position is not None:
                pending_market_exit = "ROLL_MAPPING_CHANGED"
        active_contract = contract
        last_bar = bar

        if position is not None:
            position_contract = str(position.pending.candidate["contract_code"])
            position_bar = (
                bar
                if contract == position_contract
                else roll_lookup.get((timestamp, position_contract))
            )
            if position_bar is None:
                continue
            position_bar = position_bar.copy()
            position_bar["_bar_index"] = bar_index
            last_position_bar = position_bar
            exit_decision = _manage_open_position(
                position,
                position_bar,
                metadata_store=metadata_store,
                root_symbol=root_symbol,
                exchange=exchange,
                pending_reason=pending_market_exit,
                protective_first=False,
                config=config,
            )
            if exit_decision.reason:
                trade, fill, leg = _close_position(
                    position,
                    exit_metadata=exit_decision.metadata,
                    timestamp=timestamp,
                    price=exit_decision.price,
                    reference=exit_decision.reference,
                    reason=exit_decision.reason,
                    bar_index=bar_index,
                )
                cash += float(leg["net_pnl"])
                fill_rows.append(fill)
                exit_leg_rows.append(leg)
                _update_portfolio_scaling_after_exit(
                    candidate_id=str(leg["candidate_id"]),
                    net_pnl=float(leg["net_pnl"]),
                    timestamp=timestamp,
                    cash=cash,
                    portfolio_state=portfolio_scaling,
                    config=config,
                    event_rows=scaling_event_rows,
                )
                _record_drawdown_scaling(
                    drawdown_scaling,
                    cash=cash,
                    timestamp=timestamp,
                    candidate_id=str(leg["candidate_id"]),
                    net_pnl=float(leg["net_pnl"]),
                    config=config,
                    event_rows=scaling_event_rows,
                )
                if trade is not None:
                    trade_rows.append(trade)
                    _update_symbol_scaling_after_trade(
                        symbol=root_symbol,
                        trade=trade,
                        timestamp=timestamp,
                        cash=cash,
                        symbol_state=symbol_scaling,
                        config=config,
                        event_rows=scaling_event_rows,
                    )
                    position = None
                pending_market_exit = ""

        if pending is not None:
            active_order = pending
            decision = _process_pending_order(
                active_order,
                bar,
                timestamp,
                config=config,
                equity=_marked_equity(cash, position, bar),
            )
            if decision.status == "EXPIRED":
                order_rows.append(
                    _order_row(
                        active_order,
                        "EXPIRED",
                        decision.reason,
                        timestamp,
                    )
                )
                pending = None
            elif decision.status == "BLOCKED":
                order_rows.append(
                    _order_row(
                        active_order,
                        "CANCELLED",
                        decision.reason,
                        timestamp,
                    )
                )
                rejection_rows.append(
                    _rejection(
                        root_symbol,
                        active_order.candidate,
                        decision.reason,
                        detail=f"stage=match;event_time={timestamp.isoformat()}",
                    )
                )
                pending = None
            elif decision.status == "CANCELLED":
                order_rows.append(
                    _order_row(
                        active_order,
                        "CANCELLED",
                        decision.reason,
                        timestamp,
                    )
                )
                pending = None
            elif decision.status == "FILLED":
                outcome = decision.match
                if outcome is None:
                    raise RuntimeError("filled order decision requires match data")
                base_quantity = int(outcome[4])
                (
                    quantity,
                    symbol_factor,
                    portfolio_factor,
                    quantity_scale,
                    scaling_reason,
                ) = _position_scaling_snapshot(
                    base_quantity=base_quantity,
                    symbol_state=symbol_scaling,
                    portfolio_state=portfolio_scaling,
                    config=config,
                    drawdown_state=drawdown_scaling,
                )
                if quantity < 1:
                    reason = "DYNAMIC_RISK_SCALE_BELOW_ONE_LOT"
                    order_rows.append(
                        _order_row(active_order, "CANCELLED", reason, timestamp)
                    )
                    rejection_rows.append(
                        _rejection(root_symbol, active_order.candidate, reason)
                    )
                else:
                    is_first_trade_in_trend_segment = (
                        _is_first_trade_in_trend_segment(
                            active_order.candidate,
                            filled_bull_trend_ids,
                        )
                    )
                    position, fill = _open_position(
                        active_order,
                        timestamp=timestamp,
                        bar_index=bar_index,
                        fill_price=float(outcome[2]),
                        reference=float(outcome[3]),
                        quantity=quantity,
                        base_quantity=base_quantity,
                        symbol_quantity_scale=symbol_factor,
                        portfolio_quantity_scale=portfolio_factor,
                        quantity_scale=quantity_scale,
                        position_scaling_reason=scaling_reason,
                        symbol_recovery_deficit=(
                            symbol_scaling.recovery_deficit
                        ),
                        portfolio_recovery_deficit=(
                            portfolio_scaling.recovery_deficit
                        ),
                        is_first_trade_in_trend_segment=(
                            is_first_trade_in_trend_segment
                        ),
                        config=config,
                    )
                    _record_bull_trend_fill(
                        active_order.candidate,
                        filled_bull_trend_ids,
                    )
                    fill_rows.append(fill)
                    last_position_bar = bar
                    order_rows.append(
                        _order_row(active_order, "FILLED", "FILLED", timestamp)
                    )
                pending = None

        context_row = context.get(timestamp)
        if context_row is not None:
            daily_direction = int(context_row.get("daily_direction", 0) or 0)
            if pending is not None:
                direction = int(pending.candidate["direction"])
                if daily_direction != direction:
                    order_rows.append(
                        _order_row(
                            pending,
                            "CANCELLED",
                            "DAILY_DIRECTION_INVALID",
                            timestamp,
                        )
                    )
                    pending = None
            if position is not None and pending_market_exit != "ROLL_MAPPING_CHANGED":
                position_reason = _advance_open_position_context(
                    position,
                    context_row,
                    config=config,
                )
                if position_reason:
                    pending_market_exit = position_reason

        for candidate in by_signal.get(timestamp, ()):  # Known only after this bar closes.
            filter_decision = _apply_candidate_filters(
                candidate,
                filled_bull_trend_ids=filled_bull_trend_ids,
                timestamp=timestamp,
                config=config,
                checks=("stored", "breakout", "session"),
            )
            if filter_decision.reason:
                rejection_rows.append(
                    _rejection(
                        root_symbol,
                        candidate,
                        filter_decision.reason,
                        detail=filter_decision.detail,
                    )
                )
                continue
            if pending is not None or position is not None:
                rejection_rows.append(
                    _rejection(root_symbol, candidate, "ROOT_EXPOSURE_ACTIVE")
                )
                continue
            try:
                pending = _submit_candidate(
                    candidate,
                    root_symbol=root_symbol,
                    exchange=exchange,
                    metadata_store=metadata_store,
                    config=config,
                    equity=_marked_equity(cash, position, bar),
                )
            except (LookupError, TypeError, ValueError) as exc:
                rejection_rows.append(
                    _rejection(
                        root_symbol,
                        candidate,
                        getattr(exc, "reason_code", "BLOCKED_METADATA"),
                        detail=str(exc),
                    )
                )
                pending = None
                continue
            plan_rows.append(_plan_row(pending))
            order_rows.append(_order_row(pending, "ACTIVE", "", timestamp))

        daily_rows[bar["exchange_trade_date"]] = _equity_row(
            date_value=bar["exchange_trade_date"],
            cash=cash,
            position=position,
            mark=float(bar["close"]),
        )

    if position is not None and pending_market_exit == "ROLL_MAPPING_CHANGED":
        raise _blocked_roll_execution_bar(
            str(position.pending.candidate["contract_code"]),
            active_contract,
        )
    if position is not None and last_position_bar is not None:
        timestamp = pd.Timestamp(last_position_bar["bar_end"])
        exit_metadata = _exit_metadata_snapshot(
            metadata_store,
            root_symbol=root_symbol,
            exchange=exchange,
            position=position,
            bar=last_position_bar,
        )
        if _protective_limit_locked(
            last_position_bar,
            exit_metadata,
            int(position.pending.candidate["direction"]),
        ):
            raise BlockedMetadataError(
                "BLOCKED_INTERVAL_END_LIQUIDATION: final position cannot be "
                "closed on a counterparty-locked limit bar",
                "BLOCKED_INTERVAL_END_LIQUIDATION",
            )
        reference = float(last_position_bar["close"])
        price = _market_exit_price(position, reference, exit_metadata)
        trade, fill, leg = _close_position(
            position,
            exit_metadata=exit_metadata,
            timestamp=timestamp,
            price=price,
            reference=reference,
            reason="INTERVAL_END",
            bar_index=max(len(bars) - 1, 0),
        )
        cash += float(leg["net_pnl"])
        fill_rows.append(fill)
        exit_leg_rows.append(leg)
        _update_portfolio_scaling_after_exit(
            candidate_id=str(leg["candidate_id"]),
            net_pnl=float(leg["net_pnl"]),
            timestamp=timestamp,
            cash=cash,
            portfolio_state=portfolio_scaling,
            config=config,
            event_rows=scaling_event_rows,
        )
        _record_drawdown_scaling(
            drawdown_scaling,
            cash=cash,
            timestamp=timestamp,
            candidate_id=str(leg["candidate_id"]),
            net_pnl=float(leg["net_pnl"]),
            config=config,
            event_rows=scaling_event_rows,
        )
        if trade is None:
            raise RuntimeError("interval-end liquidation must close the position")
        trade_rows.append(trade)
        _update_symbol_scaling_after_trade(
            symbol=root_symbol,
            trade=trade,
            timestamp=timestamp,
            cash=cash,
            symbol_state=symbol_scaling,
            config=config,
            event_rows=scaling_event_rows,
        )
        daily_rows[last_position_bar["exchange_trade_date"]] = _equity_row(
            date_value=last_position_bar["exchange_trade_date"],
            cash=cash,
            position=None,
            mark=reference,
        )
    if pending is not None:
        order_rows.append(
            _order_row(
                pending,
                "CANCELLED",
                "INTERVAL_END",
                pd.Timestamp(last_bar["bar_end"]) if last_bar is not None else pd.NaT,
            )
        )
    trades = _with_trade_returns(
        pd.DataFrame(trade_rows, columns=TRADE_COLUMNS),
        observation_bars_by_symbol={
            root_symbol: _observation_bars(bars, roll_bars)
        },
        default_symbol=root_symbol,
    )

    return ReplayArtifacts(
        candidates=candidate_frame.reset_index(drop=True),
        plans=pd.DataFrame(plan_rows, columns=PLAN_COLUMNS),
        orders=pd.DataFrame(order_rows, columns=ORDER_COLUMNS),
        fills=pd.DataFrame(fill_rows, columns=FILL_COLUMNS),
        exit_legs=pd.DataFrame(exit_leg_rows, columns=EXIT_LEG_COLUMNS),
        trades=trades,
        daily_equity=pd.DataFrame(
            daily_rows.values(), columns=DAILY_EQUITY_COLUMNS
        ).sort_values("date", kind="stable").reset_index(drop=True),
        rejections=pd.DataFrame(rejection_rows, columns=REJECTION_COLUMNS),
        position_scaling_events=pd.DataFrame(
            scaling_event_rows, columns=SCALING_EVENT_COLUMNS
        ),
    )


def replay_trend_portfolio(
    *,
    inputs: tuple[PortfolioReplayInput, ...],
    metadata_store: Any,
    config: MultiTimeframeTrendConfig,
    start: date,
    end: date,
    initial_equity: float,
    turnover_table: pd.DataFrame | None = None,
    gate_diagnostics: GateFailOpenDiagnostics | None = None,
) -> ReplayArtifacts:
    """Replay multiple roots on one deterministic shared account."""
    if not inputs:
        raise ValueError("portfolio replay requires at least one symbol")
    if end < start:
        raise ValueError("end precedes start")
    if not math.isfinite(initial_equity) or initial_equity <= 0:
        raise ValueError("initial_equity must be finite and positive")
    ordered_inputs = tuple(sorted(inputs, key=lambda item: item.root_symbol))
    roots = [item.root_symbol for item in ordered_inputs]
    if len(roots) != len(set(roots)):
        raise ValueError("portfolio replay root symbols must be unique")

    definitions = {item.root_symbol: item for item in ordered_inputs}
    contexts = {
        item.root_symbol: _context_lookup(item.five_minute_context)
        for item in ordered_inputs
    }
    roll_frames = {
        item.root_symbol: _validated_roll_minutes(
            item.roll_execution_bars,
            start=start,
            end=end,
        )
        for item in ordered_inputs
    }
    roll_bar_lookups = {
        root_symbol: _roll_bar_lookup(frame)
        for root_symbol, frame in roll_frames.items()
    }
    candidate_frames: list[pd.DataFrame] = []
    candidates_by_event: dict[tuple[pd.Timestamp, str], list[dict[str, Any]]] = {}
    minute_frames: list[pd.DataFrame] = []
    observation_bars_by_symbol: dict[str, pd.DataFrame] = {}
    for item in ordered_inputs:
        candidate_frame = _validated_candidates(item.candidates)
        candidate_frame["symbol"] = item.root_symbol
        candidate_frames.append(candidate_frame)
        for signal_time, group in candidate_frame.groupby("signal_time", sort=True):
            candidates_by_event[(pd.Timestamp(signal_time), item.root_symbol)] = (
                group.to_dict("records")
            )
        bars = _validated_minutes(item.minute_bars, start=start, end=end)
        observation_bars_by_symbol[item.root_symbol] = _observation_bars(
            bars,
            roll_frames[item.root_symbol],
        )
        bars = bars.assign(
            _root_symbol=item.root_symbol,
            _exchange=item.exchange,
            _bar_index=np.arange(len(bars), dtype=int),
        )
        minute_frames.append(bars)
    candidates = pd.concat(candidate_frames, ignore_index=True).sort_values(
        ["signal_time", "candidate_id"], kind="stable"
    )
    if candidates["candidate_id"].astype(str).duplicated().any():
        raise ValueError("portfolio candidate_id must be globally unique")
    events = pd.concat(minute_frames, ignore_index=True).sort_values(
        ["bar_end", "_root_symbol"], kind="stable"
    )

    cash = float(initial_equity)
    symbol_scaling = {root: _SymbolScalingState() for root in roots}
    symbol_loss_cooldowns = {
        root: _SymbolLossCooldownState() for root in roots
    }
    portfolio_scaling = _PortfolioScalingState(high_water=cash)
    drawdown_scaling = _DrawdownScalingState(high_water=cash)
    pending: dict[str, _PendingOrder] = {}
    positions: dict[str, _Position] = {}
    filled_bull_trend_ids: dict[str, set[int]] = {
        root: set() for root in roots
    }
    pending_market_exit: dict[str, str] = {}
    pending_exit_quantity: dict[str, int] = {}
    active_contract: dict[str, str] = {}
    marks: dict[str, float] = {}
    last_bars: dict[str, dict[str, Any]] = {}
    last_position_bars: dict[str, dict[str, Any]] = {}
    equity_tracker = _IncrementalPortfolioEquity()
    equity_trade_dates: frozenset[date] = frozenset()
    overnight_bounds_cache: dict[
        tuple[Any, ...], tuple[pd.Timestamp, pd.Timestamp]
    ] = {}
    overnight_sessions_id = {
        root: _sessions_cache_id(definitions[root].sessions) for root in roots
    }
    plan_rows: list[dict[str, Any]] = []
    order_rows: list[dict[str, Any]] = []
    fill_rows: list[dict[str, Any]] = []
    exit_leg_rows: list[dict[str, Any]] = []
    trade_rows: list[dict[str, Any]] = []
    rejection_rows: list[dict[str, Any]] = []
    scaling_event_rows: list[dict[str, Any]] = []
    daily_rows: dict[date, dict[str, Any]] = {}
    reduction_windows: set[tuple[str, date]] = set()
    pre_break_windows: set[tuple[str, date]] = set()
    daily_circuit = _DailyCircuitState()
    sector_by_root = {root: sector_for_root(root) for root in roots}
    chase_state = _ChaseHighState()
    chase_gate_candidates: set[str] = set()
    virtual_orders: dict[str, _VirtualOrder] = {}
    virtual_positions: dict[str, _VirtualPosition] = {}
    turnover_eligible = turnover_eligible_by_date(
        turnover_table,
        share=config.turnover_share_threshold,
        lookback_days=config.turnover_lookback_days,
        diagnostics=gate_diagnostics,
    )
    high_gap_by_root = {
        item.root_symbol: _high_gap_flags_by_trade_date(
            item.daily_context,
            config,
            diagnostics=gate_diagnostics,
        )
        for item in ordered_inputs
    }
    event_columns = tuple(events.columns)
    event_root_index = event_columns.index("_root_symbol")

    for timestamp_value, event_rows in events.groupby("bar_end", sort=True):
        timestamp = pd.Timestamp(timestamp_value)
        # ``events`` is already globally stable-sorted by time and root above.
        bars_at_event = {
            str(values[event_root_index]): dict(zip(event_columns, values))
            for values in event_rows.to_numpy(copy=False)
        }
        event_trade_dates = frozenset(
            bar["exchange_trade_date"] for bar in bars_at_event.values()
        )
        if equity_trade_dates and event_trade_dates != equity_trade_dates:
            equity_tracker.reconcile(
                cash,
                positions,
                marks,
                trade_date=max(equity_trade_dates),
            )
        equity_trade_dates = event_trade_dates
        position_bars: dict[str, dict[str, Any]] = {}
        for root_symbol, bar in bars_at_event.items():
            contract = str(bar["contract_code"])
            previous_contract = active_contract.get(root_symbol, "")
            if previous_contract and contract != previous_contract:
                old_pending = pending.pop(root_symbol, None)
                if old_pending is not None:
                    order_rows.append(
                        _order_row(
                            old_pending,
                            "CANCELLED",
                            "ROLL_MAPPING_CHANGED",
                            timestamp,
                        )
                    )
                if root_symbol in positions:
                    pending_market_exit[root_symbol] = "ROLL_MAPPING_CHANGED"
                    pending_exit_quantity.pop(root_symbol, None)
            active_contract[root_symbol] = contract
            last_bars[root_symbol] = bar
            position = positions.get(root_symbol)
            if position is None:
                marks[root_symbol] = float(bar["close"])
                continue
            position_contract = str(position.pending.candidate["contract_code"])
            position_bar = (
                bar
                if contract == position_contract
                else roll_bar_lookups[root_symbol].get(
                    (timestamp, position_contract)
                )
            )
            if position_bar is None:
                continue
            position_bar = position_bar.copy()
            position_bar["_bar_index"] = int(bar["_bar_index"])
            position_bars[root_symbol] = position_bar
            last_position_bars[root_symbol] = position_bar
            marks[root_symbol] = float(position_bar["close"])
            equity_tracker.update_position(
                root_symbol, position, marks[root_symbol]
            )
            if (
                position.current_metadata.daily.exchange_trade_date
                != position_bar["exchange_trade_date"]
            ):
                definition = definitions[root_symbol]
                position.current_metadata = _exit_metadata_snapshot(
                    metadata_store,
                    root_symbol=root_symbol,
                    exchange=definition.exchange,
                    position=position,
                    bar=position_bar,
                )

        if config.chase_high_virtual_enabled:
            _advance_virtual_positions(
                virtual_positions,
                bars_at_event,
                contexts,
                timestamp,
                chase_state,
                config,
                rejection_rows,
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
                    direction=order.direction,
                    entry_time=timestamp,
                    entry_price=fill,
                    stop_price=order.stop_price,
                    initial_risk=risk,
                    tick_size=order.tick_size,
                )

        exit_roots = sorted(
            (root for root in position_bars if root in positions),
            key=lambda root: (
                pending_market_exit.get(root) == "OVERNIGHT_MARGIN_REDUCTION",
                positions[root].entry_time,
                root,
            ),
            reverse=True,
        )
        for root_symbol in exit_roots:
            bar = position_bars[root_symbol]
            position = positions.get(root_symbol)
            if position is None:
                continue
            pending_reason = pending_market_exit.get(root_symbol, "")
            definition = definitions[root_symbol]
            exit_decision = _manage_open_position(
                position,
                bar,
                metadata_store=metadata_store,
                root_symbol=root_symbol,
                exchange=definition.exchange,
                pending_reason=pending_reason,
                protective_first=True,
                config=config,
            )
            if (
                exit_decision.limit_locked
                and pending_reason == "OVERNIGHT_MARGIN_REDUCTION"
            ):
                rejection_rows.append(
                    _rejection(
                        root_symbol,
                        position.pending.candidate,
                        "OVERNIGHT_REDUCTION_LIMIT_LOCKED",
                        detail=f"retry_pending_at={timestamp.isoformat()}",
                    )
                )
            if exit_decision.pending_reason != pending_reason:
                pending_market_exit[root_symbol] = exit_decision.pending_reason
                pending_exit_quantity.pop(root_symbol, None)
            if not exit_decision.reason:
                continue
            close_quantity = (
                pending_exit_quantity.get(root_symbol)
                if exit_decision.reason == exit_decision.pending_reason
                else None
            )
            trade, fill, leg = _close_position(
                position,
                quantity=close_quantity,
                exit_metadata=exit_decision.metadata,
                timestamp=timestamp,
                price=exit_decision.price,
                reference=exit_decision.reference,
                reason=exit_decision.reason,
                bar_index=int(bar["_bar_index"]),
            )
            cash += float(leg["net_pnl"])
            equity_tracker.update_position(
                root_symbol,
                position,
                marks.get(root_symbol, position.entry_price),
            )
            fill_rows.append(fill)
            exit_leg_rows.append(leg)
            _update_portfolio_scaling_after_exit(
                candidate_id=str(leg["candidate_id"]),
                net_pnl=float(leg["net_pnl"]),
                timestamp=timestamp,
                cash=cash,
                portfolio_state=portfolio_scaling,
                config=config,
                event_rows=scaling_event_rows,
            )
            _record_drawdown_scaling(
                drawdown_scaling,
                cash=cash,
                timestamp=timestamp,
                candidate_id=str(leg["candidate_id"]),
                net_pnl=float(leg["net_pnl"]),
                config=config,
                event_rows=scaling_event_rows,
            )
            pending_market_exit.pop(root_symbol, None)
            pending_exit_quantity.pop(root_symbol, None)
            if trade is not None:
                if position.opened_via_chase_gate:
                    # 开闸放行的真实追高单必须回灌，否则门槛开了就再也关不上
                    chase_state.record(float(trade["net_r"]))
                    rejection_rows.append(
                        _portfolio_risk_event(
                            timestamp,
                            "CHASE_HIGH_REAL_CLOSED",
                            detail=(
                                f"symbol={root_symbol};"
                                f"realized_r={float(trade['net_r']):.6g};"
                                f"prior_r={chase_state.prior_r(config.chase_high_lookback):.6g}"
                            ),
                        )
                    )
                _advance_daily_circuit(
                    daily_circuit,
                    trade_date=bar["exchange_trade_date"],
                    net_pnl=float(trade["net_pnl"]),
                    equity=equity_tracker.marked_equity(cash, positions),
                    config=config,
                )
                trade_rows.append(trade)
                _advance_symbol_loss_cooldown(
                    symbol_loss_cooldowns[root_symbol],
                    trade=trade,
                    config=config,
                )
                _update_symbol_scaling_after_trade(
                    symbol=root_symbol,
                    trade=trade,
                    timestamp=timestamp,
                    cash=cash,
                    symbol_state=symbol_scaling[root_symbol],
                    config=config,
                    event_rows=scaling_event_rows,
                )
                positions.pop(root_symbol)
                equity_tracker.remove_position(root_symbol)

        cutoff_roots = {
            root_symbol
            for root_symbol, bar in bars_at_event.items()
            if _is_overnight_reduction_time(
                timestamp,
                definitions[root_symbol].sessions,
                config.overnight_reduction_minutes,
                bounds_cache=overnight_bounds_cache,
                sessions_id=overnight_sessions_id[root_symbol],
            )
        }
        for root_symbol in sorted(cutoff_roots):
            trade_date = bars_at_event[root_symbol]["exchange_trade_date"]
            key = (root_symbol, trade_date)
            if key in reduction_windows:
                continue
            reduction_windows.add(key)
            active_order = pending.pop(root_symbol, None)
            if active_order is not None:
                order_rows.append(
                    _order_row(
                        active_order,
                        "CANCELLED",
                        "OVERNIGHT_ENTRY_WINDOW",
                        timestamp,
                    )
                )
        if positions and cutoff_roots:
            equity = equity_tracker.marked_equity(cash, positions)
            margin = _portfolio_margin_used(positions, marks)
            margin_limit = equity * config.overnight_margin_utilization
            if equity > 0 and margin > margin_limit:
                rejection_rows.append(
                    _portfolio_risk_event(
                        timestamp,
                        "OVERNIGHT_MARGIN_LIMIT_BREACH",
                        detail=(
                            f"margin={margin:.12g};limit={margin_limit:.12g}"
                        ),
                    )
                )
            planned_margin = margin
            for root_symbol, reason in pending_market_exit.items():
                if reason == "OVERNIGHT_MARGIN_REDUCTION" and root_symbol in positions:
                    position = positions[root_symbol]
                    pending_quantity = min(
                        position.quantity,
                        pending_exit_quantity.get(root_symbol, position.quantity),
                    )
                    planned_margin -= _position_margin_per_lot(
                        root_symbol, position, marks
                    ) * pending_quantity
            reducible = sorted(
                (
                    root
                    for root in positions
                    if root not in pending_market_exit
                    and _is_session_timestamp(
                        timestamp, definitions[root].sessions
                    )
                ),
                key=lambda root: (positions[root].entry_time, root),
                reverse=True,
            )
            for root_symbol in reducible:
                if equity > 0 and planned_margin <= margin_limit:
                    break
                position = positions[root_symbol]
                margin_per_lot = _position_margin_per_lot(
                    root_symbol, position, marks
                )
                required_quantity = math.ceil(
                    (planned_margin - margin_limit) / margin_per_lot - 1e-12
                )
                close_quantity = min(position.quantity, required_quantity)
                pending_market_exit[root_symbol] = "OVERNIGHT_MARGIN_REDUCTION"
                pending_exit_quantity[root_symbol] = close_quantity
                planned_margin -= margin_per_lot * close_quantity

        if config.pre_break_protection_enabled:
            for root_symbol in sorted(positions):
                bar = bars_at_event.get(root_symbol)
                if bar is None:
                    continue
                if not _is_pre_break_time(
                    timestamp,
                    definitions[root_symbol].sessions,
                    config.pre_break_lead_minutes,
                ):
                    continue
                trade_date = pd.Timestamp(bar["exchange_trade_date"]).date()
                key = (root_symbol, trade_date)
                if key in pre_break_windows:
                    continue
                pre_break_windows.add(key)
                position = positions[root_symbol]
                mark = marks.get(root_symbol)
                if mark is None:
                    continue
                open_r = _unrealized_r(position, mark)
                if not math.isfinite(open_r):
                    continue
                high_gap = bool(
                    high_gap_by_root.get(root_symbol, {}).get(trade_date, False)
                )
                threshold = (
                    float(config.pre_break_high_gap_min_unrealized_r)
                    if high_gap
                    else float(config.pre_break_min_unrealized_r)
                )
                if open_r < threshold:
                    close_quantity = int(position.quantity)
                    reason = "PRE_BREAK_NO_BUFFER"
                elif high_gap and float(config.pre_break_gap_risk_scale) < 1.0:
                    kept = int(
                        math.floor(
                            position.quantity * float(config.pre_break_gap_risk_scale)
                        )
                    )
                    close_quantity = int(position.quantity) - max(kept, 1)
                    reason = "PRE_BREAK_GAP_RISK_REDUCTION"
                else:
                    continue
                if close_quantity < 1:
                    continue
                existing_reason = pending_market_exit.get(root_symbol, "")
                if existing_reason:
                    existing_quantity = int(
                        pending_exit_quantity.get(root_symbol, position.quantity)
                    )
                    if existing_quantity >= close_quantity:
                        continue
                pending_market_exit[root_symbol] = reason
                if close_quantity >= int(position.quantity):
                    pending_exit_quantity.pop(root_symbol, None)
                else:
                    pending_exit_quantity[root_symbol] = close_quantity
                rejection_rows.append(
                    _portfolio_risk_event(
                        timestamp,
                        reason,
                        detail=(
                            f"symbol={root_symbol};unrealized_r={open_r:.6g};"
                            f"threshold={threshold:.6g};high_gap={int(high_gap)};"
                            f"close_quantity={close_quantity}"
                        ),
                    )
                )

        for root_symbol, bar in bars_at_event.items():
            active_order = pending.get(root_symbol)
            if active_order is None:
                continue
            equity = equity_tracker.marked_equity(cash, positions)
            decision = _process_pending_order(
                active_order,
                bar,
                timestamp,
                config=config,
                equity=equity,
            )
            if decision.status == "EXPIRED":
                order_rows.append(
                    _order_row(
                        active_order,
                        "EXPIRED",
                        decision.reason,
                        timestamp,
                    )
                )
                pending.pop(root_symbol)
                continue
            if decision.status == "WAITING":
                continue
            if decision.status == "BLOCKED":
                order_rows.append(
                    _order_row(
                        active_order,
                        "CANCELLED",
                        decision.reason,
                        timestamp,
                    )
                )
                rejection_rows.append(
                    _rejection(
                        root_symbol,
                        active_order.candidate,
                        decision.reason,
                        detail=f"stage=match;event_time={timestamp.isoformat()}",
                    )
                )
                pending.pop(root_symbol)
                continue
            if decision.status == "CANCELLED":
                order_rows.append(
                    _order_row(
                        active_order,
                        "CANCELLED",
                        decision.reason,
                        timestamp,
                    )
                )
                pending.pop(root_symbol)
                continue
            if decision.status != "FILLED":
                continue
            outcome = decision.match
            if outcome is None:
                raise RuntimeError("filled order decision requires match data")
            base_quantity = int(outcome[4])
            (
                scaled_quantity,
                symbol_factor,
                portfolio_factor,
                quantity_scale,
                scaling_reason,
            ) = _position_scaling_snapshot(
                base_quantity=base_quantity,
                symbol_state=symbol_scaling[root_symbol],
                portfolio_state=portfolio_scaling,
                config=config,
                drawdown_state=drawdown_scaling,
            )
            if scaled_quantity < 1:
                reason = "DYNAMIC_RISK_SCALE_BELOW_ONE_LOT"
                order_rows.append(
                    _order_row(active_order, "CANCELLED", reason, timestamp)
                )
                rejection_rows.append(
                    _rejection(root_symbol, active_order.candidate, reason)
                )
                pending.pop(root_symbol)
                continue
            quantity, limit_reason = _portfolio_entry_quantity(
                pending=active_order,
                requested_quantity=scaled_quantity,
                fill_price=float(outcome[2]),
                equity=equity,
                positions=positions,
                marks=marks,
                config=config,
                portfolio_margin_utilization=(
                    config.overnight_margin_utilization
                    if _is_night_timestamp(
                        timestamp, definitions[root_symbol].sessions
                    )
                    else config.intraday_margin_utilization
                ),
            )
            if quantity < 1:
                order_rows.append(
                    _order_row(active_order, "CANCELLED", limit_reason, timestamp)
                )
                rejection_rows.append(
                    _rejection(root_symbol, active_order.candidate, limit_reason)
                )
                pending.pop(root_symbol)
                continue
            is_first_trade_in_trend_segment = _is_first_trade_in_trend_segment(
                active_order.candidate,
                filled_bull_trend_ids[root_symbol],
            )
            position, fill = _open_position(
                active_order,
                timestamp=timestamp,
                bar_index=int(bar["_bar_index"]),
                fill_price=float(outcome[2]),
                reference=float(outcome[3]),
                quantity=quantity,
                base_quantity=base_quantity,
                symbol_quantity_scale=symbol_factor,
                portfolio_quantity_scale=portfolio_factor,
                quantity_scale=quantity_scale,
                position_scaling_reason=scaling_reason,
                symbol_recovery_deficit=(
                    symbol_scaling[root_symbol].recovery_deficit
                ),
                portfolio_recovery_deficit=(
                    portfolio_scaling.recovery_deficit
                ),
                is_first_trade_in_trend_segment=(
                    is_first_trade_in_trend_segment
                ),
                opened_via_chase_gate=(
                    str(active_order.candidate["candidate_id"])
                    in chase_gate_candidates
                ),
                config=config,
            )
            _record_bull_trend_fill(
                active_order.candidate,
                filled_bull_trend_ids[root_symbol],
            )
            positions[root_symbol] = position
            equity_tracker.update_position(
                root_symbol,
                position,
                marks.get(root_symbol, position.entry_price),
            )
            last_position_bars[root_symbol] = bar
            fill_rows.append(fill)
            order_rows.append(
                _order_row(active_order, "FILLED", "FILLED", timestamp)
            )
            pending.pop(root_symbol)

        for root_symbol, bar in bars_at_event.items():
            context_row = contexts[root_symbol].get(timestamp)
            if context_row is not None:
                daily_direction = int(context_row.get("daily_direction", 0) or 0)
                active_order = pending.get(root_symbol)
                if active_order is not None and daily_direction != int(
                    active_order.candidate["direction"]
                ):
                    order_rows.append(
                        _order_row(
                            active_order,
                            "CANCELLED",
                            "DAILY_DIRECTION_INVALID",
                            timestamp,
                        )
                    )
                    pending.pop(root_symbol)
                position = positions.get(root_symbol)
                if (
                    position is not None
                    and pending_market_exit.get(root_symbol)
                    != "ROLL_MAPPING_CHANGED"
                ):
                    position_reason = _advance_open_position_context(
                        position,
                        context_row,
                        config=config,
                    )
                    if position_reason:
                        pending_market_exit[root_symbol] = position_reason
                        pending_exit_quantity.pop(root_symbol, None)

            for candidate in candidates_by_event.get((timestamp, root_symbol), ()):
                reason = str(candidate.get("filtered_reason", "") or "").strip()
                if (
                    reason == "ENTRY_RANGE_POSITION_TOO_HIGH"
                    and int(candidate.get("chase_high_candidate", 0) or 0) == 1
                    and (
                        config.chase_high_virtual_enabled
                        or config.chase_high_entry_enabled
                    )
                ):
                    allowed, detail = _chase_high_gate_open(chase_state, config)
                    if allowed:
                        # 最近的虚拟追高单在赚钱，本笔按真实单继续走下面的流程
                        rejection_rows.append(
                            _portfolio_risk_event(
                                timestamp,
                                "CHASE_HIGH_GATE_OPEN",
                                detail=f"symbol={root_symbol};{detail}",
                            )
                        )
                        chase_gate_candidates.add(str(candidate["candidate_id"]))
                        reason = ""
                    elif not config.chase_high_virtual_enabled:
                        # 真实追高关闭且影子跟踪也关闭：按普通区间位置拒绝处理
                        rejection_rows.append(
                            _rejection(root_symbol, candidate, reason)
                        )
                        continue
                    else:
                        tick = _virtual_tick_size(
                            candidate,
                            root_symbol=root_symbol,
                            exchange=definitions[root_symbol].exchange,
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
                                direction=int(candidate["direction"]),
                                trigger=float(candidate["trigger"]),
                                stop_price=float(candidate["stop_price"]),
                                expires_at=pd.Timestamp(candidate["expires_at"]),
                                active_at=pd.Timestamp(candidate["active_time"]),
                                tick_size=tick,
                            )
                        rejection_rows.append(
                            _rejection(
                                root_symbol,
                                candidate,
                                reason,
                                detail=f"virtual=1;{detail}",
                            )
                        )
                        continue
                filter_decision = _apply_candidate_filters(
                    candidate,
                    filled_bull_trend_ids=filled_bull_trend_ids[root_symbol],
                    timestamp=timestamp,
                    config=config,
                    checks=("stored", "breakout", "cooldown"),
                    stored_reason=reason,
                    cooldown_state=symbol_loss_cooldowns[root_symbol],
                )
                if filter_decision.reason:
                    rejection_rows.append(
                        _rejection(
                            root_symbol,
                            candidate,
                            filter_decision.reason,
                            detail=filter_decision.detail,
                        )
                    )
                    continue
                if root_symbol in pending or root_symbol in positions:
                    rejection_rows.append(
                        _rejection(root_symbol, candidate, "ROOT_EXPOSURE_ACTIVE")
                    )
                    continue
                if (root_symbol, bar["exchange_trade_date"]) in reduction_windows:
                    rejection_rows.append(
                        _rejection(
                            root_symbol,
                            candidate,
                            "OVERNIGHT_ENTRY_WINDOW",
                        )
                    )
                    continue
                session_decision = _apply_candidate_filters(
                    candidate,
                    filled_bull_trend_ids=filled_bull_trend_ids[root_symbol],
                    timestamp=timestamp,
                    config=config,
                    checks=("session",),
                )
                if session_decision.reason:
                    rejection_rows.append(
                        _rejection(
                            root_symbol,
                            candidate,
                            session_decision.reason,
                            detail=session_decision.detail,
                        )
                    )
                    continue
                circuit_detail = _daily_circuit_blocked(
                    daily_circuit,
                    bar["exchange_trade_date"],
                    config,
                )
                if circuit_detail:
                    rejection_rows.append(
                        _rejection(
                            root_symbol,
                            candidate,
                            "DAILY_LOSS_CIRCUIT_BREAKER",
                            detail=circuit_detail,
                        )
                    )
                    continue
                sector_detail = _sector_exposure_blocked(
                    root_symbol,
                    sector_by_root,
                    positions,
                    pending,
                    config,
                )
                if sector_detail:
                    rejection_rows.append(
                        _rejection(
                            root_symbol,
                            candidate,
                            "SECTOR_CONCENTRATION_LIMIT",
                            detail=sector_detail,
                        )
                    )
                    continue
                turnover_detail = _turnover_share_blocked(
                    root_symbol,
                    bar["exchange_trade_date"],
                    turnover_eligible,
                    diagnostics=gate_diagnostics,
                )
                if turnover_detail:
                    rejection_rows.append(
                        _rejection(
                            root_symbol,
                            candidate,
                            "TURNOVER_SHARE_NOT_ELIGIBLE",
                            detail=turnover_detail,
                        )
                    )
                    continue
                definition = definitions[root_symbol]
                try:
                    active_order = _submit_candidate(
                        candidate,
                        root_symbol=root_symbol,
                        exchange=definition.exchange,
                        metadata_store=metadata_store,
                        config=config,
                        equity=equity_tracker.marked_equity(
                            cash, positions
                        ),
                    )
                except (LookupError, TypeError, ValueError) as exc:
                    rejection_rows.append(
                        _rejection(
                            root_symbol,
                            candidate,
                            getattr(exc, "reason_code", "BLOCKED_METADATA"),
                            detail=str(exc),
                        )
                    )
                    continue
                pending[root_symbol] = active_order
                plan_rows.append(_plan_row(active_order))
                order_rows.append(_order_row(active_order, "ACTIVE", "", timestamp))

        for trade_date in {
            bar["exchange_trade_date"] for bar in bars_at_event.values()
        }:
            row = _portfolio_equity_row(
                date_value=trade_date,
                cash=cash,
                positions=positions,
                marks=marks,
                marked_equity=equity_tracker.marked_equity(
                    cash, positions
                ),
                overnight=(
                    bool(cutoff_roots)
                    or any(
                        _is_night_timestamp(
                            timestamp, definitions[root].sessions
                        )
                        for root in bars_at_event
                    )
                ),
            )
            daily_rows[trade_date] = _merge_portfolio_daily_row(
                daily_rows.get(trade_date), row
            )

    if equity_trade_dates:
        equity_tracker.reconcile(
            cash,
            positions,
            marks,
            trade_date=max(equity_trade_dates),
        )
    for root_symbol, position in sorted(positions.items()):
        if pending_market_exit.get(root_symbol) == "ROLL_MAPPING_CHANGED":
            raise _blocked_roll_execution_bar(
                str(position.pending.candidate["contract_code"]),
                active_contract[root_symbol],
            )
    for root_symbol in sorted(positions):
        position = positions[root_symbol]
        bar = last_position_bars[root_symbol]
        definition = definitions[root_symbol]
        timestamp = pd.Timestamp(bar["bar_end"])
        exit_metadata = _exit_metadata_snapshot(
            metadata_store,
            root_symbol=root_symbol,
            exchange=definition.exchange,
            position=position,
            bar=bar,
        )
        if _protective_limit_locked(
            bar,
            exit_metadata,
            int(position.pending.candidate["direction"]),
        ):
            raise BlockedMetadataError(
                "BLOCKED_INTERVAL_END_LIQUIDATION: final position cannot be "
                "closed on a counterparty-locked limit bar",
                "BLOCKED_INTERVAL_END_LIQUIDATION",
            )
        reference = float(bar["close"])
        price = _market_exit_price(position, reference, exit_metadata)
        trade, fill, leg = _close_position(
            position,
            exit_metadata=exit_metadata,
            timestamp=timestamp,
            price=price,
            reference=reference,
            reason="INTERVAL_END",
            bar_index=int(bar["_bar_index"]),
        )
        cash += float(leg["net_pnl"])
        equity_tracker.update_position(
            root_symbol,
            position,
            marks.get(root_symbol, position.entry_price),
        )
        fill_rows.append(fill)
        exit_leg_rows.append(leg)
        _update_portfolio_scaling_after_exit(
            candidate_id=str(leg["candidate_id"]),
            net_pnl=float(leg["net_pnl"]),
            timestamp=timestamp,
            cash=cash,
            portfolio_state=portfolio_scaling,
            config=config,
            event_rows=scaling_event_rows,
        )
        _record_drawdown_scaling(
            drawdown_scaling,
            cash=cash,
            timestamp=timestamp,
            candidate_id=str(leg["candidate_id"]),
            net_pnl=float(leg["net_pnl"]),
            config=config,
            event_rows=scaling_event_rows,
        )
        if trade is None:
            raise RuntimeError("interval-end liquidation must close the position")
        trade_rows.append(trade)
        _advance_symbol_loss_cooldown(
            symbol_loss_cooldowns[root_symbol],
            trade=trade,
            config=config,
        )
        _update_symbol_scaling_after_trade(
            symbol=root_symbol,
            trade=trade,
            timestamp=timestamp,
            cash=cash,
            symbol_state=symbol_scaling[root_symbol],
            config=config,
            event_rows=scaling_event_rows,
        )
        positions.pop(root_symbol)
        equity_tracker.remove_position(root_symbol)
        trade_date = bar["exchange_trade_date"]
        row = _portfolio_equity_row(
            date_value=bar["exchange_trade_date"],
            cash=cash,
            positions=positions,
            marks=marks,
            marked_equity=equity_tracker.marked_equity(cash, positions),
        )
        daily_rows[trade_date] = _merge_portfolio_daily_row(
            daily_rows.get(trade_date), row
        )
    for root_symbol, active_order in sorted(pending.items()):
        last_bar = last_bars.get(root_symbol)
        order_rows.append(
            _order_row(
                active_order,
                "CANCELLED",
                "INTERVAL_END",
                pd.Timestamp(last_bar["bar_end"]) if last_bar is not None else pd.NaT,
            )
        )
    trades = _with_trade_returns(
        pd.DataFrame(trade_rows, columns=TRADE_COLUMNS),
        observation_bars_by_symbol=observation_bars_by_symbol,
    )

    return ReplayArtifacts(
        candidates=candidates.reset_index(drop=True),
        plans=pd.DataFrame(plan_rows, columns=PLAN_COLUMNS),
        orders=pd.DataFrame(order_rows, columns=ORDER_COLUMNS),
        fills=pd.DataFrame(fill_rows, columns=FILL_COLUMNS),
        exit_legs=pd.DataFrame(exit_leg_rows, columns=EXIT_LEG_COLUMNS),
        trades=trades,
        daily_equity=pd.DataFrame(
            daily_rows.values(), columns=DAILY_EQUITY_COLUMNS
        ).sort_values("date", kind="stable").reset_index(drop=True),
        rejections=pd.DataFrame(rejection_rows, columns=REJECTION_COLUMNS),
        position_scaling_events=pd.DataFrame(
            scaling_event_rows, columns=SCALING_EVENT_COLUMNS
        ),
    )


def _portfolio_entry_quantity(
    *,
    pending: _PendingOrder,
    requested_quantity: int,
    fill_price: float,
    equity: float,
    positions: dict[str, _Position],
    marks: dict[str, float],
    config: MultiTimeframeTrendConfig,
    portfolio_margin_utilization: float,
) -> tuple[int, str]:
    if len(positions) >= config.max_concurrent_positions:
        return 0, "MAX_CONCURRENT_POSITIONS"
    direction = int(pending.candidate["direction"])
    margin_rate = (
        float(pending.metadata.daily.margin_rate_long)
        if direction > 0
        else float(pending.metadata.daily.margin_rate_short)
    )
    margin_per_lot = (
        fill_price * float(pending.metadata.contract_size) * margin_rate
    )
    if not math.isfinite(margin_per_lot) or margin_per_lot <= 0 or equity <= 0:
        return 0, "PORTFOLIO_MARGIN_LIMIT"
    symbol_lots = math.floor(
        equity * config.max_symbol_margin_utilization / margin_per_lot
    )
    if symbol_lots < 1:
        return 0, "SYMBOL_MARGIN_LIMIT"
    used_margin = _portfolio_margin_used(positions, marks)
    portfolio_lots = math.floor(
        max(0.0, equity * portfolio_margin_utilization - used_margin)
        / margin_per_lot
    )
    if portfolio_lots < 1:
        return 0, "PORTFOLIO_MARGIN_LIMIT"
    return min(requested_quantity, symbol_lots, portfolio_lots), ""


def _session_break_closes(sessions: tuple[Any, ...]) -> tuple[Any, ...]:
    """Return the closing time of each session that is followed by a long break.

    A session's last segment end is the point after which the market is shut for
    hours: the day session closes into the evening gap, the night session closes
    into the overnight gap. The midday recess is a segment boundary inside one
    session and is deliberately not treated as a break.
    """
    closes = []
    for session in sessions:
        if not session.segments:
            continue
        closes.append(session.segments[-1].end)
    if not closes:
        raise ValueError("portfolio replay requires at least one session close")
    return tuple(closes)


def _minute_of_day(value: Any) -> int:
    return int(value.hour) * 60 + int(value.minute)


def _is_pre_break_time(
    timestamp: pd.Timestamp,
    sessions: tuple[Any, ...],
    lead_minutes: int,
) -> bool:
    """Return whether ``timestamp`` sits in the lead-in to any session close."""
    current = _minute_of_day(timestamp.timetz())
    for close_time in _session_break_closes(sessions):
        close = _minute_of_day(close_time)
        start = (close - int(lead_minutes)) % 1440
        if start <= close:
            if start <= current <= close:
                return True
        elif current >= start or current <= close:
            return True
    return False


def turnover_eligible_by_date(
    table: pd.DataFrame | None,
    *,
    share: float,
    lookback_days: int,
    diagnostics: GateFailOpenDiagnostics | None = None,
) -> dict[date, frozenset[str]]:
    """Map each trade date to the roots covering ``share`` of recent turnover.

    Turnover is summed over the ``lookback_days`` completed trade dates strictly
    before each date, so the cohort for a day never sees that day's own volume.
    Roots are ranked high to low and the smallest prefix whose cumulative share
    reaches the threshold is eligible. An empty or missing table yields an empty
    mapping, and callers treat that as "cannot judge" rather than "nobody
    qualifies".
    """
    if share <= 0:
        return {}
    if table is None or table.empty:
        observe_gate(diagnostics, "turnover_table_unavailable", fail_open=True)
        return {}
    required = {"root_symbol", "trade_date", "turnover"}
    if not required.issubset(table.columns):
        observe_gate(diagnostics, "turnover_table_unavailable", fail_open=True)
        return {}
    frame = table.copy()
    frame["trade_date"] = pd.to_datetime(frame["trade_date"], errors="coerce")
    frame = frame.dropna(subset=["trade_date"])
    if frame.empty:
        observe_gate(diagnostics, "turnover_table_unavailable", fail_open=True)
        return {}
    observe_gate(diagnostics, "turnover_table_unavailable")
    frame["root_symbol"] = frame["root_symbol"].astype(str).str.upper()
    frame["turnover"] = pd.to_numeric(frame["turnover"], errors="coerce").fillna(0.0)
    pivot = (
        frame.pivot_table(
            index="trade_date",
            columns="root_symbol",
            values="turnover",
            aggfunc="sum",
        )
        .sort_index()
        .fillna(0.0)
    )
    window = max(1, int(lookback_days))
    rolled = pivot.rolling(window, min_periods=1).sum().shift(1)
    result: dict[date, frozenset[str]] = {}
    for stamp, row in rolled.iterrows():
        values = row.dropna()
        values = values[values > 0]
        if values.empty:
            continue
        ordered = values.sort_values(ascending=False)
        cumulative = ordered.cumsum() / ordered.sum()
        keep = int((cumulative < float(share)).sum()) + 1
        result[pd.Timestamp(stamp).date()] = frozenset(ordered.index[:keep])
    return result


def _turnover_share_blocked(
    root_symbol: str,
    trade_date: Any,
    eligible_by_date: dict[date, frozenset[str]],
    *,
    diagnostics: GateFailOpenDiagnostics | None = None,
) -> str:
    """Return a rejection detail when the root is outside the turnover cohort."""
    if not eligible_by_date:
        return ""
    cohort = eligible_by_date.get(pd.Timestamp(trade_date).date())
    if cohort is None:
        observe_gate(
            diagnostics, "turnover_cohort_missing_date", fail_open=True
        )
        return ""
    observe_gate(diagnostics, "turnover_cohort_missing_date")
    if str(root_symbol).upper() in cohort:
        return ""
    return f"cohort_size={len(cohort)}"


def _order_crossed_recess(
    active_at: pd.Timestamp,
    timestamp: pd.Timestamp,
    max_recess_minutes: int,
) -> bool:
    """Return whether a resting order has sat through a recess it must not survive.

    An order carries the information of the bar that produced it. Once the market
    has been shut longer than ``max_recess_minutes`` that information is stale, so
    the order is cancelled rather than matched against a reopening print. Bars are
    at most one minute apart inside a session, so any gap beyond the threshold
    between the order becoming active and the current bar is a recess.
    """
    if max_recess_minutes <= 0:
        return False
    elapsed = (timestamp - active_at).total_seconds() / 60.0
    return elapsed > float(max_recess_minutes)


def _entry_blocked_at_match(
    active_at: pd.Timestamp,
    timestamp: pd.Timestamp,
    config: MultiTimeframeTrendConfig,
) -> str:
    """Re-check the entry gates that must hold at match time, not only at signal time.

    A candidate is screened when it arrives, but the resting stop order can fill
    minutes or hours later. Without this second check an order placed before the
    midday recess fills inside a blocked afternoon window.
    """
    if is_entry_window_blocked(
        _minute_of_day(timestamp.timetz()),
        config.entry_blocked_session_windows,
    ):
        return "ENTRY_SESSION_WINDOW_BLOCKED"
    if _order_crossed_recess(
        active_at, timestamp, config.order_max_recess_minutes
    ):
        return "ORDER_CROSSED_SESSION_RECESS"
    return ""


def _unrealized_r(position: "_Position", mark: float) -> float:
    """Return the position's open profit in units of its entry-locked 1R."""
    risk = float(position.initial_risk_cash)
    if not math.isfinite(risk) or risk <= 0:
        return math.nan
    direction = int(position.pending.candidate["direction"])
    multiplier = float(position.current_metadata.contract_size)
    move = direction * (float(mark) - float(position.entry_price))
    return move * position.quantity * multiplier / risk


def _high_gap_flags_by_trade_date(
    daily_context: pd.DataFrame | None,
    config: MultiTimeframeTrendConfig,
    *,
    diagnostics: GateFailOpenDiagnostics | None = None,
) -> dict[date, bool]:
    """Classify each trade date as high-gap using only prior completed days.

    The measure is ``|open - previous close| / previous ATR14``. Its rolling
    quantile is shifted by one bar so a day is never classified with its own
    gap, keeping the flag causal.
    """
    if daily_context is None or daily_context.empty:
        observe_gate(diagnostics, "high_gap_classification", fail_open=True)
        return {}
    observe_gate(diagnostics, "high_gap_classification")
    frame = daily_context
    required = {"open", "close", "daily_atr14"}
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError(
            "daily_context is missing required high-gap columns: "
            + ",".join(missing)
        )
    if "exchange_trade_date" in frame.columns:
        keys = pd.to_datetime(frame["exchange_trade_date"], errors="coerce")
    elif "bar_end" in frame.columns:
        keys = pd.to_datetime(frame["bar_end"], errors="coerce", utc=True)
        keys = keys.dt.tz_convert("Asia/Shanghai")
    else:
        raise ValueError(
            "daily_context requires exchange_trade_date or bar_end for high-gap classification"
        )
    open_price = pd.to_numeric(frame["open"], errors="coerce")
    close = pd.to_numeric(frame["close"], errors="coerce")
    atr_value = pd.to_numeric(frame["daily_atr14"], errors="coerce")
    ratio = (open_price - close.shift(1)).abs() / atr_value.shift(1)
    ratio = ratio.replace([np.inf, -np.inf], np.nan)
    rolling = ratio.rolling(
        int(config.high_gap_lookback_days),
        min_periods=max(10, int(config.high_gap_lookback_days) // 3),
    ).quantile(float(config.high_gap_quantile))
    flags = rolling.shift(1) >= float(config.high_gap_ratio_threshold)
    result: dict[date, bool] = {}
    for key, flag in zip(keys, flags):
        if pd.isna(key):
            continue
        result[pd.Timestamp(key).date()] = bool(flag)
    return result


def _is_overnight_reduction_time(
    timestamp: pd.Timestamp,
    sessions: tuple[Any, ...],
    lead_minutes: int,
    *,
    bounds_cache: dict[
        tuple[Any, ...], tuple[pd.Timestamp, pd.Timestamp]
    ] | None = None,
    sessions_id: tuple[Any, ...] | None = None,
) -> bool:
    resolved_sessions_id = sessions_id or _sessions_cache_id(sessions)
    key = (
        resolved_sessions_id,
        timestamp.date(),
        str(timestamp.tz),
        int(lead_minutes),
    )
    bounds = bounds_cache.get(key) if bounds_cache is not None else None
    if bounds is None:
        day_ends = [
            segment.end
            for session in sessions
            if not bool(session.is_night)
            for segment in session.segments
        ]
        if not day_ends:
            raise ValueError("portfolio replay requires a day-session close")
        close_time = max(day_ends)
        close = pd.Timestamp.combine(timestamp.date(), close_time).tz_localize(
            timestamp.tz
        )
        bounds = (close - pd.Timedelta(minutes=lead_minutes), close)
        if bounds_cache is not None:
            bounds_cache[key] = bounds
    cutoff, close = bounds
    return bool(cutoff <= timestamp <= close)


def _sessions_cache_id(sessions: tuple[Any, ...]) -> tuple[Any, ...]:
    return tuple(
        (
            str(session.session_id),
            bool(session.is_night),
            tuple(
                (
                    str(segment.segment_id),
                    segment.start,
                    segment.end,
                    segment.bucket_anchor,
                )
                for segment in session.segments
            ),
        )
        for session in sessions
    )


def _is_night_timestamp(
    timestamp: pd.Timestamp,
    sessions: tuple[Any, ...],
) -> bool:
    value = timestamp.timetz().replace(tzinfo=None)
    for session in sessions:
        if not bool(session.is_night):
            continue
        for segment in session.segments:
            if segment.start < segment.end:
                inside = segment.start < value <= segment.end
            else:
                inside = value > segment.start or value <= segment.end
            if inside:
                return True
    return False


def _is_session_timestamp(
    timestamp: pd.Timestamp,
    sessions: tuple[Any, ...],
) -> bool:
    value = timestamp.timetz().replace(tzinfo=None)
    for session in sessions:
        for segment in session.segments:
            if segment.start < segment.end:
                inside = segment.start < value <= segment.end
            else:
                inside = value > segment.start or value <= segment.end
            if inside:
                return True
    return False


def _portfolio_marked_equity(
    cash: float,
    positions: dict[str, _Position],
    marks: dict[str, float],
) -> float:
    equity = cash
    for root_symbol, position in positions.items():
        mark = marks.get(root_symbol, position.entry_price)
        direction = int(position.pending.candidate["direction"])
        multiplier = float(position.pending.metadata.contract_size)
        equity += (
            direction
            * (mark - position.entry_price)
            * position.quantity
            * multiplier
        )
        equity -= (
            float(position.pending.metadata.stressed_round_trip_fee_cash)
            * position.quantity
        )
    return equity


def _portfolio_margin_used(
    positions: dict[str, _Position],
    marks: dict[str, float],
) -> float:
    return sum(
        _position_margin_per_lot(root_symbol, position, marks)
        * position.quantity
        for root_symbol, position in positions.items()
    )


def _position_margin_per_lot(
    root_symbol: str,
    position: _Position,
    marks: dict[str, float],
) -> float:
    direction = int(position.pending.candidate["direction"])
    margin_rate = (
        float(position.current_metadata.daily.margin_rate_long)
        if direction > 0
        else float(position.current_metadata.daily.margin_rate_short)
    )
    return (
        marks.get(root_symbol, position.entry_price)
        * float(position.pending.metadata.contract_size)
        * margin_rate
    )


def _portfolio_equity_row(
    *,
    date_value: date,
    cash: float,
    positions: dict[str, _Position],
    marks: dict[str, float],
    marked_equity: float | None = None,
    overnight: bool = False,
) -> dict[str, Any]:
    equity = (
        _portfolio_marked_equity(cash, positions, marks)
        if marked_equity is None
        else marked_equity
    )
    margin = _portfolio_margin_used(positions, marks)
    unrealized = equity - cash
    open_risk = sum(
        position.initial_risk_cash
        * position.quantity
        / position.initial_quantity
        for position in positions.values()
    )
    utilization = margin / equity if equity > 0 else math.nan
    symbol_utilizations = []
    if equity > 0:
        for root_symbol, position in positions.items():
            symbol_utilizations.append(
                _portfolio_margin_used(
                    {root_symbol: position},
                    marks,
                )
                / equity
            )
    return {
        "date": date_value,
        "equity": equity,
        "cash": cash,
        "unrealized_pnl": unrealized,
        "margin_used": margin,
        "margin_utilization": utilization,
        "open_risk": open_risk,
        "peak_margin_utilization": utilization,
        "peak_overnight_margin_utilization": utilization if overnight else 0.0,
        "peak_symbol_margin_utilization": max(symbol_utilizations, default=0.0),
        "max_concurrent_positions": len(positions),
    }


def _merge_portfolio_daily_row(
    previous: dict[str, Any] | None,
    current: dict[str, Any],
) -> dict[str, Any]:
    if previous is None:
        return current
    merged = dict(current)
    for column in (
        "peak_margin_utilization",
        "peak_overnight_margin_utilization",
        "peak_symbol_margin_utilization",
        "max_concurrent_positions",
    ):
        merged[column] = max(float(previous[column]), float(current[column]))
    return merged


def _validated_minutes(frame: pd.DataFrame, *, start: date, end: date) -> pd.DataFrame:
    required = {
        "bar_end", "open", "high", "low", "close", "contract_code",
        "exchange_trade_date",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("minute bars are missing: " + ",".join(missing))
    result = frame.copy()
    result["bar_end"] = pd.to_datetime(result["bar_end"], errors="raise")
    if result["bar_end"].dt.tz is None:
        raise ValueError("minute bar_end must be timezone-aware")
    result = result.loc[
        result["exchange_trade_date"].map(lambda value: start <= value <= end)
    ].sort_values("bar_end", kind="stable").reset_index(drop=True)
    if result.duplicated("bar_end").any():
        raise ValueError("minute bar_end must be unique for one root")
    return result


def _validated_roll_minutes(
    frame: pd.DataFrame | None,
    *,
    start: date,
    end: date,
) -> pd.DataFrame:
    if frame is None or frame.empty:
        return pd.DataFrame(
            columns=[
                "bar_end",
                "open",
                "high",
                "low",
                "close",
                "contract_code",
                "exchange_trade_date",
            ]
        )
    required = {
        "bar_end", "open", "high", "low", "close", "contract_code",
        "exchange_trade_date",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("roll execution bars are missing: " + ",".join(missing))
    result = frame.copy()
    result["bar_end"] = pd.to_datetime(result["bar_end"], errors="raise")
    if result["bar_end"].dt.tz is None:
        raise ValueError("roll execution bar_end must be timezone-aware")
    result = result.loc[
        result["exchange_trade_date"].map(lambda value: start <= value <= end)
    ].sort_values(["bar_end", "contract_code"], kind="stable").reset_index(drop=True)
    if result.duplicated(["contract_code", "bar_end"]).any():
        raise ValueError("roll execution bars must be unique by contract and bar_end")
    return result


def _roll_bar_lookup(
    frame: pd.DataFrame,
) -> dict[tuple[pd.Timestamp, str], pd.Series]:
    return {
        (pd.Timestamp(row["bar_end"]), str(row["contract_code"])): row
        for _, row in frame.iterrows()
    }


def _blocked_roll_execution_bar(
    old_contract: str,
    new_contract: str,
) -> BlockedMetadataError:
    return BlockedMetadataError(
        "BLOCKED_ROLL_EXECUTION_BAR: no executable old-contract bar for "
        f"{old_contract} after {new_contract} became observable",
        "BLOCKED_ROLL_EXECUTION_BAR",
    )


def _first_trend_entry_breakout_buffer_reason(
    candidate: dict[str, Any],
    filled_bull_trend_ids: set[int],
    config: MultiTimeframeTrendConfig,
) -> str:
    ratio = config.first_trend_entry_daily_breakout_buffer_ratio
    if ratio <= 0:
        return ""
    try:
        direction = int(candidate["direction"])
        trend_id = int(candidate["daily_bull_trend_id"])
        trigger = float(candidate["trigger"])
        prior_5d_high = float(candidate["prior_5d_high"])
    except (KeyError, OverflowError, TypeError, ValueError):
        return "FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET"
    if direction <= 0 or trend_id in filled_bull_trend_ids:
        return ""
    if (
        not math.isfinite(trigger)
        or not math.isfinite(prior_5d_high)
        or trigger <= prior_5d_high * (1.0 + ratio)
    ):
        return "FIRST_TREND_ENTRY_DAILY_BREAKOUT_BUFFER_NOT_MET"
    return ""


def _is_first_trade_in_trend_segment(
    candidate: dict[str, Any],
    filled_bull_trend_ids: set[int],
) -> int:
    try:
        direction = int(candidate["direction"])
        trend_id = int(candidate["daily_bull_trend_id"])
    except (KeyError, OverflowError, TypeError, ValueError):
        return 0
    return int(direction > 0 and trend_id not in filled_bull_trend_ids)


def _record_bull_trend_fill(
    candidate: dict[str, Any],
    filled_bull_trend_ids: set[int],
) -> None:
    try:
        direction = int(candidate["direction"])
        trend_id = int(candidate["daily_bull_trend_id"])
    except (KeyError, OverflowError, TypeError, ValueError):
        return
    if direction > 0:
        filled_bull_trend_ids.add(trend_id)


def _validated_candidates(frame: pd.DataFrame) -> pd.DataFrame:
    required = {
        "candidate_id", "contract_code", "setup_type", "direction", "signal_time",
        "active_time", "expires_at", "trigger", "stop_price", "filtered_reason",
    }
    missing = sorted(required.difference(frame.columns))
    if missing:
        raise ValueError("candidates are missing: " + ",".join(missing))
    result = frame.copy()
    for column in ("signal_time", "active_time", "expires_at"):
        result[column] = pd.to_datetime(result[column], errors="raise")
        if result[column].dt.tz is None:
            raise ValueError(f"candidate {column} must be timezone-aware")
    if result["candidate_id"].astype(str).duplicated().any():
        raise ValueError("candidate_id must be unique")
    return result.sort_values(["signal_time", "candidate_id"], kind="stable")


def _context_lookup(frame: pd.DataFrame) -> dict[pd.Timestamp, dict[str, Any]]:
    if frame.empty:
        return {}
    if not {"bar_end", "daily_direction"}.issubset(frame):
        raise ValueError("five-minute context requires bar_end and daily_direction")
    result = frame.copy()
    result["bar_end"] = pd.to_datetime(result["bar_end"], errors="raise")
    if result["bar_end"].dt.tz is None:
        raise ValueError("five-minute context bar_end must be timezone-aware")
    return {
        pd.Timestamp(row["bar_end"]): row
        for row in result.to_dict("records")
    }


def _submit_candidate(
    candidate: dict[str, Any],
    *,
    root_symbol: str,
    exchange: str,
    metadata_store: Any,
    config: MultiTimeframeTrendConfig,
    equity: float,
) -> _PendingOrder:
    signal_time = pd.Timestamp(candidate["signal_time"])
    active_time = pd.Timestamp(candidate["active_time"])
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
        order_event=active_time.to_pydatetime(),
    )
    entry = float(candidate["trigger"])
    stop = float(candidate["stop_price"])
    cost = _round_trip_cost_cash(metadata)
    risk = size_for_risk(
        equity=equity,
        entry=entry,
        stop=stop,
        multiplier=float(metadata.contract_size),
        stressed_round_trip_cost=cost,
        risk_per_trade=config.risk_per_trade,
    )
    if risk.quantity < 1:
        raise _PlanRejected("RISK_BELOW_ONE_LOT")
    margin_rate = max(
        float(metadata.daily.margin_rate_long),
        float(metadata.daily.margin_rate_short),
    )
    margin_lots = math.floor(
        equity / (entry * float(metadata.contract_size) * margin_rate)
    )
    quantity = min(risk.quantity, margin_lots)
    if quantity < 1:
        raise _PlanRejected("INSUFFICIENT_MARGIN")
    return _PendingOrder(
        candidate=candidate,
        metadata=metadata,
        order_id=f"ORDER-{candidate['candidate_id']}",
        quantity=quantity,
        risk_budget=risk.risk_budget,
        loss_per_lot=risk.loss_per_lot,
    )


def _match_entry(
    pending: _PendingOrder,
    bar: pd.Series,
    *,
    config: MultiTimeframeTrendConfig,
    equity: float,
) -> tuple[str, str, float, float, int]:
    candidate = pending.candidate
    direction = int(candidate["direction"])
    trigger = float(candidate["trigger"])
    stop = float(candidate["stop_price"])
    tick = float(pending.metadata.price_tick)
    setup = str(candidate["setup_type"])
    if setup == "pullback_breakout":
        invalidation = stop + tick if direction > 0 else stop - tick
        invalidated = (
            float(bar["low"]) <= invalidation
            if direction > 0
            else float(bar["high"]) >= invalidation
        )
        if invalidated:
            return "CANCELLED", "PULLBACK_RANGE_INVALIDATED", math.nan, math.nan, 0
    touched = (
        float(bar["high"]) >= trigger
        if direction > 0
        else float(bar["low"]) <= trigger
    )
    if not touched:
        return "PENDING", "", math.nan, math.nan, 0
    if _entry_limit_locked(bar, pending.metadata, direction):
        return "PENDING", "LIMIT_OR_LIQUIDITY_BLOCKED", math.nan, math.nan, 0
    reference = (
        max(float(bar["open"]), trigger)
        if direction > 0
        else min(float(bar["open"]), trigger)
    )
    raw_fill = reference + direction * (
        float(pending.metadata.stressed_entry_slippage_ticks) * tick
    )
    fill = _round_adverse(raw_fill, tick, direction)
    if direction * (fill - stop) <= 0:
        return "CANCELLED", "GAP_INVALID_STRUCTURAL_STOP", math.nan, math.nan, 0
    cost = _round_trip_cost_cash(pending.metadata)
    risk = size_for_risk(
        equity=equity,
        entry=fill,
        stop=stop,
        multiplier=float(pending.metadata.contract_size),
        stressed_round_trip_cost=cost,
        risk_per_trade=config.risk_per_trade,
    )
    quantity = min(pending.quantity, risk.quantity)
    if quantity < 1:
        return "CANCELLED", "RISK_BELOW_ONE_LOT", math.nan, math.nan, 0
    return "FILLED", "FILLED", fill, reference, quantity


def _open_position(
    pending: _PendingOrder,
    *,
    timestamp: pd.Timestamp,
    bar_index: int,
    fill_price: float,
    reference: float,
    quantity: int,
    base_quantity: int,
    symbol_quantity_scale: float,
    portfolio_quantity_scale: float,
    quantity_scale: float,
    position_scaling_reason: str,
    symbol_recovery_deficit: float,
    portfolio_recovery_deficit: float,
    is_first_trade_in_trend_segment: int,
    config: MultiTimeframeTrendConfig,
    opened_via_chase_gate: bool = False,
) -> tuple[_Position, dict[str, Any]]:
    direction = int(pending.candidate["direction"])
    stop = float(pending.candidate["stop_price"])
    target = two_r_target(
        fill_price,
        stop,
        direction,
        config.pullback_target_r,
    )
    risk_cash = quantity * (
        abs(fill_price - stop) * float(pending.metadata.contract_size)
        + _round_trip_cost_cash(pending.metadata)
    )
    try:
        trigger = float(pending.candidate["trigger"])
        prior_5d_high = float(pending.candidate["prior_5d_high"])
    except (KeyError, TypeError, ValueError):
        trigger_to_prior_5d_high_ratio = math.nan
    else:
        trigger_to_prior_5d_high_ratio = (
            100.0 * (trigger / prior_5d_high - 1.0)
            if math.isfinite(trigger)
            and math.isfinite(prior_5d_high)
            and prior_5d_high > 0
            else math.nan
        )
    position = _Position(
        pending=pending,
        quantity=quantity,
        initial_quantity=quantity,
        entry_time=timestamp,
        entry_price=fill_price,
        entry_reference=reference,
        stop=stop,
        target=target,
        initial_risk_cash=risk_cash,
        entry_bar_index=bar_index,
        maximum_favorable_price=fill_price,
        maximum_adverse_price=fill_price,
        current_metadata=pending.metadata,
        base_quantity=base_quantity,
        symbol_quantity_scale=symbol_quantity_scale,
        portfolio_quantity_scale=portfolio_quantity_scale,
        quantity_scale=quantity_scale,
        position_scaling_reason=position_scaling_reason,
        symbol_recovery_deficit_at_entry=symbol_recovery_deficit,
        portfolio_recovery_deficit_at_entry=portfolio_recovery_deficit,
        is_first_trade_in_trend_segment=is_first_trade_in_trend_segment,
        trigger_to_prior_5d_high_ratio=trigger_to_prior_5d_high_ratio,
        opened_via_chase_gate=bool(opened_via_chase_gate),
    )
    return position, _fill_row(
        pending,
        kind="ENTRY",
        timestamp=timestamp,
        quantity=quantity,
        price=fill_price,
        reference=reference,
        reason="FILLED",
    )


def _protective_exit_touched(position: _Position, bar: pd.Series) -> bool:
    direction = int(position.pending.candidate["direction"])
    stop_hit = (
        float(bar["low"]) <= position.stop
        if direction > 0
        else float(bar["high"]) >= position.stop
    )
    return bool(stop_hit)


def _protective_exit(
    position: _Position,
    bar: pd.Series,
    *,
    metadata: Any,
) -> tuple[str, float, float]:
    direction = int(position.pending.candidate["direction"])
    stop_hit = (
        float(bar["low"]) <= position.stop
        if direction > 0
        else float(bar["high"]) >= position.stop
    )
    if stop_hit:
        if _protective_limit_locked(bar, metadata, direction):
            return "", math.nan, math.nan
        reference = (
            min(float(bar["open"]), position.stop)
            if direction > 0
            else max(float(bar["open"]), position.stop)
        )
        return "STOP", _market_exit_price(position, reference, metadata), reference
    return "", math.nan, math.nan


def _market_exit_price(
    position: _Position,
    reference: float,
    metadata: Any | None = None,
) -> float:
    direction = int(position.pending.candidate["direction"])
    effective_metadata = metadata or position.pending.metadata
    remaining_ticks = max(
        0.0,
        float(effective_metadata.stressed_round_trip_slippage_ticks)
        - float(position.pending.metadata.stressed_entry_slippage_ticks),
    )
    raw = reference - direction * remaining_ticks * float(effective_metadata.price_tick)
    return _round_adverse(raw, float(effective_metadata.price_tick), -direction)


def _close_position(
    position: _Position,
    *,
    quantity: int | None = None,
    exit_metadata: Any,
    timestamp: pd.Timestamp,
    price: float,
    reference: float,
    reason: str,
    bar_index: int,
) -> tuple[dict[str, Any] | None, dict[str, Any], dict[str, Any]]:
    candidate = position.pending.candidate
    direction = int(candidate["direction"])
    multiplier = float(position.pending.metadata.contract_size)
    close_quantity = position.quantity if quantity is None else int(quantity)
    if close_quantity < 1 or close_quantity > position.quantity:
        raise ValueError("close quantity must be within the open position")
    gross = direction * (price - position.entry_price) * close_quantity * multiplier
    entry_metadata = position.pending.metadata
    same_trade_date = (
        entry_metadata.daily.exchange_trade_date
        == exit_metadata.daily.exchange_trade_date
    )
    close_type = "CLOSE_TODAY" if same_trade_date else "CLOSE"
    entry_fee_rate = float(entry_metadata.open_fee_rate)
    entry_fixed_fee = float(entry_metadata.fee_per_lot_open)
    if same_trade_date:
        exit_fee_rate = float(exit_metadata.close_today_fee_rate)
        exit_fixed_fee = float(exit_metadata.fee_per_lot_close_today)
    else:
        exit_fee_rate = float(exit_metadata.close_fee_rate)
        exit_fixed_fee = float(exit_metadata.fee_per_lot_close)
    entry_stress = float(entry_metadata.fee_stress_multiplier)
    exit_stress = float(exit_metadata.fee_stress_multiplier)
    open_fee = (
        position.entry_price * multiplier * entry_fee_rate + entry_fixed_fee
    ) * entry_stress * close_quantity
    close_fee = (
        price * multiplier * exit_fee_rate + exit_fixed_fee
    ) * exit_stress * close_quantity
    fees = open_fee + close_fee
    slippage = (
        abs(position.entry_price - position.entry_reference)
        + abs(price - reference)
    ) * close_quantity * multiplier
    net = gross - fees
    is_final_exit = close_quantity == position.quantity
    leg = {
        "candidate_id": candidate["candidate_id"],
        "contract_code": candidate["contract_code"],
        "symbol": candidate.get("symbol", ""),
        "exit_sequence": len(position.exit_legs) + 1,
        "is_final_exit": int(is_final_exit),
        "reason": reason,
        "quantity": close_quantity,
        "entry_time": position.entry_time,
        "exit_time": timestamp,
        "entry_price": position.entry_price,
        "exit_price": price,
        "reference_price": reference,
        "gross_pnl": gross,
        "open_fee": open_fee,
        "close_fee": close_fee,
        "fees": fees,
        "slippage": slippage,
        "net_pnl": net,
        "turnover": (
            position.entry_price + price
        ) * close_quantity * multiplier,
        "close_type": close_type,
        "entry_fee_rate": entry_fee_rate,
        "exit_fee_rate": exit_fee_rate,
        "entry_fixed_fee": entry_fixed_fee,
        "exit_fixed_fee": exit_fixed_fee,
        "fee_stress_multiplier": max(entry_stress, exit_stress),
        "entry_fee_schedule_id": entry_metadata.daily.fee_schedule_id,
        "exit_fee_schedule_id": exit_metadata.daily.fee_schedule_id,
        "entry_metadata_hash": getattr(entry_metadata, "metadata_hash", ""),
        "exit_metadata_hash": getattr(exit_metadata, "metadata_hash", ""),
        "contract_multiplier": multiplier,
        "entry_fee_source": entry_metadata.fee_source,
        "exit_fee_source": exit_metadata.fee_source,
        "entry_fee_effective_from": entry_metadata.fee_effective_from,
        "exit_fee_effective_from": exit_metadata.fee_effective_from,
        "entry_fee_known_at": entry_metadata.fee_known_at,
        "exit_fee_known_at": exit_metadata.fee_known_at,
    }
    position.exit_legs.append(leg)
    position.quantity -= close_quantity
    fill = _fill_row(
        position.pending,
        kind="EXIT",
        timestamp=timestamp,
        quantity=close_quantity,
        price=price,
        reference=reference,
        reason=reason,
    )
    if not is_final_exit:
        return None, fill, leg

    legs = position.exit_legs
    total_quantity = sum(int(item["quantity"]) for item in legs)
    weighted_exit = sum(
        float(item["exit_price"]) * int(item["quantity"]) for item in legs
    ) / total_quantity
    gross = sum(float(item["gross_pnl"]) for item in legs)
    open_fee = sum(float(item["open_fee"]) for item in legs)
    close_fee = sum(float(item["close_fee"]) for item in legs)
    fees = sum(float(item["fees"]) for item in legs)
    slippage = sum(float(item["slippage"]) for item in legs)
    net = sum(float(item["net_pnl"]) for item in legs)
    turnover = sum(float(item["turnover"]) for item in legs)
    favorable = direction * (position.maximum_favorable_price - position.entry_price)
    adverse = direction * (position.entry_price - position.maximum_adverse_price)
    trade = {
        "candidate_id": candidate["candidate_id"],
        "contract_code": candidate["contract_code"],
        "direction": direction,
        "quantity": position.initial_quantity,
        "entry_time": position.entry_time,
        "exit_time": timestamp,
        "entry_price": position.entry_price,
        "exit_price": weighted_exit,
        "gross_pnl": gross,
        "fees": fees,
        "slippage": slippage,
        "net_pnl": net,
        "symbol": candidate.get("symbol", ""),
        "sector": sector_for_root(candidate.get("symbol", "")),
        "setup": candidate["setup_type"],
        "cycle": "BULL" if direction > 0 else "BEAR",
        "is_first_trade_in_trend_segment": (
            position.is_first_trade_in_trend_segment
        ),
        "trigger_to_prior_5d_high_ratio": (
            position.trigger_to_prior_5d_high_ratio
        ),
        "net_r": net / position.initial_risk_cash,
        "mfe_r": favorable * position.initial_quantity * multiplier / position.initial_risk_cash,
        "mae_r": adverse * position.initial_quantity * multiplier / position.initial_risk_cash,
        "holding_bars": max(0, bar_index - position.entry_bar_index),
        "turnover": turnover,
        "exit_reason": reason,
        "final_target": position.target,
        "target_exit_enabled": 0,
        "had_overnight_reduction": int(
            any(item["reason"] == "OVERNIGHT_MARGIN_REDUCTION" for item in legs)
        ),
        "overnight_reduced_quantity": sum(
            int(item["quantity"])
            for item in legs
            if item["reason"] == "OVERNIGHT_MARGIN_REDUCTION"
        ),
        "base_quantity": position.base_quantity,
        "symbol_quantity_scale": position.symbol_quantity_scale,
        "portfolio_quantity_scale": position.portfolio_quantity_scale,
        "quantity_scale": position.quantity_scale,
        "position_scaling_reason": position.position_scaling_reason,
        "symbol_recovery_deficit_at_entry": (
            position.symbol_recovery_deficit_at_entry
        ),
        "portfolio_recovery_deficit_at_entry": (
            position.portfolio_recovery_deficit_at_entry
        ),
        "open_fee": open_fee,
        "close_fee": close_fee,
        "close_type": _mixed_text(legs, "close_type"),
        "entry_fee_rate": entry_fee_rate,
        "exit_fee_rate": _mixed_number(legs, "exit_fee_rate"),
        "entry_fixed_fee": entry_fixed_fee,
        "exit_fixed_fee": _mixed_number(legs, "exit_fixed_fee"),
        "fee_stress_multiplier": max(
            float(item["fee_stress_multiplier"]) for item in legs
        ),
        "entry_fee_schedule_id": entry_metadata.daily.fee_schedule_id,
        "exit_fee_schedule_id": _mixed_text(legs, "exit_fee_schedule_id"),
        "entry_metadata_hash": getattr(entry_metadata, "metadata_hash", ""),
        "exit_metadata_hash": _mixed_text(legs, "exit_metadata_hash"),
        "contract_multiplier": multiplier,
        "entry_fee_source": entry_metadata.fee_source,
        "exit_fee_source": _mixed_text(legs, "exit_fee_source"),
        "entry_fee_effective_from": entry_metadata.fee_effective_from,
        "exit_fee_effective_from": _mixed_text(legs, "exit_fee_effective_from"),
        "entry_fee_known_at": entry_metadata.fee_known_at,
        "exit_fee_known_at": _mixed_text(legs, "exit_fee_known_at"),
    }
    return trade, fill, leg


def _mixed_text(rows: list[dict[str, Any]], column: str) -> Any:
    values = {str(row[column]) for row in rows}
    return rows[0][column] if len(values) == 1 else "MIXED"


def _mixed_number(rows: list[dict[str, Any]], column: str) -> float:
    values = {float(row[column]) for row in rows}
    return values.pop() if len(values) == 1 else math.nan


def _advance_position_stop(
    position: _Position,
    context: dict[str, Any],
    *,
    config: MultiTimeframeTrendConfig,
) -> None:
    direction = int(position.pending.candidate["direction"])
    kind = "low" if direction > 0 else "high"
    swing = context.get(f"latest_swing_{kind}")
    atr_value = context.get("atr14")
    known_at = context.get(f"latest_swing_{kind}_known_at")
    if any(pd.isna(value) for value in (swing, atr_value, known_at)):
        return
    position.stop = advance_trailing_stop(
        current_stop=position.stop,
        direction=direction,
        confirmed_swing=float(swing),
        atr_value=float(atr_value),
        buffer_atr=config.trailing_buffer_atr,
        tick_size=float(position.pending.metadata.price_tick),
    )


def _update_excursions(position: _Position, bar: pd.Series) -> None:
    direction = int(position.pending.candidate["direction"])
    if direction > 0:
        position.maximum_favorable_price = max(
            position.maximum_favorable_price, float(bar["high"])
        )
        position.maximum_adverse_price = min(
            position.maximum_adverse_price, float(bar["low"])
        )
    else:
        position.maximum_favorable_price = min(
            position.maximum_favorable_price, float(bar["low"])
        )
        position.maximum_adverse_price = max(
            position.maximum_adverse_price, float(bar["high"])
        )


def _marked_equity(cash: float, position: _Position | None, bar: pd.Series) -> float:
    if position is None:
        return cash
    direction = int(position.pending.candidate["direction"])
    multiplier = float(position.pending.metadata.contract_size)
    unrealized = (
        direction
        * (float(bar["close"]) - position.entry_price)
        * position.quantity
        * multiplier
    )
    fees = float(position.pending.metadata.stressed_round_trip_fee_cash) * position.quantity
    return cash + unrealized - fees


def _equity_row(
    *,
    date_value: date,
    cash: float,
    position: _Position | None,
    mark: float,
) -> dict[str, Any]:
    if position is None:
        return {
            "date": date_value,
            "equity": cash,
            "cash": cash,
            "unrealized_pnl": 0.0,
            "margin_used": 0.0,
            "margin_utilization": 0.0,
            "open_risk": 0.0,
            "peak_margin_utilization": 0.0,
            "peak_overnight_margin_utilization": 0.0,
            "peak_symbol_margin_utilization": 0.0,
            "max_concurrent_positions": 0,
        }
    direction = int(position.pending.candidate["direction"])
    multiplier = float(position.pending.metadata.contract_size)
    unrealized = direction * (mark - position.entry_price) * position.quantity * multiplier
    fees = float(position.pending.metadata.stressed_round_trip_fee_cash) * position.quantity
    equity = cash + unrealized - fees
    margin_rate = (
        float(position.current_metadata.daily.margin_rate_long)
        if direction > 0
        else float(position.current_metadata.daily.margin_rate_short)
    )
    margin = position.entry_price * position.quantity * multiplier * margin_rate
    open_risk = position.quantity * (
        abs(mark - position.stop) * multiplier
        + _round_trip_cost_cash(position.pending.metadata)
    )
    utilization = margin / equity if equity > 0 else math.nan
    return {
        "date": date_value,
        "equity": equity,
        "cash": cash,
        "unrealized_pnl": unrealized - fees,
        "margin_used": margin,
        "margin_utilization": utilization,
        "open_risk": open_risk,
        "peak_margin_utilization": utilization,
        "peak_overnight_margin_utilization": 0.0,
        "peak_symbol_margin_utilization": utilization,
        "max_concurrent_positions": 1,
    }


def _entry_limit_locked(bar: pd.Series, metadata: Any, direction: int) -> bool:
    level = float(metadata.limit_up if direction > 0 else metadata.limit_down)
    values = [float(bar[name]) for name in ("open", "high", "low", "close")]
    return all(np.isclose(value, level) for value in values)


def _protective_limit_locked(
    bar: pd.Series,
    metadata: Any,
    position_direction: int,
) -> bool:
    level = float(
        metadata.limit_down if position_direction > 0 else metadata.limit_up
    )
    values = [float(bar[name]) for name in ("open", "high", "low", "close")]
    return all(np.isclose(value, level) for value in values)


def _exit_metadata_snapshot(
    metadata_store: Any,
    *,
    root_symbol: str,
    exchange: str,
    position: _Position,
    bar: pd.Series,
) -> Any:
    timestamp = pd.Timestamp(bar["bar_end"])
    metadata = metadata_store.execution_snapshot(
        root_symbol=root_symbol,
        exchange=exchange,
        contract_code=str(position.pending.candidate["contract_code"]),
        exchange_trade_date=bar["exchange_trade_date"],
        gateway=BACKTEST_GATEWAY,
        order_types=("STOP",),
        decision_asof=timestamp.to_pydatetime(),
        order_event=timestamp.to_pydatetime(),
    )
    entry_metadata = position.pending.metadata
    if not (
        np.isclose(float(metadata.contract_size), float(entry_metadata.contract_size))
        and np.isclose(float(metadata.price_tick), float(entry_metadata.price_tick))
    ):
        raise BlockedMetadataError(
            "held contract tick or multiplier changed before exit",
            "BLOCKED_METADATA",
        )
    return metadata


def _round_trip_cost_cash(metadata: Any) -> float:
    return float(metadata.stressed_round_trip_fee_cash) + (
        float(metadata.stressed_round_trip_slippage_ticks)
        * float(metadata.price_tick)
        * float(metadata.contract_size)
    )


def _round_adverse(value: float, tick: float, order_direction: int) -> float:
    units = value / tick
    rounded = math.ceil(units - 1e-12) if order_direction > 0 else math.floor(units + 1e-12)
    return float(rounded * tick)


def _plan_row(pending: _PendingOrder) -> dict[str, Any]:
    candidate = pending.candidate
    direction = int(candidate["direction"])
    target = float(candidate.get("target_price_virtual", math.nan))
    if not math.isfinite(target):
        target = two_r_target(
            float(candidate["trigger"]),
            float(candidate["stop_price"]),
            direction,
            2.0,
        )
    return {
        "candidate_id": candidate["candidate_id"],
        "order_id": pending.order_id,
        "symbol": candidate.get("symbol", ""),
        "contract_code": candidate["contract_code"],
        "setup": candidate["setup_type"],
        "direction": direction,
        "signal_time": candidate["signal_time"],
        "active_time": candidate["active_time"],
        "expires_at": candidate["expires_at"],
        "entry": candidate["trigger"],
        "stop": candidate["stop_price"],
        "target": target,
        "quantity": pending.quantity,
        "risk_budget": pending.risk_budget,
        "loss_per_lot": pending.loss_per_lot,
        "metadata_hash": getattr(pending.metadata, "metadata_hash", ""),
    }


def _order_row(
    pending: _PendingOrder,
    status: str,
    reason: str,
    event_time: object,
) -> dict[str, Any]:
    return {
        "order_id": pending.order_id,
        "candidate_id": pending.candidate["candidate_id"],
        "contract_code": pending.candidate["contract_code"],
        "status": status,
        "reason": reason,
        "event_time": event_time,
    }


def _fill_row(
    pending: _PendingOrder,
    *,
    kind: str,
    timestamp: object,
    quantity: int,
    price: float,
    reference: float,
    reason: str,
) -> dict[str, Any]:
    return {
        "order_id": pending.order_id,
        "candidate_id": pending.candidate["candidate_id"],
        "contract_code": pending.candidate["contract_code"],
        "fill_kind": kind,
        "fill_time": timestamp,
        "quantity": quantity,
        "price": price,
        "reference_price": reference,
        "reason": reason,
    }


def _rejection(
    root_symbol: str,
    candidate: dict[str, Any],
    reason: str,
    *,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "root_symbol": root_symbol,
        "feature_asof": candidate["signal_time"],
        "reason_code": reason,
        "candidate_id": candidate["candidate_id"],
        "risk_budget": math.nan,
        "loss_per_lot": math.nan,
        "detail": detail,
    }


def _portfolio_risk_event(
    timestamp: pd.Timestamp,
    reason: str,
    *,
    detail: str = "",
) -> dict[str, Any]:
    return {
        "root_symbol": "ALL",
        "feature_asof": timestamp,
        "reason_code": reason,
        "candidate_id": "",
        "risk_budget": math.nan,
        "loss_per_lot": math.nan,
        "detail": detail,
    }


__all__ = [
    "DAILY_EQUITY_COLUMNS",
    "FILL_COLUMNS",
    "FORWARD_RETURN_HORIZONS",
    "MARKET_LIQUIDITY_COLUMNS",
    "MARKET_LIQUIDITY_WINDOWS",
    "ORDER_COLUMNS",
    "PLAN_COLUMNS",
    "PortfolioReplayInput",
    "REJECTION_COLUMNS",
    "SCALING_EVENT_COLUMNS",
    "TRADE_COLUMNS",
    "TRADE_RETURN_COLUMNS",
    "ReplayArtifacts",
    "replay_trend_portfolio",
    "replay_trend_strategy",
]
