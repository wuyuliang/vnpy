"""Strict configuration loader for the isolated cycle_v1 strategy."""
from __future__ import annotations

from dataclasses import dataclass, fields
import hashlib
from pathlib import Path
from typing import Any, TypeVar

import yaml

from .core.types import SetupType, V0_ENABLED_SETUPS


DEFAULT_CONFIG_PATH = Path(__file__).parents[1] / "config" / "cycle_v1.yaml"
T = TypeVar("T")


@dataclass(frozen=True)
class FeatureConfig:
    atr_period: int = 14
    adx_period: int = 14
    atr_compression_window: int = 50
    momentum_window: int = 5
    trend_bar_min_body_ratio: float = 0.50
    ema_fast: int = 20
    ema_slow: int = 60
    ema_slope_lookback: int = 5
    structure_window: int = 20
    percentile_lookback: int = 252
    pivot_left: int = 2
    pivot_right: int = 2
    structure_pivot_count: int = 5
    volume_bucket_lookback: int = 20

    def __post_init__(self) -> None:
        positive = (
            self.atr_period,
            self.adx_period,
            self.atr_compression_window,
            self.momentum_window,
            self.ema_fast,
            self.ema_slow,
            self.ema_slope_lookback,
            self.structure_window,
            self.percentile_lookback,
            self.pivot_left,
            self.pivot_right,
            self.structure_pivot_count,
            self.volume_bucket_lookback,
        )
        if min(positive) < 1 or self.ema_fast >= self.ema_slow:
            raise ValueError("feature windows must be positive and ema_fast < ema_slow")
        _unit_interval(self.trend_bar_min_body_ratio, "trend_bar_min_body_ratio")


@dataclass(frozen=True)
class CycleConfig:
    direction_min_pressure_margin: float = 0.10
    bo_min_body_ratio: float = 0.60
    bo_min_close_pos: float = 0.75
    bo_min_distance_atr: float = 0.10
    bo_recent_window: int = 3
    bo_min_trend_bars: int = 2
    bo_max_overlap: float = 0.55
    bo_min_pressure: float = 0.65
    breakout_tolerance_atr: float = 0.10
    channel_min_bars: int = 5
    tight_min_slope: float = 0.05
    tight_max_median_pb_bars: float = 2.0
    tight_max_pb_depth: float = 0.35
    tight_ema_cross_window: int = 10
    tight_max_ema_crosses: int = 1
    tight_max_opposite_ratio: float = 0.25
    tight_min_structure_score: float = 0.67
    broad_min_slope: float = 0.01
    broad_min_structure_score: float = 0.55
    broad_min_efficiency: float = 0.25
    range_max_abs_slope: float = 0.03
    range_min_overlap: float = 0.60
    range_min_direction_changes: float = 0.45
    range_max_efficiency: float = 0.35
    range_max_directional_strength: float = 0.20
    range_window: int = 40
    range_lower_zone: float = 0.33
    range_upper_zone: float = 0.67
    range_min_width_cost_multiple: float = 6.0
    range_tight_max_width_atr: float = 2.5
    use_range_aux_gate: bool = False
    range_max_adx_percentile: float = 40.0
    range_max_atr_compression_percentile: float = 40.0
    enter_score: float = 0.65
    confirm_bars: int = 2
    switch_margin: float = 0.10
    confidence_full_margin: float = 0.20
    min_state_bars: int = 2
    climax_range_percentile: float = 95.0
    climax_ema_distance_atr: float = 2.5
    climax_cumulative_move_atr: float = 3.0
    transition_min_momentum_decay: float = 0.35
    transition_opposite_score: float = 0.65
    transition_pressure_delta: float = 0.15
    transition_score_margin: float = 0.05

    def __post_init__(self) -> None:
        if not 0 <= self.range_lower_zone < self.range_upper_zone <= 1:
            raise ValueError("range zones must satisfy 0 <= lower < upper <= 1")
        if min(
            self.bo_recent_window,
            self.bo_min_trend_bars,
            self.channel_min_bars,
            self.tight_ema_cross_window,
            self.range_window,
            self.confirm_bars,
            self.min_state_bars,
        ) < 1:
            raise ValueError("cycle windows and confirmation counts must be positive")
        for name in (
            "direction_min_pressure_margin",
            "bo_min_body_ratio",
            "bo_min_close_pos",
            "bo_max_overlap",
            "bo_min_pressure",
            "tight_max_pb_depth",
            "tight_max_opposite_ratio",
            "tight_min_structure_score",
            "broad_min_structure_score",
            "broad_min_efficiency",
            "range_min_overlap",
            "range_min_direction_changes",
            "range_max_efficiency",
            "range_max_directional_strength",
            "enter_score",
            "switch_margin",
            "confidence_full_margin",
            "transition_min_momentum_decay",
            "transition_opposite_score",
            "transition_pressure_delta",
            "transition_score_margin",
        ):
            _unit_interval(float(getattr(self, name)), name)
        for name in (
            "range_max_adx_percentile",
            "range_max_atr_compression_percentile",
            "climax_range_percentile",
        ):
            value = float(getattr(self, name))
            if not 0 <= value <= 100:
                raise ValueError(f"{name} must be in [0, 100]")


