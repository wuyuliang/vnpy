"""RiskOrchestrator: 串联 threshold + sizing 两层组件，统一评估单条 candidate。

设计：
1. 由 ``from_config`` 一次性按 cfg 构建出所有 adjusters / scalers，避免 caller 自己拼。
2. ``evaluate(ctx, original_lots)`` 顺序：
       base_threshold → 串联 threshold_adjusters → effective_threshold
       score < effective_threshold → BLOCK(stage=threshold)
       lots = original_lots → 串联 position_scalers → adjusted_lots
       adjusted_lots <= 0 → BLOCK(stage=sizing)
       通过 → AdjustedDecision(passed=True, adjusted_lots, effective_threshold)
3. **不在 orchestrator 内跑 _BaseRule（pre-trade kill）**——那部分由 cta.live.risk.RiskGuard
   在 sim/live 入口承担（避免重复）；orchestrator 仅负责动态阈值 + 缩仓。

fail-open：
- ctx.candidate 没有 score → 若 cfg.pass_through_when_score_missing=True 直接放行（不评估阈值层，
  仅跑 sizing）
- 任何 adjuster / scaler 抛异常 → 记录到 debug 后退到上一阶段输出
"""
from __future__ import annotations

import logging
from typing import Callable, Iterable

from cta.risk.base import (
    AdjustedDecision,
    PositionScaler,
    SignalContext,
    ThresholdAdjuster,
    extract_score,
)
from cta.risk.config import RiskSystemConfig
from cta.risk.sizing.bucket_scaling import BucketScalingSizer
from cta.risk.sizing.daily_var_budget import DailyVaRBudgetSizer
from cta.risk.sizing.execution_quality_scaler import ExecutionQualityScaler
from cta.risk.sizing.holiday_position_reducer import HolidayPositionReducer
from cta.risk.sizing.linear_dd_scaler import LinearDdScaler
from cta.risk.sizing.night_session_carry import NightSessionCarryRule
from cta.risk.sizing.profit_give_back import ProfitGiveBackSizer
from cta.risk.sizing.signal_type_size_scaler import SignalTypePositionScaler
from cta.risk.sizing.volatility_regime_scaler import VolatilityRegimeScaler
from cta.risk.state.bucket_pnl_tracker import BucketPnlTracker
from cta.risk.state.daily_var_tracker import DailyVaRTracker
from cta.risk.state.execution_quality_tracker import ExecutionQualityTracker
from cta.risk.state.intraday_profit_tracker import IntradayProfitTracker
from cta.risk.state.score_quantile_manifest import ScoreQuantileManifest
from cta.risk.threshold.dynamic_bump import DynamicBumpAdjuster
from cta.risk.threshold.quantile_threshold import QuantileThresholdAdjuster
from cta.risk.threshold.static_threshold import StaticThresholdAdjuster

logger = logging.getLogger(__name__)


