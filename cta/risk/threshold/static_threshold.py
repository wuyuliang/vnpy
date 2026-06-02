"""Static threshold adjuster：把 OotEvaluationConfig 现有 (cluster, interval) 表当 base。

设计：本类实际上**不调整** base_threshold，只是把现有 cfg 字典查表"封装"为 ThresholdAdjuster
接口，便于在 orchestrator 里作为第一段：

    # 链路：
    # base = StaticThresholdAdjuster.resolve(ctx, default_global)
    #      → QuantileThresholdAdjuster.resolve(ctx, base) ← 替换为 manifest 内的 P70
    #      → DynamicBumpAdjuster.resolve(ctx, base)       ← dd → 抬高

复用 ``cta/model/oot/oot_trade_filter_gate.py`` 同样的 normalize key + override 查表
约定，避免双份实现漂移。
"""
from __future__ import annotations

import logging

from cta.risk.base import SignalContext, normalize_cluster, normalize_interval

logger = logging.getLogger(__name__)


class StaticThresholdAdjuster:
    """读 OotEvaluationConfig.trade_filter_*_threshold_by_cluster_interval 的薄包装。"""

    def __init__(
        self,
        *,
        overrides: dict[str, float] | None = None,
        global_default: float = 70.0,
    ) -> None:
        """Parameters
        ----------
        overrides
            来自 ``cfg.trade_filter_percentile_threshold_by_cluster_interval``
            或 raw 版本的 dict（key="cluster|interval", value=阈值）。
        global_default
            未命中 override 时的 fallback。
        """
        self._overrides: dict[str, float] = {}
        for k, v in (overrides or {}).items():
            try:
                self._overrides[self._normalize_key(k)] = float(v)
            except (TypeError, ValueError):
                logger.debug("StaticThresholdAdjuster skip bad override (%r, %r)", k, v)
        self.global_default = float(global_default)

    @staticmethod
    def _normalize_key(raw: object) -> str:
        parts = str(raw).strip().lower().split("|")
        if len(parts) < 2:
            return str(raw).strip().lower()
        return f"{normalize_cluster(parts[0])}|{normalize_interval(parts[1])}"

    def resolve(self, ctx: SignalContext, base_threshold: float) -> float:
        cluster = normalize_cluster(ctx.candidate.get("cluster"))
        interval = normalize_interval(ctx.candidate.get("interval"))
        if not cluster or not interval:
            return float(base_threshold)
        key = f"{cluster}|{interval}"
        if key in self._overrides:
            return float(self._overrides[key])
        # 未配 → 返回入参 base_threshold（一般等于 global_default）
        return float(base_threshold) if base_threshold is not None else float(self.global_default)


__all__ = ["StaticThresholdAdjuster"]