@dataclass(frozen=True)
class SetupConfig:
    max_signal_bar_atr: float = 1.50
    strong_opposite_body_ratio: float = 0.60
    strong_opposite_close_pos: float = 0.70
    max_pullback_bars: int = 20
    failed_breakout_max_excursion_atr: float = 0.50
    failed_breakout_min_reclaim_atr: float = 0.05
    failed_breakout_min_body_ratio: float = 0.35
    failed_breakout_min_close_pos: float = 0.60
    wedge_min_spacing: int = 2
    wedge_max_span: int = 20
    wedge_max_push_acceleration: float = 1.10
    wedge_min_reversal_body_ratio: float = 0.35
    wedge_min_reversal_close_pos: float = 0.60
    setup_expiry_bars: int = 3

    def __post_init__(self) -> None:
        if min(
            self.max_signal_bar_atr,
            self.max_pullback_bars,
            self.wedge_min_spacing,
            self.wedge_max_span,
            self.setup_expiry_bars,
        ) <= 0:
            raise ValueError("setup distances and windows must be positive")


@dataclass(frozen=True)
class ExecutionConfig:
    stop_atr_floor: float = 0.50
    stop_min_ticks: int = 2
    limit_entry_buffer_atr: float = 0.25
    min_space_R: float = 1.50
    max_cost_R: float = 0.25
    min_net_reward_R: float = 0.75
    swing_initial_target_R: float = 2.00
    break_even_min_mfe_R: float = 1.00
    runner_min_space_R: float = 1.00
    max_add_ons: int = 0
    add_on_min_mfe_R: float = 1.00
    add_on_risk_fraction: float = 0.50

    def __post_init__(self) -> None:
        if min(
            self.stop_atr_floor,
            self.stop_min_ticks,
            self.min_space_R,
            self.min_net_reward_R,
            self.swing_initial_target_R,
            self.runner_min_space_R,
        ) <= 0:
            raise ValueError("execution distances and rewards must be positive")
        if self.max_cost_R < 0 or self.max_add_ons < 0:
            raise ValueError("execution costs and add-on count must be nonnegative")
        _unit_interval(self.add_on_risk_fraction, "add_on_risk_fraction")


@dataclass(frozen=True)
class RiskConfig:
    risk_per_trade: float = 0.0200
    max_trade_risk: float = 0.0200
    min_cycle_confidence: float = 0.55
    max_symbol_open_risk: float = 0.0050
    max_sector_open_risk: float = 0.0060
    max_total_open_risk: float = 0.0150
    max_margin_utilization: float = 0.40
    max_daily_loss_soft: float = 0.010
    max_daily_loss_hard: float = 0.015
    max_drawdown_soft: float = 0.08
    max_drawdown_hard: float = 0.15
    drawdown_floor_multiplier: float = 0.25
    correlation_lookback_sessions: int = 126
    correlation_min_observations: int = 63
    correlation_threshold: float = 0.70
    max_correlation_cluster_open_risk: float = 0.0060
    max_single_symbol_pnl_contribution: float = 0.40
    cost_stress_mult: float = 2.0

    def __post_init__(self) -> None:
        for name in (
            "risk_per_trade",
            "max_trade_risk",
            "min_cycle_confidence",
            "max_symbol_open_risk",
            "max_sector_open_risk",
            "max_total_open_risk",
            "max_margin_utilization",
            "max_daily_loss_soft",
            "max_daily_loss_hard",
            "max_drawdown_soft",
            "max_drawdown_hard",
            "drawdown_floor_multiplier",
            "correlation_threshold",
            "max_correlation_cluster_open_risk",
            "max_single_symbol_pnl_contribution",
        ):
            _unit_interval(float(getattr(self, name)), name)
        if not 0 < self.correlation_threshold <= 1:
            raise ValueError("correlation_threshold must be in (0, 1]")
        if (
            self.correlation_min_observations < 2
            or self.correlation_lookback_sessions < self.correlation_min_observations
        ):
            raise ValueError("correlation history configuration is invalid")
        if self.cost_stress_mult < 1:
            raise ValueError("cost_stress_mult must be at least one")


