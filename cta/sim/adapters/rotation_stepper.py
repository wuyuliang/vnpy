"""Rotation stepper for sim/live (P1-7).

包装 [CrossSectionalRotationExecutor](../../portfolio_logic/cross_sectional_rotation_executor.py)，
让 sim_runner 主循环可以按 daily / weekly tick 调用：

    stepper = RotationStepper(cfg, universe_provider=...)
    for t in trading_days:
        intents = stepper.step(t, portfolio_state)
        for intent in intents:
            sim_runner.send_order(intent)

设计原则
--------
- **不重复 rotation 逻辑**：直接复用 ``CrossSectionalRotationExecutor``
- **缓存 stepper 状态**：``last_rebalance_dt`` 自动维护，无需外部传
- **可注入 disabled_symbols / rollover_check**：让 contract_resolver / symbol_disable 自动接入
"""
from __future__ import annotations

import logging
import math
from typing import Any, Callable, Mapping

import pandas as pd

from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig
from cta.portfolio_logic.cross_sectional_rotation_executor import (
    CrossSectionalRotationExecutor,
    RotationOrderIntent,
)
from cta.portfolio_logic.portfolio_state import PortfolioState

logger = logging.getLogger(__name__)


UniverseProvider = Callable[[pd.Timestamp], Mapping[str, pd.DataFrame]]
RolloverCheck = Callable[[str, pd.Timestamp, int], bool]


class RotationStepper:
    """sim/live rotation 主循环入口。"""

    def __init__(
        self,
        *,
        cfg: CrossSectionalRotationConfig,
        universe_provider: UniverseProvider,
        interval: str = "day",
        exchange_by_symbol: Mapping[str, str] | None = None,
        disabled_symbols: set[str] | None = None,
        rollover_check: RolloverCheck | None = None,
        drawdown_provider: Callable[[PortfolioState], float] | None = None,
    ) -> None:
        self.cfg = cfg
        self.interval = str(interval)
        self._executor = CrossSectionalRotationExecutor(
            cfg=cfg,
            universe_provider=universe_provider,
            interval=interval,
            exchange_by_symbol=exchange_by_symbol,
            disabled_symbols=disabled_symbols,
            rollover_check=rollover_check,
            drawdown_provider=drawdown_provider,
        )

    def step(
        self,
        t: pd.Timestamp,
        portfolio_state: PortfolioState,
    ) -> list[RotationOrderIntent]:
        """每个 sim 主循环 tick 调用一次；非 rebalance 日返回空。"""
        return self._executor.step(t, portfolio_state)

    @property
    def last_rebalance_dt(self) -> pd.Timestamp | None:
        """暴露内部状态用于复盘 / 日志。"""
        return self._executor.last_rebalance_dt


def intent_to_legacy_order(
    intent: RotationOrderIntent,
    *,
    price: float,
    contract_size: float,
    lot_size: int = 1,
    max_lots: int | None = None,
    order_type: str = "market",
) -> dict[str, Any]:
    """Convert rotation intent to LegacyCtaAdapter order dict.

    The returned payload matches ``LegacyCtaAdapter._dispatch_order`` contract:
    ``{"side", "lots", "price", "order_type"}``.
    """
    px = float(price)
    cs = max(float(contract_size), 1e-12)
    ls = max(int(lot_size), 1)
    notional_per_lot = abs(px * cs * ls)
    raw_lots = int(abs(float(intent.target_notional)) / notional_per_lot)
    if abs(float(intent.target_notional)) > 0.0 and raw_lots <= 0:
        raw_lots = 1
    if max_lots is not None:
        raw_lots = min(raw_lots, max(int(max_lots), 1))
    lots = max(raw_lots, 1)
    if not math.isfinite(px):
        raise ValueError(f"price must be finite, got {price!r}")
    return {
        "side": str(intent.side).strip().lower(),
        "lots": int(lots),
        "price": px,
        "order_type": str(order_type).strip().lower(),
        "signal_type": str(intent.signal_type),
        "signal_datetime": pd.Timestamp(intent.signal_datetime),
        "entry_datetime": pd.Timestamp(intent.entry_datetime),
        "planned_exit_datetime": pd.Timestamp(intent.planned_exit_datetime),
        "stop_price": float(intent.stop_price),
        "rotation_symbol": str(intent.symbol),
    }


__all__ = ["RotationStepper", "RotationOrderIntent", "intent_to_legacy_order"]
