"""Sizing 层 cfg dataclasses（W7/W8 三件套）。

每个 Sizer 一份 cfg；与 guards/config.py 同款 frozen + post_init。
"""
from __future__ import annotations

from dataclasses import dataclass, field


def _default_weekend_carry_mult_by_cluster() -> dict[str, float]:
    """周末隔仓默认 mult：带夜盘品种 0.5、无夜盘 0.3、贵金属 0.6（避险属性强）。"""
    return {
        "metal": 0.5, "chemical": 0.5, "agri": 0.5, "black": 0.5,
        "index": 0.3, "bond": 0.3, "precious": 0.6, "other": 0.4,
    }


@dataclass(frozen=True)
class HolidayPositionReducerConfig:
    """§16.4 节假日降仓。

    距下一个法定节假日 ≤ pre_holiday_taper_days → 按线性降仓
    距上一个法定节假日结束 ≤ post_holiday_warmup_days → 仍降仓

    缩仓公式：
        days_until_holiday = N
        mult = 1.0 - (1.0 - floor_mult) × max(0, (pre_holiday_taper_days - N + 1)) / pre_holiday_taper_days
        N=0  → mult = floor_mult
        N=pre_holiday_taper_days → mult ≈ 1.0
    """

    holidays_csv: str = ""        # 由 caller 注入；空则不缩仓（fail-open）
    pre_holiday_taper_days: int = 5
    post_holiday_warmup_days: int = 2
    floor_mult: float = 0.2
    apply_only_to_overnight_carry: bool = False
    inverse_for_clusters: tuple[str, ...] = ()    # 这些 cluster 节前反而满仓（如 "bond" 避险）

    def __post_init__(self) -> None:
        if self.pre_holiday_taper_days < 0:
            raise ValueError(
                f"pre_holiday_taper_days must be >= 0, got {self.pre_holiday_taper_days}"
            )
        if self.post_holiday_warmup_days < 0:
            raise ValueError(
                f"post_holiday_warmup_days must be >= 0, got {self.post_holiday_warmup_days}"
            )
        if not 0.0 < self.floor_mult <= 1.0:
            raise ValueError(f"floor_mult must be in (0,1], got {self.floor_mult}")


@dataclass(frozen=True)
class VolatilityRegimeScalerConfig:
    """§16.6 波动率自适应。

    候选行需含 ``realized_vol_pctl`` 或类似列（0-100）；否则 fail-open mult=1.0。

    分段映射：
        vol_pctl < edges[0]                → mults[0]    (低 vol 加仓)
        edges[i] <= vol_pctl < edges[i+1] → mults[i+1]
        vol_pctl >= edges[-1]              → mults[-1]   (极端高 vol 砍仓)

    默认：
        < 30        → 1.20 (低 vol 加仓 20%)
        [30, 70)    → 1.00
        [70, 85)    → 0.80
        [85, 95)    → 0.60
        >= 95       → 0.40

    cap_mult：mults 最大上限（避免叠加 > 1 的指数膨胀）
    floor_mult：mults 最低下限
    """

    vol_pctl_edges: tuple[float, ...] = (30.0, 70.0, 85.0, 95.0)
    mults: tuple[float, ...] = (1.20, 1.00, 0.80, 0.60, 0.40)
    cap_mult: float = 1.30
    floor_mult: float = 0.30
    vol_pctl_columns: tuple[str, ...] = (
        "realized_vol_pctl", "realized_vol_20d_pctl",
        "atr_pctl", "atr_pct_pctl",
    )

    def __post_init__(self) -> None:
        if len(self.mults) != len(self.vol_pctl_edges) + 1:
            raise ValueError(
                f"mults length must = vol_pctl_edges + 1, "
                f"got mults={self.mults} edges={self.vol_pctl_edges}"
            )
        if list(self.vol_pctl_edges) != sorted(self.vol_pctl_edges):
            raise ValueError(
                f"vol_pctl_edges must be strictly ascending, got {self.vol_pctl_edges}"
            )
        for e in self.vol_pctl_edges:
            if not 0.0 <= float(e) <= 100.0:
                raise ValueError(f"vol_pctl_edges item must be in [0,100], got {e}")
        if not 0.0 < self.floor_mult <= self.cap_mult:
            raise ValueError(
                f"floor_mult must be in (0, cap_mult={self.cap_mult}], got {self.floor_mult}"
            )
        if not self.vol_pctl_columns:
            raise ValueError("vol_pctl_columns must contain at least 1 candidate")


@dataclass(frozen=True)
class NightSessionCarryConfig:
    """§16.5 夜盘/周末隔仓。

    周五日盘最后 N 分钟（默认 14:30 之后）→ 按 cluster mult 缩仓；
    周末持仓（在 close 前 30 分钟评估）按 cluster mult 应用。

    本组件 sizing 视角：在指定时间窗内对开仓 lots 应用 cluster mult。
    """

    enable_friday_taper: bool = True
    friday_taper_after_hhmm: str = "14:30"
    weekend_carry_mult_by_cluster: dict[str, float] = field(
        default_factory=_default_weekend_carry_mult_by_cluster,
    )
    default_mult: float = 0.5

    def __post_init__(self) -> None:
        # HH:MM 验证
        try:
            h, m = self.friday_taper_after_hhmm.split(":")
            hh = int(h)
            mm = int(m)
        except (ValueError, AttributeError):
            raise ValueError(
                f"friday_taper_after_hhmm must be HH:MM, got {self.friday_taper_after_hhmm!r}"
            )
        if not (0 <= hh <= 23 and 0 <= mm <= 59):
            raise ValueError(
                f"friday_taper_after_hhmm hours/minutes invalid: {self.friday_taper_after_hhmm}"
            )
        if not 0.0 < self.default_mult <= 1.0:
            raise ValueError(f"default_mult must be in (0,1], got {self.default_mult}")
        for cl, m in dict(self.weekend_carry_mult_by_cluster).items():
            if not 0.0 < float(m) <= 1.0:
                raise ValueError(
                    f"weekend_carry_mult_by_cluster[{cl!r}] must be in (0,1], got {m}"
                )


