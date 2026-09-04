"""Configuration for the daily/5-minute trend strategy."""
from __future__ import annotations

import math
import re
from dataclasses import dataclass


_ENTRY_WINDOW_PATTERN = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")


@dataclass(frozen=True)
class MultiTimeframeTrendConfig:
    """Shared research parameters from the approved strategy specification."""

    daily_ema_fast: int = 5
    daily_ema_mid: int = 10
    daily_ema_slow: int = 20
    always_in_long_daily_ema_gap_min_ratio: float = 0.02
    first_trend_entry_daily_breakout_buffer_ratio: float = 0.002
    atr_period: int = 14
    daily_obstacle_lookback: int = 20
    daily_pivot_left: int = 2
    daily_pivot_right: int = 2
    obstacle_buffer_atr: float = 0.5
    always_in_window: int = 6
    always_in_min_progress: int = 4
    intraday_pivot_left: int = 2
    intraday_pivot_right: int = 2
    trailing_buffer_atr: float = 0.2
    pullback_min_bars: int = 3
    pullback_max_bars: int = 12
    volume_lookback: int = 20
    volume_quantile: float = 0.80
    entry_buffer_ticks: int = 1
    order_expiry_bars: int = 3
    candidate_setup_blacklist: tuple[str, ...] = ("pullback_breakout",)
    candidate_direction_blacklist: tuple[int, ...] = (-1,)
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
    symbol_position_scale: float = 1.0
    portfolio_drawdown_threshold: float = 0.01
    portfolio_position_scale: float = 1.0
    # P0-1 入场时段窗口：落在任一窗口内的候选拒绝开新仓
    entry_blocked_session_windows: tuple[tuple[str, str], ...] = (
        ("13:00", "15:00"),
        ("22:00", "02:30"),
    )
    # P0-2 组合级日内亏损熔断
    daily_circuit_breaker_enabled: bool = True
    daily_circuit_breaker_loss_r: float = 2.0
    daily_circuit_breaker_loss_streak: int = 2
    # P0-3 跨休市持仓保护
    pre_break_protection_enabled: bool = True
    pre_break_min_unrealized_r: float = 0.0
    pre_break_high_gap_min_unrealized_r: float = 0.5
    pre_break_gap_risk_scale: float = 0.5
    pre_break_lead_minutes: int = 10
    # P1-2 板块集中度
    max_positions_per_sector: int = 2
    # R1 挂单不得跨越长于该分钟数的休市（0 表示关闭）
    order_max_recess_minutes: int = 90
    # R2 入场质量：量比上限与结构止损距离上限（0 表示关闭）
    max_entry_volume_ratio: float = 2.0
    max_entry_stop_distance_atr: float = 0.8
    # R3 区间位置：不在近 N 个交易日 5 分钟区间的高端开新仓（0 表示关闭）
    entry_range_lookback_days: int = 2
    max_entry_range_position: float = 0.85
    min_entry_range_width_atr: float = 2.0
    # 追高影子单：被区间位置闸门拦下的候选继续做虚拟单，组合层最近 N 笔虚拟净 R
    # 超过阈值时重新放行真实追高单。0 笔回看或未启用时一律不放行。
    # 是否允许开闸放行**真实**追高单。实测（r4_fixed vs 追高关闭）追高整体为负，
    # 所以默认关闭；打开前先看影子单的滚动记录。
    chase_high_entry_enabled: bool = False
    # 是否继续跟踪影子单。与上面的开关独立：关掉真实追高的同时保留影子记录，
    # 就能持续观察"现在追高到底灵不灵"而不用真金白银去试。
    chase_high_virtual_enabled: bool = True
    chase_high_lookback: int = 10
    chase_high_min_samples: int = 8
    chase_high_min_prior_r: float = 0.0
    # 成交额占比选池：按前 N 个交易日的全合约成交额排序，只在覆盖该占比的头部
    # 队列里开新仓。0 表示关闭（默认关闭，打开会大幅改变品种池）。
    turnover_share_threshold: float = 0.0
    turnover_lookback_days: int = 5
    # P1-3 高跳空品种自适应分级
    high_gap_lookback_days: int = 60
    high_gap_quantile: float = 0.90
    high_gap_ratio_threshold: float = 1.0

    def __post_init__(self) -> None:
        if not (
            0 < self.daily_ema_fast < self.daily_ema_mid < self.daily_ema_slow
        ):
            raise ValueError("daily EMA periods must satisfy fast < mid < slow")
        raw_ema_gap_ratio = self.always_in_long_daily_ema_gap_min_ratio
        ema_gap_error = (
            "always_in_long_daily_ema_gap_min_ratio must be finite and in [0, 1)"
        )
        if isinstance(raw_ema_gap_ratio, bool):
            raise ValueError(ema_gap_error)
        try:
            always_in_long_daily_ema_gap_min_ratio = float(raw_ema_gap_ratio)
        except (TypeError, ValueError) as exc:
            raise ValueError(ema_gap_error) from exc
        if not math.isfinite(always_in_long_daily_ema_gap_min_ratio) or not (
            0 <= always_in_long_daily_ema_gap_min_ratio < 1
        ):
            raise ValueError(ema_gap_error)
        object.__setattr__(
            self,
            "always_in_long_daily_ema_gap_min_ratio",
            always_in_long_daily_ema_gap_min_ratio,
        )
        raw_breakout_buffer_ratio = self.first_trend_entry_daily_breakout_buffer_ratio
        breakout_buffer_error = (
            "first_trend_entry_daily_breakout_buffer_ratio must be finite and in [0, 1)"
        )
        if isinstance(raw_breakout_buffer_ratio, bool):
            raise ValueError(breakout_buffer_error)
        try:
            first_trend_entry_daily_breakout_buffer_ratio = float(
                raw_breakout_buffer_ratio
            )
        except (TypeError, ValueError) as exc:
            raise ValueError(breakout_buffer_error) from exc
        if not math.isfinite(first_trend_entry_daily_breakout_buffer_ratio) or not (
            0 <= first_trend_entry_daily_breakout_buffer_ratio < 1
        ):
            raise ValueError(breakout_buffer_error)
        object.__setattr__(
            self,
            "first_trend_entry_daily_breakout_buffer_ratio",
            first_trend_entry_daily_breakout_buffer_ratio,
        )
        positive_ints = (
            self.atr_period,
            self.daily_obstacle_lookback,
            self.daily_pivot_left,
            self.daily_pivot_right,
            self.always_in_window,
            self.always_in_min_progress,
            self.intraday_pivot_left,
            self.intraday_pivot_right,
            self.pullback_min_bars,
            self.pullback_max_bars,
            self.volume_lookback,
            self.entry_buffer_ticks,
            self.order_expiry_bars,
        )
        if any(int(value) <= 0 for value in positive_ints):
            raise ValueError("strategy window and tick parameters must be positive")
        if self.always_in_min_progress > self.always_in_window - 1:
            raise ValueError("always_in_min_progress exceeds available comparisons")
        if self.pullback_min_bars > self.pullback_max_bars:
            raise ValueError("pullback_min_bars must not exceed pullback_max_bars")
        setup_blacklist = tuple(self.candidate_setup_blacklist)
        if (
            len(setup_blacklist) != len(set(setup_blacklist))
            or any(
                value not in {"always_in", "pullback_breakout"}
                for value in setup_blacklist
            )
        ):
            raise ValueError(
                "candidate_setup_blacklist contains duplicate or unknown setup"
            )
        direction_blacklist = tuple(self.candidate_direction_blacklist)
        if (
            len(direction_blacklist) != len(set(direction_blacklist))
            or any(value not in {-1, 1} for value in direction_blacklist)
        ):
            raise ValueError(
                "candidate_direction_blacklist contains duplicate or invalid direction"
            )
        if not 0 < float(self.volume_quantile) < 1:
            raise ValueError("volume_quantile must be in (0, 1)")
        if float(self.obstacle_buffer_atr) < 0 or float(self.trailing_buffer_atr) < 0:
            raise ValueError("ATR buffers must be nonnegative")
        if not 0.01 <= float(self.risk_per_trade) <= 0.02:
            raise ValueError("risk_per_trade must be in [0.01, 0.02]")
        if not 0.01 <= float(self.max_risk_per_trade) <= 0.02:
            raise ValueError("max_risk_per_trade must be in [0.01, 0.02]")
        if self.risk_per_trade > self.max_risk_per_trade:
            raise ValueError("risk_per_trade must not exceed max_risk_per_trade")
        if float(self.pullback_target_r) <= 0:
            raise ValueError("pullback_target_r must be positive")
        if int(self.max_concurrent_positions) <= 0:
            raise ValueError("max_concurrent_positions must be positive")
        if not 0 < float(self.intraday_margin_utilization) <= 1:
            raise ValueError("intraday_margin_utilization must be in (0, 1]")
        if not (
            0
            < float(self.overnight_margin_utilization)
            <= float(self.intraday_margin_utilization)
        ):
            raise ValueError(
                "overnight_margin_utilization must be in "
                "(0, intraday_margin_utilization]"
            )
        if not (
            0
            < float(self.max_symbol_margin_utilization)
            <= float(self.intraday_margin_utilization)
        ):
            raise ValueError(
                "max_symbol_margin_utilization must be in "
                "(0, intraday_margin_utilization]"
            )
        if int(self.overnight_reduction_minutes) <= 0:
            raise ValueError("overnight_reduction_minutes must be positive")
        if int(self.symbol_loss_streak) <= 0:
            raise ValueError("symbol_loss_streak must be positive")
        if type(self.symbol_loss_cooldown_enabled) is not bool:
            raise ValueError("symbol_loss_cooldown_enabled must be bool")
        if (
            type(self.symbol_loss_pair_window_hours) is not int
            or self.symbol_loss_pair_window_hours <= 0
        ):
            raise ValueError("symbol_loss_pair_window_hours must be a positive int")
        if (
            type(self.symbol_loss_cooldown_hours) is not int
            or self.symbol_loss_cooldown_hours <= 0
        ):
            raise ValueError("symbol_loss_cooldown_hours must be a positive int")
        if not 0 < float(self.symbol_position_scale) <= 1:
            raise ValueError("symbol_position_scale must be in (0, 1]")
        if not 0 < float(self.portfolio_drawdown_threshold) <= 1:
            raise ValueError("portfolio_drawdown_threshold must be in (0, 1]")
        if not 0 < float(self.portfolio_position_scale) <= 1:
            raise ValueError("portfolio_position_scale must be in (0, 1]")
        object.__setattr__(
            self,
            "entry_blocked_session_windows",
            _normalize_entry_windows(self.entry_blocked_session_windows),
        )
        if type(self.daily_circuit_breaker_enabled) is not bool:
            raise ValueError("daily_circuit_breaker_enabled must be bool")
        if (
            not math.isfinite(float(self.daily_circuit_breaker_loss_r))
            or float(self.daily_circuit_breaker_loss_r) <= 0
        ):
            raise ValueError("daily_circuit_breaker_loss_r must be positive")
        if (
            type(self.daily_circuit_breaker_loss_streak) is not int
            or self.daily_circuit_breaker_loss_streak <= 0
        ):
            raise ValueError(
                "daily_circuit_breaker_loss_streak must be a positive int"
            )
        if type(self.pre_break_protection_enabled) is not bool:
            raise ValueError("pre_break_protection_enabled must be bool")
        for name in (
            "pre_break_min_unrealized_r",
            "pre_break_high_gap_min_unrealized_r",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value):
                raise ValueError(f"{name} must be finite")
            object.__setattr__(self, name, value)
        if (
            float(self.pre_break_high_gap_min_unrealized_r)
            < float(self.pre_break_min_unrealized_r)
        ):
            raise ValueError(
                "pre_break_high_gap_min_unrealized_r must not be below "
                "pre_break_min_unrealized_r"
            )
        if not 0 < float(self.pre_break_gap_risk_scale) <= 1:
            raise ValueError("pre_break_gap_risk_scale must be in (0, 1]")
        if (
            type(self.pre_break_lead_minutes) is not int
            or self.pre_break_lead_minutes <= 0
        ):
            raise ValueError("pre_break_lead_minutes must be a positive int")
        if (
            type(self.max_positions_per_sector) is not int
            or self.max_positions_per_sector <= 0
        ):
            raise ValueError("max_positions_per_sector must be a positive int")
        if (
            type(self.high_gap_lookback_days) is not int
            or self.high_gap_lookback_days <= 0
        ):
            raise ValueError("high_gap_lookback_days must be a positive int")
        if not 0 < float(self.high_gap_quantile) < 1:
            raise ValueError("high_gap_quantile must be in (0, 1)")
        if (
            not math.isfinite(float(self.high_gap_ratio_threshold))
            or float(self.high_gap_ratio_threshold) <= 0
        ):
            raise ValueError("high_gap_ratio_threshold must be positive")
        if (
            type(self.order_max_recess_minutes) is not int
            or self.order_max_recess_minutes < 0
        ):
            raise ValueError("order_max_recess_minutes must be a nonnegative int")
        for name in (
            "max_entry_volume_ratio",
            "max_entry_stop_distance_atr",
            "min_entry_range_width_atr",
        ):
            value = float(getattr(self, name))
            if not math.isfinite(value) or value < 0:
                raise ValueError(f"{name} must be finite and nonnegative")
            object.__setattr__(self, name, value)
        range_position = float(self.max_entry_range_position)
        if not math.isfinite(range_position) or not 0 <= range_position <= 1:
            raise ValueError("max_entry_range_position must be in [0, 1]")
        object.__setattr__(self, "max_entry_range_position", range_position)
        if (
            type(self.entry_range_lookback_days) is not int
            or self.entry_range_lookback_days <= 0
        ):
            raise ValueError("entry_range_lookback_days must be a positive int")
        if type(self.chase_high_entry_enabled) is not bool:
            raise ValueError("chase_high_entry_enabled must be bool")
        if type(self.chase_high_virtual_enabled) is not bool:
            raise ValueError("chase_high_virtual_enabled must be bool")
        if type(self.chase_high_lookback) is not int or self.chase_high_lookback <= 0:
            raise ValueError("chase_high_lookback must be a positive int")
        if (
            type(self.chase_high_min_samples) is not int
            or self.chase_high_min_samples <= 0
        ):
            raise ValueError("chase_high_min_samples must be a positive int")
        if self.chase_high_min_samples > self.chase_high_lookback:
            raise ValueError(
                "chase_high_min_samples must not exceed chase_high_lookback"
            )
        prior_r = float(self.chase_high_min_prior_r)
        if not math.isfinite(prior_r):
            raise ValueError("chase_high_min_prior_r must be finite")
        object.__setattr__(self, "chase_high_min_prior_r", prior_r)
        share = float(self.turnover_share_threshold)
        if not math.isfinite(share) or not 0 <= share <= 1:
            raise ValueError("turnover_share_threshold must be in [0, 1]")
        object.__setattr__(self, "turnover_share_threshold", share)
        if (
            type(self.turnover_lookback_days) is not int
            or self.turnover_lookback_days <= 0
        ):
            raise ValueError("turnover_lookback_days must be a positive int")