class RiskOrchestrator:
    """串联三层组件的统一评估入口。"""

    def __init__(
        self,
        cfg: RiskSystemConfig,
        *,
        threshold_adjusters: Iterable[ThresholdAdjuster] | None = None,
        position_scalers: Iterable[PositionScaler] | None = None,
        base_threshold_provider: Callable[[SignalContext], float] | None = None,
    ) -> None:
        self.cfg = cfg
        self._adjusters: list[ThresholdAdjuster] = list(threshold_adjusters or [])
        self._scalers: list[PositionScaler] = list(position_scalers or [])
        self._base_threshold_provider = base_threshold_provider

    # ── factory ─────────────────────────────────────────────────────

    @classmethod
    def from_config(
        cls,
        cfg: RiskSystemConfig,
        *,
        quantile_manifest: ScoreQuantileManifest | None = None,
        bucket_tracker: BucketPnlTracker | None = None,
        static_overrides: dict[str, float] | None = None,
        global_default_threshold: float = 70.0,
        base_threshold_provider: Callable[[SignalContext], float] | None = None,
        extra_threshold_adjusters: Iterable[ThresholdAdjuster] | None = None,
        extra_position_scalers: Iterable[PositionScaler] | None = None,
        daily_var_tracker: DailyVaRTracker | None = None,
        execution_quality_tracker: ExecutionQualityTracker | None = None,
        intraday_profit_tracker: IntradayProfitTracker | None = None,
    ) -> "RiskOrchestrator":
        """按 cfg 默认装配 orchestrator。

        Parameters
        ----------
        cfg
            ``RiskSystemConfig`` 实例（控制每子项是否启用）。
        quantile_manifest
            若 None 且 cfg.enable_quantile_threshold=True，将尝试从
            cfg.quantile_manifest_path 加载；都失败则 quantile adjuster 不加入。
        bucket_tracker
            若 None 且 cfg.enable_bucket_scaling=True，将从 cfg.bucket_state_path 构造。
        static_overrides
            ``OotEvaluationConfig.trade_filter_percentile_threshold_by_cluster_interval`` 拷贝；
            非 None 时会作为 base layer。
        extra_threshold_adjusters / extra_position_scalers
            额外注入的自定义组件（放在默认组件之后）。
        """
        adjusters: list[ThresholdAdjuster] = []
        # base 层：static_threshold（始终加；行为等价 fallback 到 global_default）
        if static_overrides is not None:
            adjusters.append(StaticThresholdAdjuster(
                overrides=static_overrides,
                global_default=global_default_threshold,
            ))
        # ① quantile
        if cfg.enable_quantile_threshold:
            manifest = quantile_manifest
            if manifest is None and cfg.quantile_manifest_path:
                manifest = ScoreQuantileManifest.from_json(cfg.quantile_manifest_path)
            if manifest is not None and len(manifest) > 0:
                adjusters.append(QuantileThresholdAdjuster(
                    manifest=manifest,
                    quantile_field=cfg.quantile_field,
                    sanity_band_pp=cfg.quantile_sanity_band_pp,
                    emit_pctl=True,
                ))
            else:
                logger.info("RiskOrchestrator: quantile manifest empty/missing; skip quantile adjuster")
        # ③ dynamic bump
        if cfg.enable_dynamic_bump:
            adjusters.append(DynamicBumpAdjuster(
                trigger_pct=cfg.dynamic_bump_trigger_pct,
                step_pct=cfg.dynamic_bump_step_pct,
                pp_per_step=cfg.dynamic_bump_pp_per_step,
                cap_pp=cfg.dynamic_bump_cap_pp,
                threshold_cap_pp=cfg.dynamic_bump_threshold_cap_pp,
            ))
        if extra_threshold_adjusters:
            adjusters.extend(extra_threshold_adjusters)

        scalers: list[PositionScaler] = []
        # ② bucket scaling
        if cfg.enable_bucket_scaling:
            tracker = bucket_tracker
            if tracker is None:
                tracker = BucketPnlTracker.from_path(
                    cfg.bucket_state_path,
                    window_days=cfg.bucket_window_days,
                )
            scalers.append(BucketScalingSizer(
                tracker=tracker,
                bp_steps=cfg.bucket_pnl_bp_steps,
                mults=cfg.bucket_pnl_mults,
                floor_mult=cfg.bucket_floor_mult,
                min_trades=cfg.bucket_min_trades,
                bucket_edges=cfg.bucket_pctl_edges,
                bucket_names=cfg.bucket_names,
            ))
        # ③ linear dd
        if cfg.enable_linear_dd_scaler:
            scalers.append(LinearDdScaler(
                trigger_pct=cfg.linear_dd_trigger_pct,
                step_pct=cfg.linear_dd_step_pct,
                step_mult=cfg.linear_dd_step_mult,
                floor_mult=cfg.linear_dd_floor_mult,
                hysteresis_pct=cfg.linear_dd_hysteresis_pct,
            ))
        if cfg.enable_holiday_position_reducer:
            scalers.append(HolidayPositionReducer(cfg=cfg.holiday_position_reducer))
        if cfg.enable_volatility_regime_scaler:
            scalers.append(VolatilityRegimeScaler(cfg=cfg.volatility_regime_scaler))
        if cfg.enable_signal_type_size_scaler:
            scalers.append(SignalTypePositionScaler(cfg=cfg.signal_type_size_scaler))
        if cfg.enable_night_session_carry:
            scalers.append(NightSessionCarryRule(cfg=cfg.night_session_carry))
        if cfg.enable_daily_var_budget:
            scalers.append(
                DailyVaRBudgetSizer(
                    cfg=cfg.daily_var_budget,
                    tracker=daily_var_tracker,
                )
            )
        if cfg.enable_execution_quality:
            scalers.append(
                ExecutionQualityScaler(
                    cfg=cfg.execution_quality,
                    tracker=execution_quality_tracker,
                )
            )
        if cfg.enable_profit_give_back_sizer:
            scalers.append(
                ProfitGiveBackSizer(
                    cfg=cfg.profit_give_back,
                    tracker=intraday_profit_tracker,
                )
            )
        if extra_position_scalers:
            scalers.extend(extra_position_scalers)

        return cls(
            cfg=cfg,
            threshold_adjusters=adjusters,
            position_scalers=scalers,
            base_threshold_provider=base_threshold_provider,
        )

    # ── core API ────────────────────────────────────────────────────

    def evaluate(
        self,
        ctx: SignalContext,
        *,
        original_lots: int,
        base_threshold: float | None = None,
    ) -> AdjustedDecision:
        debug: dict = {"original_lots": int(original_lots)}
        # ── ① threshold layer
        if base_threshold is None:
            base_threshold = self._resolve_base_threshold(ctx)
        eff_threshold = float(base_threshold)
        debug["base_threshold"] = eff_threshold
        for adj in self._adjusters:
            name = adj.__class__.__name__
            try:
                eff_threshold = float(adj.resolve(ctx, eff_threshold))
            except Exception as exc:  # noqa: BLE001
                logger.warning("threshold adjuster %s failed: %s; keep previous", name, exc)
                debug[f"err_{name}"] = str(exc)
                continue
            debug[f"after_{name}"] = eff_threshold

        # 阈值层判断
        score = extract_score(ctx, prefer_pctl=True)
        debug["score_pctl"] = score
        if score is None:
            if not self.cfg.pass_through_when_score_missing:
                return AdjustedDecision(
                    passed=False, block_reason="score_missing",
                    block_stage="threshold", adjusted_lots=0,
                    effective_threshold=eff_threshold, debug=debug,
                )
            # 缺分数则跳过阈值比较，直接进 sizing
        else:
            if score < eff_threshold:
                return AdjustedDecision(
                    passed=False, block_reason="below_threshold",
                    block_stage="threshold", adjusted_lots=0,
                    effective_threshold=eff_threshold, debug=debug,
                )

        # ── ② sizing layer
        lots = int(original_lots)
        for sc in self._scalers:
            name = sc.__class__.__name__
            try:
                new_lots, reason = sc.scale(ctx, lots)
            except Exception as exc:  # noqa: BLE001
                logger.warning("position scaler %s failed: %s; keep previous", name, exc)
                debug[f"err_{name}"] = str(exc)
                continue
            debug[f"after_{name}"] = {"lots": int(new_lots), "reason": reason}
            lots = int(new_lots)
            if lots <= 0:
                return AdjustedDecision(
                    passed=False, block_reason=f"lots_zero:{reason}",
                    block_stage="sizing", adjusted_lots=0,
                    effective_threshold=eff_threshold, debug=debug,
                )

        return AdjustedDecision(
            passed=True, block_reason="", block_stage="",
            adjusted_lots=lots, effective_threshold=eff_threshold, debug=debug,
        )

    # ── helpers ─────────────────────────────────────────────────────

    def _resolve_base_threshold(self, ctx: SignalContext) -> float:
        if self._base_threshold_provider is not None:
            try:
                return float(self._base_threshold_provider(ctx))
            except Exception as exc:  # noqa: BLE001
                logger.debug("base_threshold_provider failed: %s", exc)
        # fallback: 70pp（与 OotEvaluationConfig.trade_filter_percentile_threshold 一致）
        return 70.0


__all__ = ["RiskOrchestrator"]
