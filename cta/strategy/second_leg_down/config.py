"""Configuration for the one-minute Second Leg Down short strategy."""
from __future__ import annotations

from dataclasses import dataclass, replace
import math

from cta.config.replay_common import BaseReplayConfig


@dataclass(frozen=True)
class SecondLegDownConfig(BaseReplayConfig):
    """Causal signal, sizing, and exit parameters."""

    risk_per_trade: float = 0.005
    max_risk_per_trade: float = 0.005
    entry_blocked_session_windows: tuple[tuple[str, str], ...] = ()
    profit_floor_enabled: bool = True
    profit_floor_arm_r: float = 0.5
    profit_floor_giveback_r: float = 0.5
    profit_floor_giveback_pct: float = 0.0
    no_progress_bars: int = 5
    no_progress_min_r: float = 0.5
    follow_through_bars: int = 3
    follow_through_body_atr_mult: float = 2.0

    ema_fast: int = 5
    ema_mid: int = 10
    ema_slow: int = 20
    # 1 分钟上做归一化用 60 根的简单平均：Wilder 衰减把权重压在最近几分钟，
    # 一根暴力 K 线会自己把要衡量它的阈值抬起来。
    atr_period: int = 60
    atr_method: str = "sma"
    # per_bar: 两根各自 >= big_body_atr_mult × ATR（原始口径）
    # leg:     整条腿净跌幅（第一根开盘 → 最后一根收盘，两根或三根）
    #          >= leg_body_atr_mult × ATR，且两根大阴线各自
    #          >= leg_body_atr_mult × leg_per_bar_fraction × ATR
    body_mode: str = "leg"
    leg_body_atr_mult: float = 4.0
    leg_per_bar_fraction: float = 0.5
    big_body_atr_mult: float = 3.0
    small_body_atr_mult: float = 0.5
    wick_body_ratio: float = 0.15
    allow_three_bar_pattern: bool = True
    volume_surge_mult: float = 2.0
    volume_baseline_bars: int = 60
    volume_baseline_min_samples: int = 15
    entry_block_minutes_after_open: int = 10
    entry_block_minutes_before_close: int = 20
    structure_lookback_bars: int = 60
    require_structure_break: bool = False
    stop_mode: str = "pattern_open"
    stop_atr_mult: float = 1.5
    min_risk_pct: float = 0.002
    max_risk_pct: float = 0.005
    max_capital_share: float = 0.10
    # 一手装不进 10% 时的放宽档：装得进 15% 就按一手开，超过就不开
    single_lot_capital_share: float = 0.15
    # 信号周期（分钟）。1 = 原始 1 分钟；5 = 用 5 分钟合成 K 线找形态，
    # 执行与出场仍然走 1 分钟。以"根"计的窗口都按信号周期解释，见 for_replay()。
    signal_timeframe_minutes: int = 5
    order_ttl_bars: int = 5

    def __post_init__(self) -> None:
        if not 0 < self.ema_fast < self.ema_mid < self.ema_slow:
            raise ValueError("EMA periods must satisfy fast < mid < slow")
        positive_ints = (
            self.atr_period,
            self.volume_baseline_bars,
            self.volume_baseline_min_samples,
            self.structure_lookback_bars,
            self.order_ttl_bars,
        )
        if any(type(value) is not int or value <= 0 for value in positive_ints):
            raise ValueError("strategy windows must be positive integers")
        if self.volume_baseline_min_samples > self.volume_baseline_bars:
            raise ValueError("volume baseline minimum samples exceed its window")
        for name in (
            "entry_block_minutes_after_open",
            "entry_block_minutes_before_close",
            "no_progress_bars",
            "follow_through_bars",
        ):
            value = getattr(self, name)
            if type(value) is not int or value < 0:
                raise ValueError(f"{name} must be a nonnegative integer")
        positive_values = (
            "big_body_atr_mult",
            "leg_body_atr_mult",
            "small_body_atr_mult",
            "volume_surge_mult",
            "stop_atr_mult",
            "min_risk_pct",
            "max_risk_pct",
            "max_capital_share",
            "no_progress_min_r",
            "follow_through_body_atr_mult",
            "profit_floor_arm_r",
            "profit_floor_giveback_r",
        )
        for name in positive_values:
            value = float(getattr(self, name))
            if not math.isfinite(value) or value <= 0:
                raise ValueError(f"{name} must be finite and positive")
        if not 0 <= float(self.wick_body_ratio) <= 1:
            raise ValueError("wick_body_ratio must be in [0, 1]")
        if not 0 < self.min_risk_pct < self.max_risk_pct < 1:
            raise ValueError("risk percentages must satisfy 0 < min < max < 1")
        if not 0 < self.max_capital_share <= 1:
            raise ValueError("max_capital_share must be in (0, 1]")
        if not self.max_capital_share <= self.single_lot_capital_share <= 1:
            raise ValueError(
                "single_lot_capital_share must be between max_capital_share and 1"
            )
        if type(self.signal_timeframe_minutes) is not int or (
            self.signal_timeframe_minutes <= 0
        ):
            raise ValueError("signal_timeframe_minutes must be a positive integer")
        if self.body_mode not in {"per_bar", "leg"}:
            raise ValueError("body_mode must be per_bar or leg")
        if self.atr_method not in {"wilder", "sma"}:
            raise ValueError("atr_method must be wilder or sma")
        fraction = float(self.leg_per_bar_fraction)
        if not math.isfinite(fraction) or not 0 < fraction <= 1:
            raise ValueError("leg_per_bar_fraction must be in (0, 1]")
        if self.stop_mode not in {"pattern_open", "pattern_high", "atr"}:
            raise ValueError("stop_mode must be pattern_open, pattern_high, or atr")
        if type(self.allow_three_bar_pattern) is not bool:
            raise ValueError("allow_three_bar_pattern must be bool")
        if type(self.require_structure_break) is not bool:
            raise ValueError("require_structure_break must be bool")
        if type(self.profit_floor_enabled) is not bool:
            raise ValueError("profit_floor_enabled must be bool")
        pct = float(self.profit_floor_giveback_pct)
        if not math.isfinite(pct) or not 0 <= pct < 1:
            raise ValueError("profit_floor_giveback_pct must be in [0, 1)")

    def for_replay(self) -> "SecondLegDownConfig":
        """Return the config the replay engine should see.

        The engine steps one-minute bars, but ``no_progress_bars`` and
        ``follow_through_bars`` are written in *signal* bars — "5 bars after
        entry" means five bars of whatever timeframe the setup was found on.
        Leaving them unscaled would kill every 5-minute trade inside its first
        signal bar and make the timeframe comparison meaningless.
        """
        step = int(self.signal_timeframe_minutes)
        if step <= 1:
            return self
        return replace(
            self,
            no_progress_bars=int(self.no_progress_bars) * step,
            follow_through_bars=int(self.follow_through_bars) * step,
        )
