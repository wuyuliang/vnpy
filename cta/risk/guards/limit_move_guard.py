"""§16.1 LimitMoveGuard：涨跌停硬约束。

继承 ``cta.live.risk._BaseRule``，挂入 ``RiskGuard.rules``（推荐放在链路最前，
见 cta/docs/risk.md §21 invariant 6）。

判定逻辑（按 prev_close + current_price + cluster limit_pct）：
    upper = prev_close × (1 + limit_pct)
    lower = prev_close × (1 - limit_pct)
    upper_near = upper × (1 - near_limit_tolerance_pct)
    lower_near = lower × (1 + near_limit_tolerance_pct)

    current >= upper            → "at_upper"（已触涨停）
    current >= upper_near        → "near_upper"（近涨停）
    current <= lower            → "at_lower"
    current <= lower_near        → "near_lower"

行为：
| offset | direction | 价格状态 | 行为 |
|--------|-----------|---------|------|
| open   | long      | at_upper / near_upper | 拒（成交概率 0 + 锁仓）|
| open   | short     | at_lower / near_lower | 拒 |
| open   | long      | at_lower / near_lower | 拒（追跌不智）|
| open   | short     | at_upper / near_upper | 拒（追涨不智）|
| close  | *         | *                     | 放行（默认；cfg.block_close_at_unfavorable_limit=True 才拦）|

输入 order 字典需包含：
    symbol, direction (long/short), offset (open/close), price, prev_close, [cluster]
缺 prev_close 或 prev_close <= 0 → fail-open 放行。
"""
from __future__ import annotations

import logging

from cta.config.symbol_cluster_config import infer_symbol_cluster, infer_symbol_limit_pct
from cta.live.risk import RiskContext, RiskDecision, _BaseRule
from cta.risk.base import normalize_cluster, normalize_symbol
from cta.risk.guards.config import LimitMoveGuardConfig

logger = logging.getLogger(__name__)


class LimitMoveGuard(_BaseRule):
    """涨跌停硬约束。"""

    name: str = "limit_move"

    def __init__(self, cfg: LimitMoveGuardConfig | None = None) -> None:
        self.cfg = cfg or LimitMoveGuardConfig()

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        # 解析订单基本字段
        offset = self._get_offset(order)
        direction = self._get_direction(order)
        symbol = self._get_symbol(order)
        if not symbol or not direction:
            return RiskDecision(True, "")   # 不可识别 → fail-open
        try:
            current = float(order.get("price", 0.0) or 0.0)
            prev_close = float(order.get("prev_close", 0.0) or 0.0)
        except (TypeError, ValueError):
            return RiskDecision(True, "")
        if current <= 0 or prev_close <= 0:
            return RiskDecision(True, "")   # 缺数据 → fail-open
        cluster = self._get_cluster(order, symbol)
        limit_pct = self._resolve_limit_pct(symbol, cluster)
        if limit_pct <= 0:
            return RiskDecision(True, "")
        # 区间判定
        upper = prev_close * (1.0 + limit_pct)
        lower = prev_close * (1.0 - limit_pct)
        upper_near = upper * (1.0 - self.cfg.near_limit_tolerance_pct)
        lower_near = lower * (1.0 + self.cfg.near_limit_tolerance_pct)
        at_upper = current >= upper
        at_lower = current <= lower
        near_upper = current >= upper_near and not at_upper
        near_lower = current <= lower_near and not at_lower
        # 平仓默认放行（持仓有出口）
        if offset == "close":
            if not self.cfg.block_close_at_unfavorable_limit:
                return RiskDecision(True, "")
            # cfg 启用严格模式：触板时拦平仓（near 不拦）
            if at_upper or at_lower:
                state = "at_upper" if at_upper else "at_lower"
                return RiskDecision(
                    False, f"{self.name}:close_{state}:{symbol}={current:.4f}/{prev_close:.4f}",
                )
            return RiskDecision(True, "")
        # 开仓判定
        if offset == "open":
            if at_upper or at_lower:
                if self.cfg.block_open_when_at:
                    state = "at_upper" if at_upper else "at_lower"
                    return RiskDecision(
                        False, f"{self.name}:{state}:{symbol}={current:.4f}/{prev_close:.4f}",
                    )
            if near_upper or near_lower:
                if self.cfg.block_open_when_near:
                    state = "near_upper" if near_upper else "near_lower"
                    return RiskDecision(
                        False, f"{self.name}:{state}:{symbol}={current:.4f}/{prev_close:.4f}",
                    )
        # 兜底放行
        return RiskDecision(True, "")

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _get_offset(order: dict) -> str:
        return str(order.get("offset", "")).strip().lower()

    @staticmethod
    def _get_direction(order: dict) -> str:
        d = order.get("direction") or order.get("side")
        return str(d or "").strip().lower()

    @staticmethod
    def _get_symbol(order: dict) -> str:
        sym = order.get("symbol")
        if sym:
            return normalize_symbol(sym)
        vt = order.get("vt_symbol", "")
        if vt:
            return normalize_symbol(str(vt).split(".", 1)[0])
        return ""

    @staticmethod
    def _get_cluster(order: dict, symbol: str) -> str:
        cl = order.get("cluster")
        if cl:
            return normalize_cluster(cl)
        return normalize_cluster(infer_symbol_cluster(symbol))

    def _resolve_limit_pct(self, symbol: str, cluster: str) -> float:
        override = self.cfg.limit_pct_by_cluster.get(cluster)
        if override is not None:
            return float(override)
        return float(infer_symbol_limit_pct(symbol))


__all__ = ["LimitMoveGuard"]
