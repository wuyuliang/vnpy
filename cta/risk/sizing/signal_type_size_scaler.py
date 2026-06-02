"""按 signal_type 缩放 lots 的 PositionScaler（sim/live 三层一致组件）。

目标：让 sim/live 的下单 sizing 与 OOT 的 ``signal_type_size_multiplier`` 对齐——
pullback 类信号放大、atr_breakout 缩小。OOT 把系数作用在单笔名义上限上，这里直接乘 lots，
两者意图一致。

fail-open：候选缺 ``signal_type`` 或该类型未配置 → mult=1.0，不动 lots。
默认在 orchestrator 关闭（``enable_signal_type_size_scaler=False``），需显式开启。
"""
from __future__ import annotations

import logging
from math import floor

from cta.risk.base import PositionScaler, SignalContext
from cta.risk.sizing.config import SignalTypeSizeScalerConfig

logger = logging.getLogger(__name__)


class SignalTypePositionScaler(PositionScaler):
    """按 candidate.signal_type 乘 lots。"""

    def __init__(self, cfg: SignalTypeSizeScalerConfig | None = None) -> None:
        self.cfg = cfg or SignalTypeSizeScalerConfig()

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]:
        lots = int(lots_so_far)
        if lots <= 0:
            return 0, "signal_type_size:lots_already_zero"
        signal_type = str((ctx.candidate or {}).get("signal_type", "")).strip().lower()
        if not signal_type:
            return lots, "signal_type_size:no_signal_type"
        mult = self.compute_mult(signal_type)
        if mult == 1.0:
            return lots, f"signal_type_size:{signal_type}:mult=1.00"
        new_lots = max(0, floor(lots * mult))
        # 放大时向上取整保证至少不丢失整数倍；缩小到 0 但 mult>0 时保留 1 手
        if new_lots == 0 and lots >= 1 and mult > 0:
            new_lots = 1
        return new_lots, f"signal_type_size:{signal_type}:mult={mult:.2f}"

    def compute_mult(self, signal_type: str) -> float:
        mult = float(self.cfg.multiplier_by_signal_type.get(str(signal_type).strip().lower(), 1.0))
        return max(self.cfg.floor_mult, min(self.cfg.cap_mult, mult))


__all__ = ["SignalTypePositionScaler"]