@dataclass(frozen=True)
class ProfileConfig:
    small_tf_candidates: tuple[str, ...] = ("5m", "15m", "30m")
    medium_tf_candidates: tuple[str, ...] = ("30m", "60m", "4h")
    large_tf_candidates: tuple[str, ...] = ("4h", "1d")
    target_medium_small_ratio: int = 6
    target_large_medium_ratio: int = 6
    min_history_sessions: int = 500
    min_intraday_history_bars: int = 500
    min_large_history_bars: int = 250
    min_small_bars_per_session: int = 6
    min_medium_bars_per_session: int = 2
    min_atr_ticks: float = 8.0
    max_cost_to_atr: float = 0.15
    max_zero_volume_ratio: float = 0.01
    max_gap_ratio: float = 0.005

    def __post_init__(self) -> None:
        for name in ("small_tf_candidates", "medium_tf_candidates", "large_tf_candidates"):
            object.__setattr__(self, name, tuple(getattr(self, name)))
        if not all(
            getattr(self, name)
            for name in ("small_tf_candidates", "medium_tf_candidates", "large_tf_candidates")
        ):
            raise ValueError("timeframe candidate sets must not be empty")
        if min(
            self.target_medium_small_ratio,
            self.target_large_medium_ratio,
            self.min_history_sessions,
            self.min_intraday_history_bars,
            self.min_large_history_bars,
            self.min_small_bars_per_session,
            self.min_medium_bars_per_session,
            self.min_atr_ticks,
        ) <= 0:
            raise ValueError("profile history, ratio, and ATR gates must be positive")
        for name in ("max_cost_to_atr", "max_zero_volume_ratio", "max_gap_ratio"):
            _unit_interval(float(getattr(self, name)), name)


@dataclass(frozen=True)
class BrooksCycleConfig:
    schema: str
    version: str
    features: FeatureConfig
    cycle: CycleConfig
    setup: SetupConfig
    execution: ExecutionConfig
    risk: RiskConfig
    profile: ProfileConfig
    enabled_setups: frozenset[SetupType]
    config_hash: str

    def __post_init__(self) -> None:
        if self.schema != "brooks_cycle_v1":
            raise ValueError("config schema must be brooks_cycle_v1")
        if self.version != "cycle_v1":
            raise ValueError("config version must be cycle_v1")
        if self.enabled_setups != V0_ENABLED_SETUPS:
            raise ValueError("cycle_v1 must enable exactly the frozen v0 setup set")
        if self.risk.max_daily_loss_hard <= self.risk.max_daily_loss_soft:
            raise ValueError("max_daily_loss_hard must exceed soft limit")
        if self.risk.max_drawdown_hard <= self.risk.max_drawdown_soft:
            raise ValueError("max_drawdown_hard must exceed soft limit")


def load_config(path: str | Path = DEFAULT_CONFIG_PATH) -> BrooksCycleConfig:
    config_path = Path(path)
    raw_bytes = config_path.read_bytes()
    payload = yaml.safe_load(raw_bytes) or {}
    if not isinstance(payload, dict):
        raise ValueError("config must be a mapping")
    if payload.get("schema") != "brooks_cycle_v1":
        raise ValueError("config schema must be brooks_cycle_v1")
    allowed = {
        "schema", "version", "features", "cycle", "setup", "execution", "risk",
        "profile", "enabled_setups",
    }
    unknown = set(payload).difference(allowed)
    if unknown:
        raise ValueError(f"unknown config sections: {sorted(unknown)}")
    enabled = frozenset(SetupType(value) for value in payload.get("enabled_setups", ()))
    return BrooksCycleConfig(
        schema=str(payload["schema"]),
        version=str(payload.get("version", "")),
        features=_strict_dataclass(FeatureConfig, payload.get("features", {})),
        cycle=_strict_dataclass(CycleConfig, payload.get("cycle", {})),
        setup=_strict_dataclass(SetupConfig, payload.get("setup", {})),
        execution=_strict_dataclass(ExecutionConfig, payload.get("execution", {})),
        risk=_strict_dataclass(RiskConfig, payload.get("risk", {})),
        profile=_strict_dataclass(ProfileConfig, payload.get("profile", {})),
        enabled_setups=enabled,
        config_hash=hashlib.sha256(raw_bytes).hexdigest(),
    )


def _strict_dataclass(cls: type[T], values: Any) -> T:
    if not isinstance(values, dict):
        raise ValueError(f"{cls.__name__} config must be a mapping")
    names = {item.name for item in fields(cls)}
    unknown = set(values).difference(names)
    if unknown:
        raise ValueError(f"unknown {cls.__name__} fields: {sorted(unknown)}")
    return cls(**values)


def _unit_interval(value: float, name: str) -> None:
    if not 0 <= value <= 1:
        raise ValueError(f"{name} must be in [0, 1]")


__all__ = [
    "BrooksCycleConfig",
    "CycleConfig",
    "ExecutionConfig",
    "FeatureConfig",
    "ProfileConfig",
    "RiskConfig",
    "SetupConfig",
    "load_config",
]
