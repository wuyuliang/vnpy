"""Drawdown and profit-floor replay controls."""
from __future__ import annotations

from dataclasses import dataclass
import math
from typing import Any

import numpy as np
import pandas as pd

from cta.config.multi_timeframe_trend_config import MultiTimeframeTrendConfig

from .models import _Position

def _position_point_value(position: _Position) -> float:
    """按建仓初始手数计算每个价格点对应的现金金额。"""
    return float(position.initial_quantity) * float(
        position.current_metadata.contract_size
    )


def _price_for_unrealized_r(position: _Position, target_r: float) -> float:
    """把以 R 表示的浮盈换算成价格。"""
    risk = float(position.initial_risk_cash)
    value = _position_point_value(position)
    if not np.isfinite(risk) or risk <= 0 or value <= 0:
        return math.nan
    direction = int(position.pending.candidate["direction"])
    return float(position.entry_price) + direction * target_r * risk / value


def _peak_unrealized_r(position: _Position) -> float:
    """已确认的峰值浮盈（R）。极值由 ``_update_excursions`` 按 1 分钟 K 线的
    最高价（多头）/最低价（空头）维护。"""
    risk = float(position.initial_risk_cash)
    value = _position_point_value(position)
    if not np.isfinite(risk) or risk <= 0 or value <= 0:
        return math.nan
    direction = int(position.pending.candidate["direction"])
    move = direction * (
        float(position.maximum_favorable_price) - float(position.entry_price)
    )
    return move * value / risk


def _refresh_profit_floor(
    position: _Position,
    config: MultiTimeframeTrendConfig,
) -> None:
    """按当前峰值浮盈重算止盈地板，供**下一根** K 线使用。

    地板只上抬不下移：峰值本身单调不减，所以地板天然单调，但显式取 max 以防
    合约乘数或手数在减仓后变化时地板意外回落。
    """
    if not config.profit_floor_enabled:
        return
    peak_r = _peak_unrealized_r(position)
    if not np.isfinite(peak_r) or peak_r < float(config.profit_floor_arm_r):
        return
    giveback = max(
        float(config.profit_floor_giveback_r),
        float(config.profit_floor_giveback_pct) * peak_r,
    )
    remaining_fraction = float(position.quantity) / float(position.initial_quantity)
    giveback *= remaining_fraction
    floor_r = max(0.0, peak_r - giveback)
    price = _price_for_unrealized_r(position, floor_r)
    if not np.isfinite(price):
        return
    direction = int(position.pending.candidate["direction"])
    current = position.profit_floor_price
    if not np.isfinite(current):
        position.profit_floor_price = price
    elif direction > 0:
        position.profit_floor_price = max(current, price)
    else:
        position.profit_floor_price = min(current, price)

@dataclass
class _DrawdownScalingState:
    """Hysteresis band on realized-equity drawdown.

    The older portfolio scaler releases only once the whole loss has been earned
    back, which in practice never releases: on the 2026H1 run it triggered at
    1.23% drawdown on 01-26 and was still active on 06-03. This one enters above
    ``drawdown_scale_threshold`` and leaves below ``drawdown_scale_release``,
    so it recovers on its own and cannot latch.
    """

    high_water: float
    active: bool = False

    def drawdown(self, cash: float) -> float:
        if self.high_water <= 0:
            return 0.0
        return max(0.0, (self.high_water - cash) / self.high_water)

    def advance(self, cash: float, config: MultiTimeframeTrendConfig) -> bool:
        """Fold realized equity in; return True when the active flag changed."""
        self.high_water = max(self.high_water, float(cash))
        threshold = float(config.drawdown_scale_threshold)
        if threshold <= 0:
            changed = self.active
            self.active = False
            return changed
        level = self.drawdown(cash)
        was_active = self.active
        if not self.active and level > threshold:
            self.active = True
        elif self.active and level < float(config.drawdown_scale_release):
            self.active = False
        return self.active != was_active
def _record_drawdown_scaling(
    state: _DrawdownScalingState,
    *,
    cash: float,
    timestamp: pd.Timestamp,
    candidate_id: str,
    net_pnl: float,
    config: MultiTimeframeTrendConfig,
    event_rows: list[dict[str, Any]],
) -> None:
    """Advance the drawdown band and audit any state change."""
    if not state.advance(cash, config):
        return
    event_rows.append(
        {
            "event_time": timestamp,
            "sequence": len(event_rows) + 1,
            "scope": "DRAWDOWN",
            "symbol": "",
            "event_type": "TRIGGER" if state.active else "RECOVER",
            "candidate_id": candidate_id,
            "net_pnl": net_pnl,
            "realized_cash": cash,
            "high_water": state.high_water,
            "drawdown_fraction": state.drawdown(cash),
            "position_scale": (
                config.drawdown_scale_factor if state.active else 1.0
            ),
            "recovery_deficit": max(0.0, state.high_water - cash),
        }
    )
