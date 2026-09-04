"""Fail-closed pre-trade sizing and risk policy for rule-only scalp orders."""
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import date, datetime, timedelta
import math
from types import MappingProxyType
from typing import Any

import pandas as pd

from cta.live.risk import RiskContext
from cta.risk.base import SignalContext
from cta.risk.guards.config import LiquidityFloorGuardConfig
from cta.risk.guards.liquidity_floor_guard import LiquidityFloorGuard
from cta.risk.sizing.config import (
    DailyVaRBudgetConfig,
    ExecutionQualityConfig,
    VolatilityRegimeScalerConfig,
)
from cta.risk.sizing.daily_var_budget import DailyVaRBudgetSizer
from cta.risk.sizing.execution_quality_scaler import ExecutionQualityScaler
from cta.risk.sizing.volatility_regime_scaler import VolatilityRegimeScaler

from .config import ScalpConfig


@dataclass(frozen=True)
class AccountSnapshot:
    marked_equity: float
    margin_used_and_reserved: float
    portfolio_open_risk: float
    symbol_open_risk: Mapping[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not _positive(self.marked_equity):
            raise ValueError("marked_equity must be finite and positive")
        for name in ("margin_used_and_reserved", "portfolio_open_risk"):
            value = float(getattr(self, name))
            if not _nonnegative(value):
                raise ValueError(f"{name} must be finite and nonnegative")
        normalized = {str(key): float(value) for key, value in self.symbol_open_risk.items()}
        if any(not _nonnegative(value) for value in normalized.values()):
            raise ValueError("symbol_open_risk values must be finite and nonnegative")
        object.__setattr__(self, "symbol_open_risk", MappingProxyType(normalized))

    def to_dict(self) -> dict[str, Any]:
        return {
            "marked_equity": self.marked_equity,
            "margin_used_and_reserved": self.margin_used_and_reserved,
            "portfolio_open_risk": self.portfolio_open_risk,
            "symbol_open_risk": dict(self.symbol_open_risk),
        }


@dataclass(frozen=True)
class RiskSnapshot:
    current_risk_pct: float
    session_return: float
    trade_date_return: float
    peak_profit: float
    giveback_cny: float
    drawdown_pct: float
    symbol_consecutive_losses: int
    portfolio_consecutive_losses: int
    cooldown_until: datetime | None
    symbol_stopped: bool
    portfolio_stopped: bool
    daily_locked: bool

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["cooldown_until"] = (
            self.cooldown_until.isoformat() if self.cooldown_until is not None else None
        )
        return payload


class PortfolioRiskState:
    """Mutable single-writer state behind immutable per-symbol risk snapshots."""

    def __init__(
        self,
        config: ScalpConfig,
        *,
        symbols: tuple[str, ...],
    ) -> None:
        if not symbols or len(set(symbols)) != len(symbols):
            raise ValueError("symbols must be non-empty and unique")
        self.config = config
        self.symbols = tuple(symbols)
        self._trade_date: date | None = None
        self._trade_date_start_equity = float(config.account.initial_equity)
        self._trade_date_peak_equity = float(config.account.initial_equity)
        self._marked_equity = float(config.account.initial_equity)
        self._historical_peak_equity = float(config.account.initial_equity)
        self._session_ids: dict[str, str] = {}
        self._session_start_equity = {
            symbol: float(config.account.initial_equity) for symbol in symbols
        }
        self._symbol_consecutive_losses = {symbol: 0 for symbol in symbols}
        self._portfolio_consecutive_losses = 0
        self._cooldown_until: dict[str, datetime | None] = {
            symbol: None for symbol in symbols
        }
        self._symbol_stopped = {symbol: False for symbol in symbols}
        self._portfolio_loss_stopped = False
        self._drawdown_stop_sessions_remaining = 0
        self._drawdown_stop_armed = False
        self._hard_kill = False
        self._account_session_key: tuple[str, ...] = ()
        self._daily_locked = False

    @property
    def trade_date_start_equity(self) -> float:
        return self._trade_date_start_equity

    def update_mark(
        self,
        *,
        now: datetime,
        exchange_trade_date: date,
        marked_equity: float,
        session_ids: Mapping[str, str],
    ) -> bool:
        if pd.Timestamp(now).tzinfo is None:
            raise ValueError("risk mark timestamp must be timezone-aware")
        if not _positive(marked_equity):
            raise ValueError("marked_equity must be finite and positive")
        unknown = set(session_ids).difference(self.symbols)
        if unknown:
            raise ValueError(f"unknown risk symbols: {sorted(unknown)}")
        if self._trade_date is None or exchange_trade_date != self._trade_date:
            self._reset_trade_date(exchange_trade_date, float(marked_equity))
        self._marked_equity = float(marked_equity)
        self._historical_peak_equity = max(self._historical_peak_equity, self._marked_equity)
        self._trade_date_peak_equity = max(self._trade_date_peak_equity, self._marked_equity)
        session_key = tuple(sorted(str(value) for value in session_ids.values()))
        if (
            self._account_session_key
            and session_key != self._account_session_key
            and self._drawdown_stop_sessions_remaining > 0
        ):
            self._drawdown_stop_sessions_remaining -= 1
        self._account_session_key = session_key
        for symbol, session_id in session_ids.items():
            if self._session_ids.get(symbol) != session_id:
                self._session_ids[symbol] = str(session_id)
                self._session_start_equity[symbol] = self._marked_equity

        risk = self.config.risk
        trade_date_return = self._marked_equity / self._trade_date_start_equity - 1.0
        peak_profit = self._trade_date_peak_equity / self._trade_date_start_equity - 1.0
        giveback = self._trade_date_peak_equity - self._marked_equity
        if trade_date_return <= -risk.daily_hard_loss_pct:
            self._daily_locked = True
        if (
            peak_profit >= risk.profit_lock_activation_pct
            and giveback >= risk.profit_giveback_pct * self._trade_date_start_equity
        ):
            self._daily_locked = True
        if self._marked_equity <= self._historical_peak_equity * (
            1.0 - risk.drawdown_kill_pct
        ):
            self._hard_kill = True
        if self._marked_equity <= self._historical_peak_equity * (
            1.0 - risk.drawdown_stop_pct
        ):
            if not self._drawdown_stop_armed:
                self._drawdown_stop_armed = True
                self._drawdown_stop_sessions_remaining = risk.drawdown_stop_sessions
        else:
            self._drawdown_stop_armed = False
        return self._daily_locked or self._is_portfolio_stopped()

    def record_closed_trade(
        self,
        symbol: str,
        net_pnl: float,
        exit_time: datetime,
    ) -> None:
        if symbol not in self._symbol_consecutive_losses:
            raise ValueError(f"unknown risk symbol: {symbol}")
        if pd.Timestamp(exit_time).tzinfo is None:
            raise ValueError("exit_time must be timezone-aware")
        if not math.isfinite(float(net_pnl)):
            raise ValueError("net_pnl must be finite")
        if net_pnl < 0:
            self._symbol_consecutive_losses[symbol] += 1
            self._portfolio_consecutive_losses += 1
            if (
                self._symbol_consecutive_losses[symbol]
                >= self.config.risk.symbol_loss_cooldown_after
            ):
                self._cooldown_until[symbol] = exit_time + timedelta(
                    minutes=self.config.risk.cooldown_minutes
                )
            if self._symbol_consecutive_losses[symbol] >= self.config.risk.symbol_stop_after:
                self._symbol_stopped[symbol] = True
            if self._portfolio_consecutive_losses >= self.config.risk.portfolio_stop_after:
                self._portfolio_loss_stopped = True
        else:
            self._symbol_consecutive_losses[symbol] = 0
            self._portfolio_consecutive_losses = 0
            self._cooldown_until[symbol] = None

    def snapshot(self, symbol: str) -> RiskSnapshot:
        if symbol not in self._symbol_consecutive_losses:
            raise ValueError(f"unknown risk symbol: {symbol}")
        session_start = self._session_start_equity[symbol]
        session_return = self._marked_equity / session_start - 1.0
        trade_date_return = self._marked_equity / self._trade_date_start_equity - 1.0
        peak_profit = self._trade_date_peak_equity / self._trade_date_start_equity - 1.0
        drawdown = 1.0 - self._marked_equity / self._historical_peak_equity
        return RiskSnapshot(
            current_risk_pct=self.config.account.base_risk_pct,
            session_return=session_return,
            trade_date_return=trade_date_return,
            peak_profit=peak_profit,
            giveback_cny=self._trade_date_peak_equity - self._marked_equity,
            drawdown_pct=drawdown,
            symbol_consecutive_losses=self._symbol_consecutive_losses[symbol],
            portfolio_consecutive_losses=self._portfolio_consecutive_losses,
            cooldown_until=self._cooldown_until[symbol],
            symbol_stopped=self._symbol_stopped[symbol],
            portfolio_stopped=self._is_portfolio_stopped(),
            daily_locked=self._daily_locked,
        )

    def _is_portfolio_stopped(self) -> bool:
        return (
            self._portfolio_loss_stopped
            or self._drawdown_stop_sessions_remaining > 0
            or self._hard_kill
        )

    def _reset_trade_date(self, trade_date: date, equity: float) -> None:
        self._trade_date = trade_date
        self._trade_date_start_equity = equity
        self._trade_date_peak_equity = equity
        self._daily_locked = False
        self._portfolio_consecutive_losses = 0
        self._portfolio_loss_stopped = False
        for symbol in self.symbols:
            self._symbol_consecutive_losses[symbol] = 0
            self._cooldown_until[symbol] = None
            self._symbol_stopped[symbol] = False


@dataclass(frozen=True)
class RiskEvaluationContext:
    now: datetime
    exchange_trade_date: date
    account: AccountSnapshot
    risk: RiskSnapshot


@dataclass(frozen=True)
class SizeDecision:
    quantity: int
    risk_budget: float
    risk_quantity: int
    margin_quantity: int
    symbol_risk_quantity: int
    portfolio_risk_quantity: int
    net_stop_loss_per_contract: float
    margin_per_contract: float
    reason: str

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class RuleRiskDecision:
    allowed: bool
    quantity: int
    reason: str
    audit: tuple[dict[str, Any], ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def calculate_order_size(
    candidate: Mapping[str, Any] | object,
    account: AccountSnapshot,
    spec: object,
    risk: RiskSnapshot,
    *,
    max_symbol_open_risk_pct: float = 0.0025,
    max_portfolio_open_risk_pct: float = 0.0050,
    max_entry_margin_usage_pct: float = 0.30,
) -> SizeDecision:
    """Floor quantity by trade, symbol, portfolio, margin, and lot constraints."""
    trigger = _candidate_float(candidate, "trigger_price")
    stop_loss = _candidate_float(candidate, "net_stop_loss_per_contract")
    margin_rate = _candidate_float(candidate, "margin_rate")
    symbol = str(_candidate_value(candidate, "symbol"))
    contract_size = float(spec.contract_size)
    lot_step = int(spec.lot_step)
    if not all(
        (
            _positive(trigger),
            _positive(stop_loss),
            _positive(margin_rate),
            _positive(contract_size),
            lot_step > 0,
            _positive(risk.current_risk_pct),
        )
    ):
        raise ValueError("invalid sizing input")

    equity = account.marked_equity
    risk_budget = equity * risk.current_risk_pct
    symbol_remaining = max(
        0.0,
        equity * max_symbol_open_risk_pct - float(account.symbol_open_risk.get(symbol, 0.0)),
    )
    portfolio_remaining = max(
        0.0,
        equity * max_portfolio_open_risk_pct - account.portfolio_open_risk,
    )
    margin_per_contract = trigger * contract_size * margin_rate
    margin_remaining = max(
        0.0,
        equity * max_entry_margin_usage_pct - account.margin_used_and_reserved,
    )
    risk_quantity = math.floor(risk_budget / stop_loss)
    symbol_quantity = math.floor(symbol_remaining / stop_loss)
    portfolio_quantity = math.floor(portfolio_remaining / stop_loss)
    margin_quantity = math.floor(margin_remaining / margin_per_contract)
    raw = min(risk_quantity, symbol_quantity, portfolio_quantity, margin_quantity)
    quantity = max(0, (raw // lot_step) * lot_step)
    return SizeDecision(
        quantity=quantity,
        risk_budget=risk_budget,
        risk_quantity=risk_quantity,
        margin_quantity=margin_quantity,
        symbol_risk_quantity=symbol_quantity,
        portfolio_risk_quantity=portfolio_quantity,
        net_stop_loss_per_contract=stop_loss,
        margin_per_contract=margin_per_contract,
        reason="ok" if quantity > 0 else "qty_zero",
    )


class RuleOnlyRiskAdapter:
    """Call compatible risk components only after strict input pre-validation."""

    def __init__(
        self,
        config: ScalpConfig,
        *,
        mode: str,
        liquidity_guard: object | None = None,
        volatility_scaler: object | None = None,
        daily_var_scaler: object | None = None,
        execution_quality_scaler: object | None = None,
    ) -> None:
        if mode not in {"backtest", "live"}:
            raise ValueError("mode must be backtest or live")
        self.config = config
        self.mode = mode
        liquidity = config.risk_components.liquidity
        self.liquidity_guard = liquidity_guard or LiquidityFloorGuard(
            LiquidityFloorGuardConfig(
                volume_floor_ratio=liquidity.volume_floor_ratio,
                max_spread_ticks=liquidity.max_spread_ticks,
                min_turnover_ratio=liquidity.min_turnover_ratio,
                allow_close_orders=liquidity.allow_close_orders,
                bypass_for_symbols=liquidity.bypass_for_symbols,
                require_all_indicators=liquidity.require_all_indicators,
            )
        )
        volatility = config.risk_components.volatility
        self.volatility_scaler = volatility_scaler or VolatilityRegimeScaler(
            VolatilityRegimeScalerConfig(
                vol_pctl_edges=volatility.vol_pctl_edges,
                mults=volatility.mults,
                cap_mult=volatility.cap_mult,
                floor_mult=volatility.floor_mult,
                vol_pctl_columns=volatility.vol_pctl_columns,
            )
        )
        daily_var = config.risk_components.daily_var
        self.daily_var_scaler = daily_var_scaler or DailyVaRBudgetSizer(
            DailyVaRBudgetConfig(
                budget_bp_by_cluster=dict(daily_var.budget_bp_by_cluster),
                default_budget_bp=daily_var.default_budget_bp,
                account_budget_bp=daily_var.account_budget_bp,
                warning_ratio=daily_var.warning_ratio,
                warning_mult=daily_var.warning_mult,
                reset_at_session_open=daily_var.reset_at_session_open,
            )
        )
        execution = config.risk_components.execution_quality
        self.execution_quality_scaler = execution_quality_scaler or ExecutionQualityScaler(
            ExecutionQualityConfig(
                slippage_ratio_threshold=execution.slippage_ratio_threshold,
                reject_rate_threshold=execution.reject_rate_threshold,
                avg_price_deviation_threshold=execution.avg_price_deviation_threshold,
                rolling_window_days=execution.rolling_window_days,
                mults=tuple(execution.mults),
                min_trades_for_assessment=execution.min_trades_for_assessment,
            )
        )

    def evaluate(
        self,
        order: Mapping[str, Any],
        context: RiskEvaluationContext,
        *,
        proposed_quantity: int,
    ) -> RuleRiskDecision:
        if str(order.get("offset", "")).lower() != "open":
            return RuleRiskDecision(True, max(0, int(proposed_quantity)), "close_always_allowed")
        return self.evaluate_entry(order, context, proposed_quantity=proposed_quantity)

    def evaluate_entry(
        self,
        candidate: Mapping[str, Any],
        context: RiskEvaluationContext,
        *,
        proposed_quantity: int,
    ) -> RuleRiskDecision:
        quantity = max(0, int(proposed_quantity))
        if quantity <= 0:
            return RuleRiskDecision(False, 0, "qty_zero")
        validation = self._validate_inputs(candidate)
        if validation is not None:
            return RuleRiskDecision(False, 0, validation)
        local = self._local_policy(candidate, context)
        if local is not None:
            return RuleRiskDecision(False, 0, local)
        audit: list[dict[str, Any]] = []
        quantity = self._local_scale(quantity, context.risk, audit)
        if quantity <= 0:
            return RuleRiskDecision(False, 0, "local_scale_qty_zero", tuple(audit))

        try:
            risk_context = RiskContext(
                pos={},
                daily_pnl=float(candidate["marked_equity_daily_pnl"]),
                capital=context.account.marked_equity,
                now=pd.Timestamp(context.now),
            )
            order = {
                "symbol": candidate["symbol"],
                "vt_symbol": candidate["symbol"],
                "offset": "open",
                "volume_ratio": candidate["volume_ratio"],
                "turnover_ratio": candidate["turnover_activity_ratio"],
            }
            if "bid_ask_spread_ticks" in candidate:
                order["bid_ask_spread_ticks"] = candidate["bid_ask_spread_ticks"]
            liquidity_decision = self.liquidity_guard.check(order, risk_context)
            audit.append(
                {
                    "component": "LiquidityFloorGuard",
                    "allowed": bool(liquidity_decision.allowed),
                    "reason": liquidity_decision.reason,
                }
            )
            if not liquidity_decision.allowed:
                return RuleRiskDecision(False, 0, liquidity_decision.reason, tuple(audit))

            signal_context = SignalContext(
                candidate=dict(candidate),
                portfolio={
                    "equity": context.account.marked_equity,
                    "daily_pnl": float(candidate["marked_equity_daily_pnl"]),
                    "daily_pnl_by_cluster": dict(candidate["marked_equity_daily_pnl_by_cluster"]),
                },
                bar_dt=pd.Timestamp(context.now),
            )
            quantity, reason = self.volatility_scaler.scale(signal_context, quantity)
            audit.append({"component": "VolatilityRegimeScaler", "quantity": quantity, "reason": reason})
            if quantity <= 0:
                return RuleRiskDecision(False, 0, reason, tuple(audit))
            quantity, reason = self.daily_var_scaler.scale(signal_context, quantity)
            audit.append({"component": "DailyVaRBudgetSizer", "quantity": quantity, "reason": reason})
            if quantity <= 0:
                return RuleRiskDecision(False, 0, reason, tuple(audit))
            quantity, reason = self._execution_quality(candidate, signal_context, quantity)
            audit.append({"component": "ExecutionQualityScaler", "quantity": quantity, "reason": reason})
        except Exception as exc:
            audit.append({"component": "exception", "error": f"{type(exc).__name__}:{exc}"})
            return RuleRiskDecision(False, 0, "component_exception", tuple(audit))
        if quantity <= 0:
            return RuleRiskDecision(False, 0, reason, tuple(audit))
        return RuleRiskDecision(True, quantity, "ok", tuple(audit))

    def _validate_inputs(self, candidate: Mapping[str, Any]) -> str | None:
        liquidity = self.config.risk_components.liquidity
        required = list(
            liquidity.required_live_fields if self.mode == "live" else liquidity.required_backtest_fields
        )
        required.extend(
            [
                "symbol",
                "contract_code",
                "realized_vol_pctl_252_30m",
                "volatility_sample_count",
                "last_trade_date",
                "main_switch_date",
                "trading_days_to_expiry",
                "trading_days_since_switch",
                "daily_limit_known",
                "marked_equity_daily_pnl",
                "marked_equity_daily_pnl_by_cluster",
                "execution_quality_state",
            ]
        )
        for name in required:
            if name not in candidate or candidate[name] is None:
                return f"missing_input:{name}"
        numeric = list(
            liquidity.required_live_fields if self.mode == "live" else liquidity.required_backtest_fields
        )
        numeric.extend(
            [
                "realized_vol_pctl_252_30m",
                "volatility_sample_count",
                "trading_days_to_expiry",
                "trading_days_since_switch",
                "marked_equity_daily_pnl",
            ]
        )
        for name in numeric:
            try:
                value = float(candidate[name])
            except (TypeError, ValueError):
                return f"invalid_input:{name}"
            if not math.isfinite(value):
                return f"nonfinite_input:{name}"
        for name in ("trading_days_to_expiry", "trading_days_since_switch"):
            value = float(candidate[name])
            if value < 0 or not value.is_integer():
                return f"invalid_input:{name}"
        for name in ("last_trade_date", "main_switch_date"):
            try:
                parsed = pd.Timestamp(candidate[name])
            except (TypeError, ValueError):
                return f"invalid_input:{name}"
            if pd.isna(parsed):
                return f"invalid_input:{name}"
        for name in ("daily_limit_known", "roll_freeze", "market_data_stale"):
            if name in candidate and not isinstance(candidate[name], bool):
                return f"invalid_input:{name}"
        if int(candidate["volatility_sample_count"]) < self.config.risk_components.volatility.lookback_completed_30m_bars:
            return "missing_input:volatility_window"
        by_cluster = candidate["marked_equity_daily_pnl_by_cluster"]
        if not isinstance(by_cluster, Mapping):
            return "invalid_input:marked_equity_daily_pnl_by_cluster"
        if not candidate["daily_limit_known"]:
            return "missing_input:daily_limit_known"
        try:
            execution_count = int(candidate.get("execution_quality_sample_count", 0))
        except (TypeError, ValueError):
            return "invalid_input:execution_quality_sample_count"
        execution = self.config.risk_components.execution_quality
        execution_state = str(candidate["execution_quality_state"])
        if execution_count < execution.min_trades_for_assessment:
            if execution_state != "INACTIVE_WARMUP":
                return "invalid_input:execution_quality_state"
        else:
            if execution_state != "ACTIVE":
                return "invalid_input:execution_quality_state"
            for name in (
                "execution_slippage_ratio",
                "execution_reject_rate",
                "execution_avg_price_deviation_pct",
            ):
                if name not in candidate or candidate[name] is None:
                    return f"missing_input:{name}"
                try:
                    value = float(candidate[name])
                except (TypeError, ValueError):
                    return f"invalid_input:{name}"
                if not math.isfinite(value) or value < 0:
                    return f"invalid_input:{name}"
        return None

    def _local_policy(
        self,
        candidate: Mapping[str, Any],
        context: RiskEvaluationContext,
    ) -> str | None:
        risk = context.risk
        cfg = self.config.risk
        if bool(candidate.get("market_data_stale", False)):
            return "market_data_stale"
        if bool(candidate.get("roll_freeze", False)):
            return "roll_freeze"
        days_to_expiry = int(float(candidate["trading_days_to_expiry"]))
        days_since_switch = int(float(candidate["trading_days_since_switch"]))
        rollover = self.config.risk_components.rollover
        if days_to_expiry <= rollover.force_close_days:
            return "rollover_force_close_window"
        if days_to_expiry < rollover.freeze_window_days:
            return "rollover_expiry_window"
        if 0 <= days_since_switch < rollover.post_switch_freeze_days:
            return "rollover_post_switch_freeze"
        if risk.drawdown_pct >= cfg.drawdown_kill_pct:
            return "drawdown_kill"
        if risk.drawdown_pct >= cfg.drawdown_stop_pct:
            return "drawdown_stop"
        if risk.trade_date_return <= -cfg.daily_hard_loss_pct:
            return "daily_hard_loss"
        if risk.daily_locked:
            return "daily_locked"
        if risk.portfolio_stopped or risk.portfolio_consecutive_losses >= cfg.portfolio_stop_after:
            return "portfolio_loss_stop"
        if risk.symbol_stopped or risk.symbol_consecutive_losses >= cfg.symbol_stop_after:
            return "symbol_loss_stop"
        if risk.cooldown_until is not None and context.now < risk.cooldown_until:
            return "symbol_loss_cooldown"
        if (
            risk.peak_profit >= cfg.profit_lock_activation_pct
            and risk.giveback_cny >= cfg.profit_giveback_pct * context.account.marked_equity
        ):
            return "profit_giveback"
        return None

    def _local_scale(
        self,
        quantity: int,
        risk: RiskSnapshot,
        audit: list[dict[str, Any]],
    ) -> int:
        cfg = self.config.risk
        result = quantity
        if risk.session_return <= -cfg.session_soft_loss_pct:
            result = math.floor(result * 0.5)
            audit.append({"component": "session_soft_loss", "quantity": result})
        if risk.drawdown_pct >= cfg.drawdown_half_pct:
            result = math.floor(result * 0.5)
            audit.append({"component": "drawdown_half", "quantity": result})
        return result

    def _execution_quality(
        self,
        candidate: Mapping[str, Any],
        context: SignalContext,
        quantity: int,
    ) -> tuple[int, str]:
        del context
        execution = self.config.risk_components.execution_quality
        count = int(candidate.get("execution_quality_sample_count", 0))
        state = str(candidate["execution_quality_state"])
        if count < execution.min_trades_for_assessment:
            if state != "INACTIVE_WARMUP":
                raise ValueError("execution quality warmup state is missing")
            return math.floor(quantity * execution.warmup_multiplier), "exec_quality:INACTIVE_WARMUP"
        triggered: list[tuple[str, float]] = []
        if float(candidate["execution_slippage_ratio"]) > execution.slippage_ratio_threshold:
            triggered.append(("slippage", execution.mults[0]))
        if float(candidate["execution_reject_rate"]) > execution.reject_rate_threshold:
            triggered.append(("reject", execution.mults[1]))
        if (
            float(candidate["execution_avg_price_deviation_pct"])
            > execution.avg_price_deviation_threshold
        ):
            triggered.append(("deviation", execution.mults[2]))
        if not triggered:
            return quantity, "exec_quality:ok"
        multiplier = min(value for _, value in triggered)
        result = math.floor(quantity * multiplier)
        if result == 0 and quantity >= 1 and multiplier > 0:
            result = 1
        names = ",".join(name for name, _ in triggered)
        return result, f"exec_quality:{names}:mult={multiplier:.2f}"


def _candidate_value(candidate: Mapping[str, Any] | object, name: str) -> Any:
    if isinstance(candidate, Mapping):
        if name in candidate:
            return candidate[name]
        context = candidate.get("context", {})
    else:
        if hasattr(candidate, name):
            return getattr(candidate, name)
        context = getattr(candidate, "context", {})
    if isinstance(context, Mapping) and name in context:
        return context[name]
    raise ValueError(f"candidate missing {name}")


def _candidate_float(candidate: Mapping[str, Any] | object, name: str) -> float:
    try:
        value = float(_candidate_value(candidate, name))
    except (TypeError, ValueError) as exc:
        raise ValueError(f"candidate {name} must be numeric") from exc
    return value


def _positive(value: float) -> bool:
    return math.isfinite(float(value)) and float(value) > 0


def _nonnegative(value: float) -> bool:
    return math.isfinite(float(value)) and float(value) >= 0


__all__ = [
    "AccountSnapshot",
    "PortfolioRiskState",
    "RiskEvaluationContext",
    "RiskSnapshot",
    "RuleOnlyRiskAdapter",
    "RuleRiskDecision",
    "SizeDecision",
    "calculate_order_size",
]
