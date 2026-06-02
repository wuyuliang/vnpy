"""§16.5 NightSessionCarryRule：夜盘/周末隔仓。

判定（基于 ctx.bar_dt 时间）：
- 周五 + 时间 >= friday_taper_after_hhmm → 按 cluster mult 缩仓
- 其他情况 → mult=1.0（默认不动）

cfg.enable_friday_taper=False 时整体 no-op。

fail-open：bar_dt 缺失 / 解析失败 → mult=1.0。
"""
from __future__ import annotations

import logging
from datetime import time
from math import floor

import pandas as pd

from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.risk.base import PositionScaler, SignalContext, normalize_cluster
from cta.risk.sizing.config import NightSessionCarryConfig

logger = logging.getLogger(__name__)


class NightSessionCarryRule(PositionScaler):
    """周末隔仓缩仓器。"""

    def __init__(self, cfg: NightSessionCarryConfig | None = None) -> None:
        self.cfg = cfg or NightSessionCarryConfig()
        h, m = self.cfg.friday_taper_after_hhmm.split(":")
        self._friday_taper_after = time(int(h), int(m))

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]:
        lots = int(lots_so_far)
        if lots <= 0:
            return 0, "night_carry:lots_already_zero"
        if not self.cfg.enable_friday_taper:
            return lots, "night_carry:disabled"
        bar_dt = self._bar_dt(ctx)
        if bar_dt is None:
            return lots, "night_carry:no_bar_dt"
        # 周五 = weekday 4
        if bar_dt.weekday() != 4:
            return lots, "night_carry:not_friday"
        if bar_dt.time() < self._friday_taper_after:
            return lots, "night_carry:before_taper_time"
        cluster = self._cluster_of(ctx)
        mult = self._mult_for_cluster(cluster)
        if mult >= 1.0:
            return lots, f"night_carry:cluster={cluster}:mult=1.00"
        new_lots = max(0, floor(lots * mult))
        if new_lots == 0 and lots >= 1 and mult > 0:
            new_lots = 1
        return new_lots, f"night_carry:cluster={cluster}:mult={mult:.2f}"

    # ── helpers ─────────────────────────────────────────────────────

    @staticmethod
    def _bar_dt(ctx: SignalContext) -> pd.Timestamp | None:
        try:
            v = pd.Timestamp(ctx.bar_dt)
            if pd.isna(v):
                return None
            return v
        except Exception:  # noqa: BLE001
            return None

    @staticmethod
    def _cluster_of(ctx: SignalContext) -> str:
        cl = ctx.candidate.get("cluster") if isinstance(ctx.candidate, dict) else None
        if cl:
            return normalize_cluster(cl)
        sym = ctx.candidate.get("symbol", "") if isinstance(ctx.candidate, dict) else ""
        return normalize_cluster(infer_symbol_cluster(str(sym)))

    def _mult_for_cluster(self, cluster: str) -> float:
        m = self.cfg.weekend_carry_mult_by_cluster.get(cluster)
        if m is None:
            return float(self.cfg.default_mult)
        return float(m)


__all__ = ["NightSessionCarryRule"]
