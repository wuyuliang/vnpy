"""RiskSystemConfig：每个子系统的开关 + 关键参数 + post_init 校验。

设计：
- 子系统三连独立开关（quantile_threshold / bucket_scaling / linear_dd_scaler / dynamic_bump）
- 默认值与现状保持等价：
    * enable_quantile_threshold=True 但 quantile_field="p70"，行为退化为"用 train+valid 的 P70"
    * enable_bucket_scaling=False（需 30 日 sim soak 预热后再开）
    * enable_linear_dd_scaler=True 但 trigger_pct=0.01（dd 不到 1% 完全无影响）
    * enable_dynamic_bump=True 但 dd<1% 时 +0pp（等价无效）
- 所有参数都可被 OOT/sim cfg 覆盖，便于 ad-hoc 调参

不变量：post_init 严格校验，避免无意义值（trigger_pct < 0 / pp_per_pct < 0 等）。
"""
from __future__ import annotations

from dataclasses import dataclass, field

from cta.risk.sizing.config import (
    DailyVaRBudgetConfig,
    ExecutionQualityConfig,
    HolidayPositionReducerConfig,
    NightSessionCarryConfig,
    ProfitGiveBackConfig,
    SignalTypeSizeScalerConfig,
    VolatilityRegimeScalerConfig,
)


