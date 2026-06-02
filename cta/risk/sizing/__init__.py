"""Position scalers: 仓位缩放可插拔器。

每个 scaler 实现 ``cta.risk.base.PositionScaler.scale(ctx, lots_so_far) -> (new_lots, reason)``。
RiskOrchestrator 按顺序串联，前一个的输出作为后一个的输入。

默认链路（cfg 全开时）：
    original_lots → BucketScalingSizer → LinearDdScaler → PortfolioThrottleSizer → final
"""
from cta.risk.sizing.bucket_scaling import BucketScalingSizer
from cta.risk.sizing.config import (
    DailyVaRBudgetConfig,
    ExecutionQualityConfig,
    HolidayPositionReducerConfig,
    NightSessionCarryConfig,
    ProfitGiveBackConfig,
    SignalTypeSizeScalerConfig,
    VolatilityRegimeScalerConfig,
)
from cta.risk.sizing.daily_var_budget import DailyVaRBudgetSizer
from cta.risk.sizing.execution_quality_scaler import ExecutionQualityScaler
from cta.risk.sizing.holiday_position_reducer import HolidayPositionReducer
from cta.risk.sizing.linear_dd_scaler import LinearDdScaler
from cta.risk.sizing.night_session_carry import NightSessionCarryRule
from cta.risk.sizing.portfolio_throttle import PortfolioThrottleSizer
from cta.risk.sizing.profit_give_back import ProfitGiveBackSizer
from cta.risk.sizing.signal_type_size_scaler import SignalTypePositionScaler
from cta.risk.sizing.volatility_regime_scaler import VolatilityRegimeScaler

__all__ = [
    "BucketScalingSizer",
    "DailyVaRBudgetConfig",
    "DailyVaRBudgetSizer",
    "ExecutionQualityConfig",
    "ExecutionQualityScaler",
    "HolidayPositionReducer",
    "HolidayPositionReducerConfig",
    "LinearDdScaler",
    "NightSessionCarryConfig",
    "NightSessionCarryRule",
    "PortfolioThrottleSizer",
    "ProfitGiveBackConfig",
    "ProfitGiveBackSizer",
    "SignalTypePositionScaler",
    "SignalTypeSizeScalerConfig",
    "VolatilityRegimeScaler",
    "VolatilityRegimeScalerConfig",
]
