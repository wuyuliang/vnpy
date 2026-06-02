"""Threshold adjusters: 模型分阈值的可插拔调整器。

每个 adjuster 实现 ``cta.risk.base.ThresholdAdjuster.resolve(ctx, base_threshold) -> float``。
RiskOrchestrator 按顺序串联 adjuster，前一个的输出作为后一个的输入。

默认链路（cfg 全开时）：
    base = cfg.get_base_threshold(ctx)
    base → QuantileThresholdAdjuster → DynamicBumpAdjuster → final
"""
from cta.risk.threshold.dynamic_bump import DynamicBumpAdjuster
from cta.risk.threshold.quantile_threshold import QuantileThresholdAdjuster
from cta.risk.threshold.static_threshold import StaticThresholdAdjuster

__all__ = [
    "DynamicBumpAdjuster",
    "QuantileThresholdAdjuster",
    "StaticThresholdAdjuster",
]