@dataclass(frozen=True)
class RiskSystemConfig:
    """风控系统总配置。

    嵌入到 ``OotEvaluationConfig.risk_system: RiskSystemConfig | None`` 由调用方提供；
    None 时表示完全关闭风控子系统（与历史行为一致）。
    """

    # ── 子系统开关 ───────────────────────────────────────────────────
    enable_quantile_threshold: bool = True
    enable_bucket_scaling: bool = False           # 需 sim soak 预热后再 default-on
    enable_linear_dd_scaler: bool = True
    enable_dynamic_bump: bool = True
    enable_holiday_position_reducer: bool = False
    enable_volatility_regime_scaler: bool = False
    enable_night_session_carry: bool = False
    enable_daily_var_budget: bool = False
    enable_execution_quality: bool = False
    enable_profit_give_back_sizer: bool = False
    enable_signal_type_size_scaler: bool = False  # sim/live 三层一致用：与 OOT signal_type_size_multiplier 对齐

    # ── 子系统①：quantile threshold ──────────────────────────────────
    # manifest 路径（None → 用 latest.json symlink）
    quantile_manifest_path: str | None = None
    # 取哪一档作为 threshold（"p50" / "p60" / "p70" / "p80" / "p90" / "p95"）
    quantile_field: str = "p70"
    # 当 manifest 命中但分数不在 [base-50, base+50] 范围内时，警告且 fallback 到 base
    quantile_sanity_band_pp: float = 50.0

    # ── 子系统②：bucket scaling ──────────────────────────────────────
    bucket_window_days: int = 30
    bucket_min_trades: int = 20
    # bp 相对账户净值的步进（**严格升序**：从最严到最宽）
    # 默认 (-10, -5, 0)：< -10bp → 最严，>= 0 → mult=1.0；mults 长度 = bp_steps + 1
    bucket_pnl_bp_steps: tuple[float, ...] = (-10.0, -5.0, 0.0)
    # mults[0] 对应 < bp_steps[0]（最严段），mults[-1] 对应 >= bp_steps[-1]（最宽段）
    bucket_pnl_mults: tuple[float, ...] = (0.5, 0.7, 0.9, 1.0)
    bucket_floor_mult: float = 0.3
    bucket_state_path: str = "cta/run/state/bucket_pnl_state.json"
    # 候选分到桶里的边界（百分位）：< 60 → 不进桶（也不算缩仓 → mult=1.0）
    bucket_pctl_edges: tuple[float, ...] = (60.0, 70.0, 80.0, 90.0)
    # 桶名（与 edges 一一对应，长度 = len(edges)）
    bucket_names: tuple[str, ...] = ("p60-70", "p70-80", "p80-90", "p90+")

    # ── 子系统③：linear DD scaler ────────────────────────────────────
    # dd 从 trigger 起每 step 缩 step_mult，下限 floor
    linear_dd_trigger_pct: float = 0.01
    linear_dd_step_pct: float = 0.01
    linear_dd_step_mult: float = 0.10
    linear_dd_floor_mult: float = 0.10
    # 滞后：dd 回到 (trigger - hysteresis) 之下才解除当档缩仓
    linear_dd_hysteresis_pct: float = 0.005

    # ── 子系统③：dynamic bump（阈值抬高） ─────────────────────────────
    # 每超过 trigger 一个 step（默认 1pp dd），阈值上抬 pp_per_step
    dynamic_bump_trigger_pct: float = 0.01
    dynamic_bump_step_pct: float = 0.01
    dynamic_bump_pp_per_step: float = 5.0
    dynamic_bump_cap_pp: float = 25.0           # 累计抬高上限
    dynamic_bump_threshold_cap_pp: float = 95.0  # 最终阈值天花板（pctl 单位）

    # ── W7-W10: 可选附加 sizing 组件配置 ──────────────────────────────
    holiday_position_reducer: HolidayPositionReducerConfig = field(
        default_factory=HolidayPositionReducerConfig
    )
    volatility_regime_scaler: VolatilityRegimeScalerConfig = field(
        default_factory=VolatilityRegimeScalerConfig
    )
    signal_type_size_scaler: SignalTypeSizeScalerConfig = field(
        default_factory=SignalTypeSizeScalerConfig
    )
    night_session_carry: NightSessionCarryConfig = field(
        default_factory=NightSessionCarryConfig
    )
    daily_var_budget: DailyVaRBudgetConfig = field(
        default_factory=DailyVaRBudgetConfig
    )
    execution_quality: ExecutionQualityConfig = field(
        default_factory=ExecutionQualityConfig
    )
    profit_give_back: ProfitGiveBackConfig = field(
        default_factory=ProfitGiveBackConfig
    )

    # ── 通用 ─────────────────────────────────────────────────────────
    # candidate 没有 score 时是否放行（fail-open）
    pass_through_when_score_missing: bool = True
    # orchestrator debug dict 是否持久化（开销大，仅 OOT/调试用）
    record_debug_trace: bool = False

    def __post_init__(self) -> None:
        if self.quantile_field not in {"p50", "p60", "p70", "p80", "p90", "p95"}:
            raise ValueError(
                f"quantile_field must be one of p50/60/70/80/90/95, got {self.quantile_field!r}"
            )
        if self.bucket_window_days < 1:
            raise ValueError(f"bucket_window_days must be >= 1, got {self.bucket_window_days}")
        if self.bucket_min_trades < 1:
            raise ValueError(f"bucket_min_trades must be >= 1, got {self.bucket_min_trades}")
        if len(self.bucket_pnl_mults) != len(self.bucket_pnl_bp_steps) + 1:
            raise ValueError(
                f"bucket_pnl_mults length must = bp_steps + 1, "
                f"got mults={self.bucket_pnl_mults} steps={self.bucket_pnl_bp_steps}"
            )
        if list(self.bucket_pnl_bp_steps) != sorted(self.bucket_pnl_bp_steps):
            raise ValueError(
                f"bucket_pnl_bp_steps must be strictly ascending, got {self.bucket_pnl_bp_steps}"
            )
        if not 0.0 < self.bucket_floor_mult <= 1.0:
            raise ValueError(f"bucket_floor_mult must be in (0,1], got {self.bucket_floor_mult}")
        if len(self.bucket_names) != len(self.bucket_pctl_edges):
            raise ValueError(
                f"bucket_names length must = pctl_edges length, "
                f"got names={self.bucket_names} edges={self.bucket_pctl_edges}"
            )
        # 单调
        edges = list(self.bucket_pctl_edges)
        if edges != sorted(edges):
            raise ValueError(f"bucket_pctl_edges must be monotonically increasing, got {edges}")
        if self.linear_dd_trigger_pct < 0.0 or self.linear_dd_trigger_pct > 1.0:
            raise ValueError(f"linear_dd_trigger_pct must be in [0,1], got {self.linear_dd_trigger_pct}")
        if self.linear_dd_step_pct <= 0.0:
            raise ValueError(f"linear_dd_step_pct must be > 0, got {self.linear_dd_step_pct}")
        if not 0.0 <= self.linear_dd_step_mult <= 1.0:
            raise ValueError(f"linear_dd_step_mult must be in [0,1], got {self.linear_dd_step_mult}")
        if not 0.0 < self.linear_dd_floor_mult <= 1.0:
            raise ValueError(f"linear_dd_floor_mult must be in (0,1], got {self.linear_dd_floor_mult}")
        if self.linear_dd_hysteresis_pct < 0.0:
            raise ValueError(f"linear_dd_hysteresis_pct must be >= 0, got {self.linear_dd_hysteresis_pct}")
        if self.dynamic_bump_pp_per_step < 0.0:
            raise ValueError(f"dynamic_bump_pp_per_step must be >= 0, got {self.dynamic_bump_pp_per_step}")
        if self.dynamic_bump_cap_pp < 0.0:
            raise ValueError(f"dynamic_bump_cap_pp must be >= 0, got {self.dynamic_bump_cap_pp}")
        if not 0.0 <= self.dynamic_bump_threshold_cap_pp <= 100.0:
            raise ValueError(
                f"dynamic_bump_threshold_cap_pp must be in [0,100], got {self.dynamic_bump_threshold_cap_pp}"
            )


__all__ = ["RiskSystemConfig"]
