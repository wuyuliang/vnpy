"""§16.6 VolatilityRegimeScaler：波动率自适应。

按 candidate ``realized_vol_pctl``（或同义列）所处档位映射 mult；低 vol 加仓 / 高 vol 减仓。

fail-open：候选没有 vol pctl 列 → mult=1.0，不动 lots。
"""
from __future__ import annotations

import logging
from math import floor

from cta.risk.base import PositionScaler, SignalContext
from cta.risk.sizing.config import VolatilityRegimeScalerConfig

logger = logging.getLogger(__name__)


class VolatilityRegimeScaler(PositionScaler):
    """波动率分段缩仓 / 加仓。"""

    def __init__(self, cfg: VolatilityRegimeScalerConfig | None = None) -> None:
        self.cfg = cfg or VolatilityRegimeScalerConfig()

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]:
        lots = int(lots_so_far)
        if lots <= 0:
            return 0, "vol_regime:lots_already_zero"
        vol_pctl = self._extract_vol_pctl(ctx)
        if vol_pctl is None:
            return lots, "vol_regime:no_vol_data"
        mult = self.compute_mult(vol_pctl)
        new_lots = max(0, floor(lots * mult))
        if new_lots == 0 and lots >= 1 and mult > 0:
            new_lots = 1
        return new_lots, f"vol_regime:pctl={vol_pctl:.1f}:mult={mult:.2f}"

    def compute_mult(self, vol_pctl: float) -> float:
        try:
            v = float(vol_pctl)
        except (TypeError, ValueError):
            return 1.0
        # 值域夹到 [0, 100]
        if v < 0.0:
            v = 0.0
        if v > 100.0:
            v = 100.0
        edges = self.cfg.vol_pctl_edges
        mults = self.cfg.mults
        if v < edges[0]:
            mult = mults[0]
        elif v >= edges[-1]:
            mult = mults[-1]
        else:
            mult = mults[-1]
            for i in range(len(edges) - 1):
                if edges[i] <= v < edges[i + 1]:
                    mult = mults[i + 1]
                    break
        # 双向 clamp
        return max(self.cfg.floor_mult, min(self.cfg.cap_mult, float(mult)))

    def _extract_vol_pctl(self, ctx: SignalContext) -> float | None:
        cand = ctx.candidate or {}
        for col in self.cfg.vol_pctl_columns:
            if col in cand:
                try:
                    v = float(cand[col])
                except (TypeError, ValueError):
                    continue
                if v != v:  # NaN
                    continue
                # 若 [0, 1] → ×100
                return v * 100.0 if 0 <= v <= 1.0 else v
        return None


__all__ = ["VolatilityRegimeScaler"]
