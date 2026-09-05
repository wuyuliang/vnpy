"""Configuration fields shared by deterministic CTA replay strategies."""
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class BaseReplayConfig:
    """Parameters consumed by the shared portfolio replay engine."""

    first_trend_entry_daily_breakout_buffer_ratio: float = 0.002
    trailing_buffer_atr: float = 0.2
    risk_per_trade: float = 0.01
    max_risk_per_trade: float = 0.02
    pullback_target_r: float = 2.0
    max_concurrent_positions: int = 5
    intraday_margin_utilization: float = 0.60
    overnight_margin_utilization: float = 0.20
    max_symbol_margin_utilization: float = 0.40
    overnight_reduction_minutes: int = 10
    symbol_loss_streak: int = 2
    symbol_loss_cooldown_enabled: bool = True
    symbol_loss_pair_window_hours: int = 48
    symbol_loss_cooldown_hours: int = 24
    drawdown_scale_threshold: float = 0.02
    drawdown_scale_release: float = 0.01
    drawdown_scale_factor: float = 0.5
    position_scale_min_one_lot: bool = True
    symbol_position_scale: float = 1.0
    portfolio_drawdown_threshold: float = 0.01
    portfolio_position_scale: float = 1.0
    entry_blocked_session_windows: tuple[tuple[str, str], ...] = (
        ("13:00", "15:00"),
        ("22:00", "02:30"),
    )
    daily_circuit_breaker_enabled: bool = True
    daily_circuit_breaker_loss_r: float = 2.0
    daily_circuit_breaker_loss_streak: int = 2
    pre_break_protection_enabled: bool = True
    pre_break_min_unrealized_r: float = 0.0
    pre_break_high_gap_min_unrealized_r: float = 0.5
    pre_break_gap_risk_scale: float = 0.5
    pre_break_lead_minutes: int = 10
    max_positions_per_sector: int = 2
    order_max_recess_minutes: int = 90
    max_entry_volume_ratio: float = 2.0
    max_entry_stop_distance_atr: float = 0.8
    entry_range_lookback_days: int = 2
    max_entry_range_position: float = 0.85
    min_entry_range_width_atr: float = 2.0
    profit_floor_enabled: bool = True
    profit_floor_arm_r: float = 0.5
    profit_floor_giveback_r: float = 1.0
    profit_floor_giveback_pct: float = 0.25
    profit_floor_extra_slippage_ticks: int = 1
    no_progress_bars: int = 0
    no_progress_min_r: float = 0.5
    follow_through_bars: int = 0
    follow_through_body_atr_mult: float = 2.0
    chase_high_entry_enabled: bool = False
    chase_high_virtual_enabled: bool = True
    chase_high_lookback: int = 10
    chase_high_min_samples: int = 8
    chase_high_min_prior_r: float = 0.0
    turnover_share_threshold: float = 0.0
    turnover_lookback_days: int = 5
    high_gap_lookback_days: int = 60
    high_gap_quantile: float = 0.90
    high_gap_ratio_threshold: float = 1.0
