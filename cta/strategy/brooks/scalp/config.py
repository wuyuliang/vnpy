"""Frozen configuration for the rule-only Brooks scalp strategy."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from dataclasses import dataclass, fields, is_dataclass
from pathlib import Path
from types import MappingProxyType
from typing import Any

import yaml


DEFAULT_CONFIG_PATH = Path(__file__).with_name("strategy.yaml")


@dataclass(frozen=True)
class AccountConfig:
    initial_equity: float
    base_risk_pct: float
    max_symbol_open_risk_pct: float
    max_portfolio_open_risk_pct: float
    max_entry_margin_usage_pct: float
    hard_margin_usage_pct: float


@dataclass(frozen=True)
class MetadataConfig:
    root: str
    require_authoritative_exchange_source: bool
    missing_policy: str


@dataclass(frozen=True)
class CoordinationConfig:
    active_bar_stale_tolerance_seconds: int
    inactive_symbol_is_stale: bool


@dataclass(frozen=True)
class IntervalsConfig:
    regime: str
    setup: str
    execution: str
    warmup_30m: int
    warmup_5m: int
    entry_valid_1m_bars: int
    max_holding_1m_bars: int
    no_follow_through_bars: int


@dataclass(frozen=True)
class RulesConfig:
    allow_runtime_disable: bool
    required_rule_directions: tuple[tuple[str, str], ...]


@dataclass(frozen=True)
class TradePlanConfig:
    planned_net_payoff: float
    allowed_net_payoff_min: float
    allowed_net_payoff_max: float
    trigger_buffer_ticks: int
    stop_buffer_ticks: int
    minimum_space_r: float
    pressure_fee_multiplier: float
    pressure_slippage_multiplier: float
    same_bar_policy: str
    allow_scale_in: bool
    allow_partial_exit: bool
    allow_cross_session: bool


@dataclass(frozen=True)
class RiskConfig:
    session_soft_loss_pct: float
    daily_hard_loss_pct: float
    max_observed_daily_loss_pct: float
    profit_lock_activation_pct: float
    profit_giveback_pct: float
    symbol_loss_cooldown_after: int
    symbol_stop_after: int
    portfolio_stop_after: int
    cooldown_minutes: int
    drawdown_half_pct: float
    drawdown_stop_pct: float
    drawdown_kill_pct: float
    drawdown_stop_sessions: int
    no_new_entry_minutes: int
    force_flat_minutes: int


@dataclass(frozen=True)
class LiquidityConfig:
    volume_lookback_5m_bars: int
    volume_floor_ratio: float
    max_spread_ticks: int
    min_turnover_ratio: float
    allow_close_orders: bool
    require_all_indicators: bool
    required_backtest_fields: tuple[str, ...]
    required_live_fields: tuple[str, ...]
    bypass_for_symbols: tuple[str, ...]


@dataclass(frozen=True)
class RolloverConfig:
    freeze_window_days: int
    force_close_days: int
    post_switch_freeze_days: int


@dataclass(frozen=True)
class VolatilityConfig:
    vol_pctl_columns: tuple[str, ...]
    lookback_completed_30m_bars: int
    vol_pctl_edges: tuple[float, ...]
    mults: tuple[float, ...]
    cap_mult: float
    floor_mult: float


@dataclass(frozen=True)
class DailyVarConfig:
    budget_bp_by_cluster: Mapping[str, float]
    default_budget_bp: float
    account_budget_bp: float
    warning_ratio: float
    warning_mult: float
    reset_at_session_open: bool
    require_explicit_marked_equity_pnl: bool


@dataclass(frozen=True)
class ExecutionQualityConfig:
    slippage_ratio_threshold: float
    reject_rate_threshold: float
    avg_price_deviation_threshold: float
    rolling_window_days: int
    mults: tuple[float, ...]
    min_trades_for_assessment: int
    warmup_multiplier: float


@dataclass(frozen=True)
class RiskComponentsConfig:
    missing_input_policy: str
    liquidity: LiquidityConfig
    rollover: RolloverConfig
    volatility: VolatilityConfig
    daily_var: DailyVarConfig
    execution_quality: ExecutionQualityConfig


@dataclass(frozen=True)
class ValidationConfig:
    minimum_group_trades: int
    minimum_net_win_rate: float
    average_daily_return_target: float
    average_monthly_return_target: float
    risk_score_minimum: int


@dataclass(frozen=True)
class ScalpConfig:
    account: AccountConfig
    metadata: MetadataConfig
    coordination: CoordinationConfig
    intervals: IntervalsConfig
    rules: RulesConfig
    trade_plan: TradePlanConfig
    risk: RiskConfig
    risk_components: RiskComponentsConfig
    validation: ValidationConfig

    def to_dict(self) -> dict[str, Any]:
        """Return a JSON-safe snapshot used by reports and hashing."""
        return _jsonable(self)

    @property
    def sha256(self) -> str:
        return config_sha256(self)


def _jsonable(value: Any) -> Any:
    if is_dataclass(value) and not isinstance(value, type):
        return {field.name: _jsonable(getattr(value, field.name)) for field in fields(value)}
    if isinstance(value, Mapping):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (tuple, list)):
        return [_jsonable(item) for item in value]
    return value


def _parse_risk_components(raw: Mapping[str, Any]) -> RiskComponentsConfig:
    daily_var = dict(raw["daily_var"])
    daily_var["budget_bp_by_cluster"] = MappingProxyType(
        {
            str(key): float(value)
            for key, value in daily_var.get("budget_bp_by_cluster", {}).items()
        }
    )
    liquidity = dict(raw["liquidity"])
    for key in ("required_backtest_fields", "required_live_fields", "bypass_for_symbols"):
        liquidity[key] = tuple(liquidity[key])
    volatility = dict(raw["volatility"])
    for key in ("vol_pctl_columns", "vol_pctl_edges", "mults"):
        volatility[key] = tuple(volatility[key])
    execution_quality = dict(raw["execution_quality"])
    execution_quality["mults"] = tuple(execution_quality["mults"])
    return RiskComponentsConfig(
        missing_input_policy=str(raw["missing_input_policy"]),
        liquidity=LiquidityConfig(**liquidity),
        rollover=RolloverConfig(**raw["rollover"]),
        volatility=VolatilityConfig(**volatility),
        daily_var=DailyVarConfig(**daily_var),
        execution_quality=ExecutionQualityConfig(**execution_quality),
    )


def _from_mapping(raw: Mapping[str, Any]) -> ScalpConfig:
    expected = {
        "account",
        "metadata",
        "coordination",
        "intervals",
        "rules",
        "trade_plan",
        "risk",
        "risk_components",
        "validation",
    }
    if set(raw) != expected:
        missing = sorted(expected - set(raw))
        extra = sorted(set(raw) - expected)
        raise ValueError(f"invalid scalp config sections: missing={missing}, extra={extra}")

    rules = dict(raw["rules"])
    rules["required_rule_directions"] = tuple(
        (str(rule), str(direction)) for rule, direction in rules["required_rule_directions"]
    )
    config = ScalpConfig(
        account=AccountConfig(**raw["account"]),
        metadata=MetadataConfig(**raw["metadata"]),
        coordination=CoordinationConfig(**raw["coordination"]),
        intervals=IntervalsConfig(**raw["intervals"]),
        rules=RulesConfig(**rules),
        trade_plan=TradePlanConfig(**raw["trade_plan"]),
        risk=RiskConfig(**raw["risk"]),
        risk_components=_parse_risk_components(raw["risk_components"]),
        validation=ValidationConfig(**raw["validation"]),
    )
    if config.rules.allow_runtime_disable:
        raise ValueError("rules.allow_runtime_disable must remain false")
    return config


def load_config(path: str | Path | None = None) -> ScalpConfig:
    """Load the complete frozen YAML; runtime threshold overrides are unsupported."""
    config_path = Path(path) if path is not None else DEFAULT_CONFIG_PATH
    with config_path.open("r", encoding="utf-8") as handle:
        raw = yaml.safe_load(handle)
    if not isinstance(raw, Mapping):
        raise ValueError(f"scalp config must be a mapping: {config_path}")
    return _from_mapping(raw)


def config_sha256(value: ScalpConfig | str | Path | None = None) -> str:
    """Hash the canonical semantic configuration snapshot."""
    if isinstance(value, ScalpConfig):
        snapshot = value.to_dict()
    else:
        snapshot = load_config(value).to_dict()
    encoded = json.dumps(
        snapshot,
        ensure_ascii=True,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


__all__ = [
    "DEFAULT_CONFIG_PATH",
    "ScalpConfig",
    "config_sha256",
    "load_config",
]
