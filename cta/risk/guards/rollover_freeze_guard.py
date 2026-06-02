"""§16.3 RolloverFreezeGuard：换月禁交。

依赖：``cta.risk.state.rollover_calendar.RolloverCalendar``（CSV 加载 expiry_date /
main_switch_date）。

判定（按 cfg）：
    days_to_expiry = calendar.days_to_expiry(symbol, now)
    days_since_main_switch = calendar.days_since_main_switch(symbol, now)

    if days_to_expiry is not None and days_to_expiry <= 0:
        → "expired"（已过期，拒所有）
    if 0 < days_to_expiry <= force_close_days and offset == "open":
        → "force_close_window"（仅平仓允许）
    if 0 < days_to_expiry < freeze_window_days and offset == "open":
        → "expiry_window"
    if days_since_main_switch is not None and 0 <= days_since_main_switch < post_switch_freeze_days
        and offset == "open":
        → "post_switch_freeze"

calendar lookup 返回 None（未知 symbol / 无 main_switch_date）→ 跳过对应判定（fail-open）。
平仓默认放行（allow_close_during_freeze=True）。
"""
from __future__ import annotations

import logging

import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.live.risk import RiskContext, RiskDecision, _BaseRule
from cta.risk.base import normalize_cluster, normalize_symbol
from cta.risk.guards.config import RolloverFreezeGuardConfig
from cta.risk.state.rollover_calendar import RolloverCalendar

logger = logging.getLogger(__name__)


class RolloverFreezeGuard(_BaseRule):
    """换月禁交。"""

    name: str = "rollover_freeze"

    def __init__(
        self,
        cfg: RolloverFreezeGuardConfig | None = None,
        *,
        calendar: RolloverCalendar | None = None,
    ) -> None:
        self.cfg = cfg or RolloverFreezeGuardConfig()
        self.calendar = calendar or RolloverCalendar()

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        symbol = self._symbol(order)
        if not symbol:
            return RiskDecision(True, "")
        offset = str(order.get("offset", "")).strip().lower()
        now = pd.Timestamp(getattr(ctx, "now", pd.Timestamp.now()))
        d2e = self.calendar.days_to_expiry(symbol, now)
        d_main = self.calendar.days_since_main_switch(symbol, now)
        # 1. 过期合约：任何方向都拒（极少触发，但兜底）
        if d2e is not None and d2e < 0:
            return RiskDecision(False, f"{self.name}:expired:{symbol}:d2e={d2e}")
        # 2. 平仓策略
        if offset == "close" and self.cfg.allow_close_during_freeze:
            return RiskDecision(True, "")
        if offset != "open":
            return RiskDecision(True, "")
        # 3. 老合约 expiry window
        if d2e is not None:
            cluster = normalize_cluster(infer_symbol_cluster(symbol))
            window = int(
                self.cfg.days_to_expiry_override_by_cluster.get(
                    cluster, self.cfg.freeze_window_days,
                )
            )
            if 0 <= d2e <= self.cfg.force_close_days:
                return RiskDecision(
                    False,
                    f"{self.name}:force_close_window:{symbol}:d2e={d2e}",
                )
            if 0 <= d2e < window:
                return RiskDecision(
                    False,
                    f"{self.name}:expiry_window:{symbol}:d2e={d2e}<{window}",
                )
        # 4. 新主力 post_switch freeze
        if d_main is not None and 0 <= d_main < self.cfg.post_switch_freeze_days:
            return RiskDecision(
                False,
                f"{self.name}:post_switch_freeze:{symbol}:d_main={d_main}<{self.cfg.post_switch_freeze_days}",
            )
        return RiskDecision(True, "")

    @staticmethod
    def _symbol(order: dict) -> str:
        sym = order.get("symbol")
        if sym:
            return normalize_symbol(sym)
        vt = order.get("vt_symbol", "")
        if vt:
            return normalize_symbol(str(vt).split(".", 1)[0])
        return ""


__all__ = ["RolloverFreezeGuard"]
