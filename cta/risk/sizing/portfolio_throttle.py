"""Thin adapter：把 portfolio_logic.RiskThrottle 包装成 PositionScaler 接口。

设计：现有 ``cta/portfolio_logic/risk_throttle.py:RiskThrottle.apply_to_caps`` 是从
``ThrottleLevel`` 映射到 ``CapsConfig`` 整体缩放（管 max_total_positions / max_per_cluster
/ notional_pct）—— 它的输出维度是"组合层 cap"，不是"单笔 lots"。

本 adapter 把"组合层 cap 缩比"用作"单笔 lots 的额外缩放因子"，让 RiskOrchestrator 链路里
能直接连接现有 RiskThrottle 4 档。

用法：
    base_caps = CapsConfig(max_total_positions=10, ...)
    throttle = RiskThrottle(RiskThrottleConfig())
    adapter = PortfolioThrottleSizer(throttle, base_caps, level_provider=lambda ctx: ...)
"""
from __future__ import annotations

import logging
from typing import Callable

from cta.risk.base import PositionScaler, SignalContext

logger = logging.getLogger(__name__)


class PortfolioThrottleSizer(PositionScaler):
    """按当前 ThrottleLevel.max_total_positions_mult 缩 lots。"""

    def __init__(
        self,
        throttle,
        base_caps,
        *,
        level_provider: Callable[[SignalContext], object] | None = None,
        default_level_name: str = "normal",
    ) -> None:
        """Parameters
        ----------
        throttle
            ``cta.portfolio_logic.risk_throttle.RiskThrottle`` 实例。
        base_caps
            ``cta.portfolio_logic.config.CapsConfig`` 实例（仅用于读 base cap 字段）。
        level_provider
            从 ctx 解出当前 ThrottleLevel；常见做法是 caller 把 level 存到 ctx.portfolio。
            None 时 fallback 到 default_level_name。
        """
        self.throttle = throttle
        self.base_caps = base_caps
        self.level_provider = level_provider
        self.default_level_name = str(default_level_name)

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]:
        lots = int(lots_so_far)
        if lots <= 0:
            return 0, "portfolio_throttle:lots_already_zero"
        level = self._resolve_level(ctx)
        if level is None:
            return lots, "portfolio_throttle:no_level"
        try:
            mult = float(level.max_total_positions_mult)
        except AttributeError:
            return lots, "portfolio_throttle:level_no_mult"
        if mult >= 1.0:
            return lots, f"portfolio_throttle:level={getattr(level, 'name', '?')}:mult=1.00"
        # 与 LinearDdScaler 同款 floor 兜底
        from math import floor as _floor
        new_lots = max(0, _floor(lots * mult))
        if new_lots == 0 and lots >= 1 and mult > 0:
            new_lots = 1
        return new_lots, f"portfolio_throttle:level={getattr(level, 'name', '?')}:mult={mult:.2f}"

    def _resolve_level(self, ctx: SignalContext):
        if self.level_provider is not None:
            try:
                return self.level_provider(ctx)
            except Exception as exc:  # noqa: BLE001
                logger.debug("level_provider failed: %s", exc)
        # fallback: 从 ctx.portfolio 读 level_name
        name = (ctx.portfolio or {}).get("throttle_level_name", self.default_level_name)
        try:
            return self.throttle.level_by_name(str(name))
        except (KeyError, AttributeError):
            return None


__all__ = ["PortfolioThrottleSizer"]
