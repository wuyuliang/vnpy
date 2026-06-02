"""W6 四件套 cfg dataclasses，全部 frozen + post_init 严校验。

设计原则：
- 每条 guard 一份 cfg；字段默认值 = 中国期货实战经验值（见 cta/docs/risk.md §16/§18）
- 所有"按 cluster override"的 dict 默认空 → guard 用全局 fallback
- post_init 校验：避免 negative trigger / 阈值越界等无意义配置
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class LimitMoveGuardConfig:
    """§16.1 涨跌停硬约束。

    near_limit_tolerance_pct: 距板多少 pct 视为"近板"。默认 0.3%（中国商品板距 5-10%
        的 3-6%）。
    block_open_when_near: 近板时拒所有开仓（避免触板被锁）。
    block_open_when_at: 触板时拒所有开仓（成交概率几乎 0）。
    block_close_at_unfavorable_limit: 平仓基本不拦（持仓有出口）；特殊场景可开。
    limit_pct_by_cluster: cluster override；空则用 infer_symbol_limit_pct。
    """

    near_limit_tolerance_pct: float = 0.003
    block_open_when_near: bool = True
    block_open_when_at: bool = True
    block_close_at_unfavorable_limit: bool = False
    limit_pct_by_cluster: dict[str, float] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if not 0.0 <= self.near_limit_tolerance_pct <= 0.05:
            raise ValueError(
                f"near_limit_tolerance_pct must be in [0, 0.05], got {self.near_limit_tolerance_pct}"
            )
        for cl, v in dict(self.limit_pct_by_cluster).items():
            if not 0.0 < float(v) < 0.20:
                raise ValueError(
                    f"limit_pct_by_cluster[{cl!r}] must be in (0, 0.20), got {v}"
                )


@dataclass(frozen=True)
class LiquidityFloorGuardConfig:
    """§16.2 流动性下限。

    指标三选一即可触发：
        volume / N 日中位数  <  volume_floor_ratio                → "稀薄"
        bid_ask_spread_ticks >  max_spread_ticks                   → "宽 spread"
        volume × close / open_interest < min_turnover_ratio        → "OI 占比过低"
    """

    volume_floor_ratio: float = 0.30
    max_spread_ticks: int = 3
    min_turnover_ratio: float = 0.05
    allow_close_orders: bool = True
    bypass_for_symbols: tuple[str, ...] = ()   # 主力合约白名单（如 "RB0", "CU0"）
    require_all_indicators: bool = False        # True = 三个指标都触发才拒；默认任一触发即拒

    def __post_init__(self) -> None:
        if not 0.0 < self.volume_floor_ratio <= 1.0:
            raise ValueError(
                f"volume_floor_ratio must be in (0, 1], got {self.volume_floor_ratio}"
            )
        if self.max_spread_ticks < 0:
            raise ValueError(f"max_spread_ticks must be >= 0, got {self.max_spread_ticks}")
        if not 0.0 < self.min_turnover_ratio <= 1.0:
            raise ValueError(
                f"min_turnover_ratio must be in (0, 1], got {self.min_turnover_ratio}"
            )


@dataclass(frozen=True)
class RolloverFreezeGuardConfig:
    """§16.3 换月禁交。

    场景：
        days_to_expiry < freeze_window_days        → 老合约拒开仓
        days_to_expiry < force_close_days          → 老合约强制平仓
        days_since_main_switch < post_switch_freeze_days  → 新主力 freeze
    """

    freeze_window_days: int = 7
    post_switch_freeze_days: int = 3
    force_close_days: int = 3
    allow_close_during_freeze: bool = True
    days_to_expiry_override_by_cluster: dict[str, int] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.freeze_window_days < 0:
            raise ValueError(f"freeze_window_days must be >= 0, got {self.freeze_window_days}")
        if self.post_switch_freeze_days < 0:
            raise ValueError(
                f"post_switch_freeze_days must be >= 0, got {self.post_switch_freeze_days}"
            )
        if self.force_close_days < 0 or self.force_close_days > self.freeze_window_days:
            raise ValueError(
                f"force_close_days must be in [0, freeze_window_days={self.freeze_window_days}], "
                f"got {self.force_close_days}"
            )
        for cl, v in dict(self.days_to_expiry_override_by_cluster).items():
            if int(v) < 0:
                raise ValueError(
                    f"days_to_expiry_override_by_cluster[{cl!r}] must be >= 0, got {v}"
                )


@dataclass(frozen=True)
class PredictionStaleGuardConfig:
    """§18.2 数据新鲜度。

    predictions.csv mtime 距 now > max_stale_hours → 拒开仓
    model 文件 mtime 距 now > max_model_age_days  → warning（不拦，记日志）
    """

    max_stale_hours: float = 4.0
    max_model_age_days: float = 14.0
    predictions_path: str = ""            # 由 caller 注入
    model_path: str = ""                  # 由 caller 注入；空则跳过 model age check
    block_open_on_stale: bool = True
    block_close_on_stale: bool = False
    cache_seconds: float = 30.0           # mtime 缓存（避免每订单一次 stat IO，见 review §4.4 P1-4）

    def __post_init__(self) -> None:
        if self.max_stale_hours <= 0:
            raise ValueError(f"max_stale_hours must be > 0, got {self.max_stale_hours}")
        if self.max_model_age_days <= 0:
            raise ValueError(f"max_model_age_days must be > 0, got {self.max_model_age_days}")
        if self.cache_seconds < 0:
            raise ValueError(f"cache_seconds must be >= 0, got {self.cache_seconds}")


@dataclass(frozen=True)
class ConsecutiveLossGuardConfig:
    """§17.2 连续亏损暂停。

    同 (cluster, symbol, signal_type) 在 lookback_days 内连续亏 ≥ n_consecutive_losses
    笔 → 冷却 cooldown_hours。开仓拒；平仓允许。
    """

    n_consecutive_losses: int = 3
    cooldown_hours: int = 24
    lookback_days: int = 7
    state_path: str = "cta/run/state/consecutive_loss_state.json"
    apply_to_groups: tuple[str, ...] = ("cluster", "symbol", "signal_type")
    block_close_orders: bool = False

    def __post_init__(self) -> None:
        if self.n_consecutive_losses < 1:
            raise ValueError(
                f"n_consecutive_losses must be >= 1, got {self.n_consecutive_losses}"
            )
        if self.cooldown_hours < 0:
            raise ValueError(f"cooldown_hours must be >= 0, got {self.cooldown_hours}")
        if self.lookback_days < 1:
            raise ValueError(f"lookback_days must be >= 1, got {self.lookback_days}")
        allowed = {"cluster", "symbol", "signal_type", "interval"}
        for g in self.apply_to_groups:
            if str(g).strip().lower() not in allowed:
                raise ValueError(
                    f"apply_to_groups item must be one of {allowed}, got {g!r}"
                )


@dataclass(frozen=True)
class SignalConcentrationGuardConfig:
    """§17.3 同类信号并发上限。

    防止 cross_sectional rotation 一次 30+ 同质 momentum 信号同时下单 → 风险敞口集中。
    """

    max_per_signal_type_per_bar: int = 5
    max_per_cluster_per_bar: int = 3
    reset_at_session_open: bool = True

    def __post_init__(self) -> None:
        if self.max_per_signal_type_per_bar < 1:
            raise ValueError(
                f"max_per_signal_type_per_bar must be >= 1, "
                f"got {self.max_per_signal_type_per_bar}"
            )
        if self.max_per_cluster_per_bar < 1:
            raise ValueError(
                f"max_per_cluster_per_bar must be >= 1, "
                f"got {self.max_per_cluster_per_bar}"
            )


@dataclass(frozen=True)
class ScoreDistributionDriftConfig:
    """§18.1 模型分漂移监控参数。"""

    train_distribution_path: str = "cta/model/manifests/score_distribution_train_latest.json"
    drift_metric: str = "kl"  # kl / ks / wasserstein
    warning_threshold: float = 0.5
    critical_threshold: float = 1.0
    emergency_threshold: float = 2.0
    min_samples_for_assessment: int = 100
    rolling_window_hours: int = 4
    block_open_on_critical: bool = True
    block_close_on_critical: bool = False

    def __post_init__(self) -> None:
        if self.drift_metric not in {"kl", "ks", "wasserstein"}:
            raise ValueError(
                f"drift_metric must be one of kl/ks/wasserstein, got {self.drift_metric!r}"
            )
        if self.warning_threshold < 0.0:
            raise ValueError(
                f"warning_threshold must be >= 0, got {self.warning_threshold}"
            )
        if self.critical_threshold < self.warning_threshold:
            raise ValueError(
                "critical_threshold must be >= warning_threshold, "
                f"got {self.critical_threshold} < {self.warning_threshold}"
            )
        if self.emergency_threshold < self.critical_threshold:
            raise ValueError(
                "emergency_threshold must be >= critical_threshold, "
                f"got {self.emergency_threshold} < {self.critical_threshold}"
            )
        if self.min_samples_for_assessment < 1:
            raise ValueError(
                "min_samples_for_assessment must be >= 1, "
                f"got {self.min_samples_for_assessment}"
            )
        if self.rolling_window_hours < 1:
            raise ValueError(
                f"rolling_window_hours must be >= 1, got {self.rolling_window_hours}"
            )


@dataclass(frozen=True)
class ProfitGiveBackConfig:
    """§19.1 盈利回吐保护参数。"""

    activation_pnl_pct: float = 0.03
    give_back_ratio: float = 0.50
    block_new_opens_after_trigger: bool = True
    reset_at_session_open: bool = True

    def __post_init__(self) -> None:
        if not 0.0 <= self.activation_pnl_pct <= 1.0:
            raise ValueError(
                f"activation_pnl_pct must be in [0,1], got {self.activation_pnl_pct}"
            )
        if not 0.0 < self.give_back_ratio <= 1.0:
            raise ValueError(
                f"give_back_ratio must be in (0,1], got {self.give_back_ratio}"
            )


__all__ = [
    "ConsecutiveLossGuardConfig",
    "LimitMoveGuardConfig",
    "LiquidityFloorGuardConfig",
    "PredictionStaleGuardConfig",
    "ProfitGiveBackConfig",
    "RolloverFreezeGuardConfig",
    "ScoreDistributionDriftConfig",
    "SignalConcentrationGuardConfig",
]
