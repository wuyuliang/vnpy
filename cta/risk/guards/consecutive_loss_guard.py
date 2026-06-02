"""§17.2 ConsecutiveLossGuard：连续亏暂停。

依赖 ``cta.risk.state.consecutive_loss_tracker.ConsecutiveLossTracker``。

判定（仅 open；close 默认放行）：
    key = make_key(cluster, symbol, signal_type)
    if tracker.is_in_cooldown(key, now=ctx.now):
        → 拒（reason 含 until 时间戳）

caller 需在策略 on_trade 回调里调 tracker.on_trade，让 cooldown 状态实时更新。
"""
from __future__ import annotations

import logging

import pandas as pd

from cta.live.risk import RiskContext, RiskDecision, _BaseRule
from cta.risk.guards.config import ConsecutiveLossGuardConfig
from cta.risk.state.consecutive_loss_tracker import ConsecutiveLossTracker

logger = logging.getLogger(__name__)


class ConsecutiveLossGuard(_BaseRule):
    """连续亏损暂停。"""

    name: str = "consecutive_loss"

    def __init__(
        self,
        cfg: ConsecutiveLossGuardConfig | None = None,
        *,
        tracker: ConsecutiveLossTracker | None = None,
    ) -> None:
        self.cfg = cfg or ConsecutiveLossGuardConfig()
        self.tracker = tracker or ConsecutiveLossTracker(
            n_consecutive_losses=self.cfg.n_consecutive_losses,
            cooldown_hours=self.cfg.cooldown_hours,
            lookback_days=self.cfg.lookback_days,
            apply_to_groups=self.cfg.apply_to_groups,
            state_path=self.cfg.state_path or None,
        )

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        offset = str(order.get("offset", "")).strip().lower()
        if offset == "close" and not self.cfg.block_close_orders:
            return RiskDecision(True, "")
        if offset != "close" and offset != "open":
            return RiskDecision(True, "")
        key = self.tracker.make_key(
            cluster=str(order.get("cluster", "")),
            symbol=str(order.get("symbol", "")),
            signal_type=str(order.get("signal_type", "")),
            interval=str(order.get("interval", "")),
        )
        now = pd.Timestamp(getattr(ctx, "now", pd.Timestamp.now()))
        in_cooldown, until = self.tracker.is_in_cooldown(key, now=now)
        if in_cooldown:
            return RiskDecision(
                False, f"{self.name}:cooldown:{key.to_str()}:until={until}",
            )
        return RiskDecision(True, "")


__all__ = ["ConsecutiveLossGuard"]
