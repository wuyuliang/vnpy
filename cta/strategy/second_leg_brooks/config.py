"""Configuration for the Brooks second-leg-down short strategy."""
from __future__ import annotations

from dataclasses import dataclass
import math

from cta.config.replay_common import BaseReplayConfig


@dataclass(frozen=True)
class SecondLegBrooksConfig(BaseReplayConfig):
    """Causal signal, sizing and exit parameters.

    The setup is a three-part structure — a first leg down, a pullback that
    does not undo it, then a bar that resumes the move. The entry bar is
    deliberately *ordinary*: what carries the read is the structure, not one
    violent candle. Demanding a violent candle is what made the earlier
    single-leg formulation both rare and late (it fired into exhaustion).
    """

    # --- 组合与风险（沿用 BaseReplayConfig 的语义） ---
    risk_per_trade: float = 0.005
    max_risk_per_trade: float = 0.005
    entry_blocked_session_windows: tuple[tuple[str, str], ...] = ()

    # --- 信号周期 ---
    signal_timeframe_minutes: int = 5

    # --- 特征 ---
    atr_period: int = 60
    atr_method: str = "sma"
    ema_fast: int = 5
    ema_mid: int = 10
    ema_slow: int = 20
    volume_baseline_bars: int = 60
    volume_baseline_min_samples: int = 15
    structure_lookback_bars: int = 60

    # --- ① 第一段 ---
    first_leg_lookback: int = 20
    first_leg_atr_mult: float = 1.5

    # --- ② 回抽 ---
    pullback_min_bars: int = 2
    pullback_max_bars: int = 6
    pullback_min_ratio: float = 0.20
    pullback_max_ratio: float = 0.90

    # --- ③ 入场根 ---
    entry_body_atr_mult: float = 1.0
    entry_lower_wick_ratio: float = 0.4
    entry_volume_mult: float = 1.2
    require_break_of_swing_low: bool = True
    # 同一个回抽结构只做一次：不去重的话，结构成立后的连续几根都会各开一单，
    # 止损位完全相同，等于把同一笔交易下了三次、风险三倍。
    one_candidate_per_structure: bool = True

    # --- 时间窗（判的是下单那一刻） ---
    entry_block_minutes_after_open: int = 10
    entry_block_minutes_before_close: int = 20

    # --- 仓位 ---
    stop_buffer_ticks: int = 1
    min_risk_pct: float = 0.002
    max_risk_pct: float = 0.005
    max_capital_share: float = 0.10
    single_lot_capital_share: float = 0.15
    order_ttl_bars: int = 5

    # --- 出场 ---
    profit_floor_enabled: bool = True
    profit_floor_arm_r: float = 0.5
    profit_floor_giveback_r: float = 0.5
    profit_floor_giveback_pct: float = 0.0
    no_progress_bars: int = 8
    no_progress_min_r: float = 0.5
    follow_through_bars: int = 3
    follow_through_body_atr_mult: float = 1.0

    def __post_init__(self) -> None:
        if not 0 < self.ema_fast < self.ema_mid < self.ema_slow:
            raise ValueError("EMA periods must satisfy fast < mid < slow")
        positive_ints = (
            "atr_period", "volume_baseline_bars", "volume_baseline_min_samples",
            "structure_lookback_bars", "first_leg_lookback", "order_ttl_bars",
            "signal_timeframe_minutes",
        )
        for name in positive_ints:
            value = getattr(self, name)
            if type(value) is not int or value <= 0:
                raise ValueError(f"{name} must be a positive integer")
        if self.volume_baseline_min_samples > self.volume_baseline_bars:
            raise ValueError("volume baseline minimum samples exceed its window")
        nonnegative_ints = (
            "entry_block_minutes_after_open", "entry_block_minutes_before_close",
            "no_progress_bars", "follow_through_bars", "stop_buffer_ticks",
        )
        for name in nonnegative_ints:
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        if not 1 <= self.pullback_min_bars <= self.pullback_max_bars:
            raise ValueError("pullback bars must satisfy 1 <= min <= max")
        positive_values = (
            "first_leg_atr_mult", "entry_body_atr_mult", "entry_volume_mult",
            "no_progress_min_r", "follow_through_body_atr_mult",
            "profit_floor_arm_r", "profit_floor_giveback_r",
        )
        for name in positive_values:
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not 0 < self.pullback_min_ratio < self.pullback_max_ratio <= 1:
            raise ValueError(
                "pullback ratios must satisfy 0 < min < max <= 1"
            )
        if not 0 <= float(self.entry_lower_wick_ratio) <= 1:
            raise ValueError("entry_lower_wick_ratio must be in [0, 1]")
        if not 0 < self.min_risk_pct < self.max_risk_pct < 1:
            raise ValueError("risk percentages must satisfy 0 < min < max < 1")
        for name in ("max_capital_share", "single_lot_capital_share"):
            value = float(getattr(self, name))
            if not 0 < value <= 1:
                raise ValueError(f"{name} must be in (0, 1]")
        if self.atr_method not in {"wilder", "sma"}:
            raise ValueError("atr_method must be wilder or sma")
        for name in (
            "require_break_of_swing_low",
            "one_candidate_per_structure",
            "profit_floor_enabled",
        ):
            if type(getattr(self, name)) is not bool:
                raise ValueError(f"{name} must be bool")
        pct = float(self.profit_floor_giveback_pct)
        if not math.isfinite(pct) or not 0 <= pct < 1:
            raise ValueError("profit_floor_giveback_pct must be in [0, 1)")

    def for_replay(self) -> "SecondLegBrooksConfig":
        """Scale the bar-counted exit windows to execution (1-minute) bars."""
        from dataclasses import replace

        step = max(1, int(self.signal_timeframe_minutes))
        if step == 1:
            return self
        return replace(
            self,
            no_progress_bars=self.no_progress_bars * step,
            follow_through_bars=self.follow_through_bars * step,
        )
