"""§16.2 LiquidityFloorGuard：流动性下限。

输入 order 字典：
    symbol, direction, offset
若 order 内已附带流动性指标（caller 已算好），优先用 order 内字段：
    volume_ratio, bid_ask_spread_ticks, turnover_ratio
否则从注入的 ``LiquiditySnapshotTracker`` 查 (symbol) 实时 snapshot。

判定规则（任一触发即拒；require_all_indicators=True 时三项都触发才拒）：
    volume_ratio < cfg.volume_floor_ratio
    spread_ticks > cfg.max_spread_ticks
    turnover_ratio < cfg.min_turnover_ratio

任意指标 None（数据不足）→ 该指标视为通过；所有指标 None → 整体 fail-open。
"""
from __future__ import annotations

import logging

from cta.live.risk import RiskContext, RiskDecision, _BaseRule
from cta.risk.base import normalize_symbol
from cta.risk.guards.config import LiquidityFloorGuardConfig
from cta.risk.state.liquidity_snapshot_tracker import (
    LiquiditySnapshot,
    LiquiditySnapshotTracker,
)

logger = logging.getLogger(__name__)


class LiquidityFloorGuard(_BaseRule):
    """流动性下限：volume / spread / turnover 三指标硬约束。"""

    name: str = "liquidity_floor"

    def __init__(
        self,
        cfg: LiquidityFloorGuardConfig | None = None,
        *,
        tracker: LiquiditySnapshotTracker | None = None,
    ) -> None:
        self.cfg = cfg or LiquidityFloorGuardConfig()
        self.tracker = tracker

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        offset = str(order.get("offset", "")).strip().lower()
        if offset == "close" and self.cfg.allow_close_orders:
            return RiskDecision(True, "")
        symbol = self._symbol(order)
        if not symbol:
            return RiskDecision(True, "")
        if symbol in {str(s).strip().upper() for s in self.cfg.bypass_for_symbols}:
            return RiskDecision(True, "")
        # 三指标：先看 order 自带（caller 已算），再 fallback tracker
        snap = self._snapshot_from_order_or_tracker(order, symbol)
        triggers = self._evaluate_triggers(snap)
        if not triggers:
            return RiskDecision(True, "")
        if self.cfg.require_all_indicators and len(triggers) < self._n_indicators_with_data(snap):
            # 要求全部都触发但有未触发的指标 → 放行
            return RiskDecision(True, "")
        reason = ",".join(triggers)
        return RiskDecision(False, f"{self.name}:{symbol}:{reason}")

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _symbol(order: dict) -> str:
        sym = order.get("symbol")
        if sym:
            return normalize_symbol(sym)
        vt = order.get("vt_symbol", "")
        if vt:
            return normalize_symbol(str(vt).split(".", 1)[0])
        return ""

    def _snapshot_from_order_or_tracker(
        self, order: dict, symbol: str,
    ) -> LiquiditySnapshot:
        vol_ratio = self._get_float(order, "volume_ratio")
        spread = self._get_float(order, "bid_ask_spread_ticks")
        turnover = self._get_float(order, "turnover_ratio")
        if vol_ratio is not None or spread is not None or turnover is not None:
            return LiquiditySnapshot(
                symbol=symbol,
                volume_ratio=vol_ratio,
                spread_ticks=spread,
                turnover_ratio=turnover,
                sample_count=int(order.get("liquidity_sample_count", 0) or 0),
            )
        if self.tracker is not None:
            return self.tracker.snapshot(symbol)
        return LiquiditySnapshot(
            symbol=symbol, volume_ratio=None, spread_ticks=None,
            turnover_ratio=None, sample_count=0,
        )

    @staticmethod
    def _get_float(order: dict, key: str) -> float | None:
        if key not in order:
            return None
        try:
            v = float(order[key])
        except (TypeError, ValueError):
            return None
        return v if v == v else None  # NaN check

    def _evaluate_triggers(self, snap: LiquiditySnapshot) -> list[str]:
        triggers: list[str] = []
        if snap.volume_ratio is not None and snap.volume_ratio < self.cfg.volume_floor_ratio:
            triggers.append(f"vol_ratio={snap.volume_ratio:.3f}<{self.cfg.volume_floor_ratio}")
        if snap.spread_ticks is not None and snap.spread_ticks > self.cfg.max_spread_ticks:
            triggers.append(f"spread={snap.spread_ticks:.1f}>{self.cfg.max_spread_ticks}")
        if snap.turnover_ratio is not None and snap.turnover_ratio < self.cfg.min_turnover_ratio:
            triggers.append(f"turnover={snap.turnover_ratio:.4f}<{self.cfg.min_turnover_ratio}")
        return triggers

    @staticmethod
    def _n_indicators_with_data(snap: LiquiditySnapshot) -> int:
        return sum(
            1 for v in (snap.volume_ratio, snap.spread_ticks, snap.turnover_ratio) if v is not None
        )


__all__ = ["LiquidityFloorGuard"]