def _normalize_entry_windows(
    windows: object,
) -> tuple[tuple[str, str], ...]:
    """Validate ``HH:MM`` window pairs and return a normalized tuple."""
    if windows is None:
        return ()
    if isinstance(windows, (str, bytes)):
        raise ValueError("entry_blocked_session_windows must be a sequence of pairs")
    normalized: list[tuple[str, str]] = []
    for window in windows:
        if isinstance(window, (str, bytes)) or len(tuple(window)) != 2:
            raise ValueError(
                "each entry_blocked_session_windows item must be a (start, end) pair"
            )
        start, end = (str(value).strip() for value in window)
        for value in (start, end):
            if not _ENTRY_WINDOW_PATTERN.fullmatch(value):
                raise ValueError(
                    f"entry window bound must be HH:MM in 00:00..23:59, got {value!r}"
                )
        if start == end:
            raise ValueError("entry window start and end must differ")
        normalized.append((start, end))
    if len(normalized) != len(set(normalized)):
        raise ValueError("entry_blocked_session_windows contains duplicate windows")
    return tuple(normalized)


def minutes_of_day(value: str) -> int:
    """Return minutes since midnight for an ``HH:MM`` string."""
    hours, minutes = value.split(":")
    return int(hours) * 60 + int(minutes)


def is_entry_window_blocked(
    minute_of_day: int,
    windows: tuple[tuple[str, str], ...],
) -> bool:
    """Return whether ``minute_of_day`` falls in any half-open blocked window."""
    for start, end in windows:
        start_minute = minutes_of_day(start)
        end_minute = minutes_of_day(end)
        if start_minute < end_minute:
            if start_minute <= minute_of_day < end_minute:
                return True
        elif minute_of_day >= start_minute or minute_of_day < end_minute:
            return True
    return False


__all__ = [
    "MultiTimeframeTrendConfig",
    "is_entry_window_blocked",
    "minutes_of_day",
]
