"""Position-side exit evaluator for sim/live (P1-13).

按 design doc §3.4 串联（trailing_take_profit / profit_aware_horizon 已于 2026-05-29
删除，仅保留 hard_stop）：
  1. hard_stop（防爆仓） / intrabar_stop_loss_pct_by_cluster_interval（P1-13）

trailing_stop / mfe_mae 留接口给后续 wire。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.sim.adapters.state_provider import StateProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionExitDecision:
    """exit 决策（含归因 reason）。"""
    should_exit: bool
    exit_reason: str
    exit_price: float
    extra: dict[str, Any]


def _resolve_intrabar_stop_pct(
    *,
    cluster: str,
    interval: str,
    cfg: OotEvaluationConfig | None,
) -> float:
    """P1-13：按 cluster|interval 取 intrabar_stop_loss_pct；缺则回退 global。"""
    if cfg is None:
        return 0.01
    overrides = dict(getattr(cfg, "intrabar_stop_loss_pct_by_cluster_interval", {}) or {})
    key = f"{str(cluster or '').strip().lower()}|{str(interval or '').strip().lower()}"
    if key in overrides:
        return float(overrides[key])
    return float(getattr(cfg, "intrabar_stop_loss_pct", 0.01))


class PositionEvaluator:
    """sim/live 持仓阶段评估器，每 bar 调一次（当前仅 hard_stop）。"""

    def __init__(
        self,
        *,
        oot_cfg: OotEvaluationConfig | None = None,
        state_provider: StateProvider | None = None,
    ) -> None:
        self.oot_cfg = oot_cfg
        self.state_provider = state_provider

    def evaluate(
        self,
        position: dict[str, Any],
        bar: dict[str, Any] | pd.Series,
        *,
        highwater_price: float = float("nan"),
        cluster: str = "",
        interval: str = "",
    ) -> PositionExitDecision:
        """每 bar 评估持仓是否应离场（hard_stop only）。"""
        row = dict(bar)
        side = str(position.get("side", "long")).strip().lower()
        entry_price = float(position.get("entry_price", float("nan")))
        current_price = float(row.get("close", row.get("price", float("nan"))))

        stop_pct = _resolve_intrabar_stop_pct(cluster=cluster, interval=interval, cfg=self.oot_cfg)
        valid = (
            np.isfinite(entry_price) and entry_price > 0
            and np.isfinite(current_price) and current_price > 0
        )
        if valid and stop_pct > 0:
            sign = -1.0 if side == "short" else 1.0
            stop_price = float(entry_price) * (1.0 - sign * stop_pct)
            if side == "long" and current_price <= stop_price:
                return PositionExitDecision(
                    should_exit=True, exit_reason="hard_stop",
                    exit_price=float(current_price),
                    extra={"stop_price": stop_price, "stop_pct": stop_pct},
                )
            if side == "short" and current_price >= stop_price:
                return PositionExitDecision(
                    should_exit=True, exit_reason="hard_stop",
                    exit_price=float(current_price),
                    extra={"stop_price": stop_price, "stop_pct": stop_pct},
                )

        return PositionExitDecision(
            should_exit=False, exit_reason="", exit_price=float("nan"), extra={},
        )


__all__ = ["PositionEvaluator", "PositionExitDecision"]