@dataclass(frozen=True)
class DailyVaRBudgetConfig:
    """§19.2 Daily VaR budget 参数。"""

    budget_bp_by_cluster: dict[str, float] = field(default_factory=dict)
    default_budget_bp: float = 60.0
    account_budget_bp: float = 250.0
    warning_ratio: float = 0.95
    warning_mult: float = 0.5
    reset_at_session_open: bool = True

    def __post_init__(self) -> None:
        if self.default_budget_bp <= 0.0:
            raise ValueError(
                f"default_budget_bp must be > 0, got {self.default_budget_bp}"
            )
        if self.account_budget_bp <= 0.0:
            raise ValueError(
                f"account_budget_bp must be > 0, got {self.account_budget_bp}"
            )
        if not 0.0 < self.warning_ratio <= 1.0:
            raise ValueError(f"warning_ratio must be in (0,1], got {self.warning_ratio}")
        if not 0.0 < self.warning_mult <= 1.0:
            raise ValueError(f"warning_mult must be in (0,1], got {self.warning_mult}")
        for cl, v in dict(self.budget_bp_by_cluster).items():
            if float(v) <= 0.0:
                raise ValueError(f"budget_bp_by_cluster[{cl!r}] must be > 0, got {v}")


@dataclass(frozen=True)
class ExecutionQualityConfig:
    """§19.3 执行质量反馈参数。"""

    slippage_ratio_threshold: float = 3.0
    reject_rate_threshold: float = 0.05
    avg_price_deviation_threshold: float = 0.005
    rolling_window_days: int = 7
    mults: tuple[float, float, float] = (0.5, 0.7, 0.3)
    min_trades_for_assessment: int = 10

    def __post_init__(self) -> None:
        if self.slippage_ratio_threshold <= 0.0:
            raise ValueError(
                "slippage_ratio_threshold must be > 0, "
                f"got {self.slippage_ratio_threshold}"
            )
        if not 0.0 <= self.reject_rate_threshold <= 1.0:
            raise ValueError(
                f"reject_rate_threshold must be in [0,1], got {self.reject_rate_threshold}"
            )
        if self.avg_price_deviation_threshold < 0.0:
            raise ValueError(
                "avg_price_deviation_threshold must be >= 0, "
                f"got {self.avg_price_deviation_threshold}"
            )
        if self.rolling_window_days < 1:
            raise ValueError(
                f"rolling_window_days must be >= 1, got {self.rolling_window_days}"
            )
        if self.min_trades_for_assessment < 1:
            raise ValueError(
                "min_trades_for_assessment must be >= 1, "
                f"got {self.min_trades_for_assessment}"
            )
        if len(self.mults) != 3:
            raise ValueError(f"mults must contain 3 items, got {self.mults}")
        for m in self.mults:
            if not 0.0 < float(m) <= 1.0:
                raise ValueError(f"each multiplier must be in (0,1], got {m}")


@dataclass(frozen=True)
class ProfitGiveBackConfig:
    """§19.1 盈利回吐保护参数（供 sizing/guard 复用）。"""

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


def _default_signal_type_size_multiplier() -> dict[str, float]:
    """与 OotEvaluationConfig.signal_type_size_multiplier 同口径（三层一致）。"""
    return {
        "bull_pullback_continuation": 4.0,
        "breakout_pullback_continuation": 3.0,
        "cross_sectional_momentum": 0.8,
        "trend_acceleration_breakout": 0.5,
        "atr_breakout": 0.2,
        "donchian_breakout": 0.25,
        "tight_range_breakout": 0.15,
    }


@dataclass(frozen=True)
class SignalTypeSizeScalerConfig:
    """按 signal_type 缩放 lots（sim/live 与 OOT 的 signal_type_size_multiplier 对齐）。

    候选行需含 ``signal_type``；缺失或未配置 → mult=1.0（fail-open）。
    mult > 1 放大（pullback 类）、< 1 缩小（atr_breakout）。
    与 OOT 的差异：OOT 把系数作用在单笔名义上限上，这里直接乘 lots——两者意图一致。
    """

    multiplier_by_signal_type: dict[str, float] = field(
        default_factory=_default_signal_type_size_multiplier
    )
    floor_mult: float = 0.0
    cap_mult: float = 5.0

    def __post_init__(self) -> None:
        norm: dict[str, float] = {}
        for key, value in dict(self.multiplier_by_signal_type).items():
            st = str(key).strip().lower()
            if not st:
                continue
            v = float(value)
            if not 0.0 < v <= self.cap_mult:
                raise ValueError(
                    f"multiplier_by_signal_type[{key}] must be in (0,{self.cap_mult}], got {v}"
                )
            norm[st] = v
        object.__setattr__(self, "multiplier_by_signal_type", norm)
        if self.floor_mult < 0.0:
            raise ValueError(f"floor_mult must be >= 0, got {self.floor_mult}")
        if self.cap_mult < self.floor_mult:
            raise ValueError("cap_mult must be >= floor_mult")


__all__ = [
    "DailyVaRBudgetConfig",
    "ExecutionQualityConfig",
    "HolidayPositionReducerConfig",
    "NightSessionCarryConfig",
    "ProfitGiveBackConfig",
    "SignalTypeSizeScalerConfig",
    "VolatilityRegimeScalerConfig",
]
