"""ATR + 风险百分比 动态仓位计算。

公式:
    qty = floor( equity * risk_pct * leverage_mult / (stop_distance * size) )
    risk_pct * equity = 单笔愿意承担的账户风险(元)
    stop_distance * size = 单手亏损(元)
    leverage_mult = 组合回撤分级系数(1.0 / 0.5 / 0.25)

返回 0 时记 warning,上层应跳过该笔。
"""
from __future__ import annotations

import logging
import math

logger = logging.getLogger(__name__)


def calc_position_size(
    equity: float,
    stop_distance: float,
    contract_size: float,
    risk_pct: float,
    leverage_mult: float = 1.0,
    min_qty: int = 0,
) -> int:
    """返回整数手数;输入无效或计算结果 <= 0 返回 min_qty。"""
    if equity <= 0 or stop_distance <= 0 or contract_size <= 0 or risk_pct <= 0:
        logger.warning(
            "calc_position_size: 无效输入 equity=%.2f stop=%.4f size=%s risk=%s",
            equity, stop_distance, contract_size, risk_pct,
        )
        return min_qty

    risk_cash = equity * risk_pct * leverage_mult
    per_lot_loss = stop_distance * contract_size
    raw = risk_cash / per_lot_loss

    if raw < 1:
        logger.info(
            "calc_position_size: risk/stop 组合仅能开 %.3f 手 → 返回 %d",
            raw, min_qty,
        )
        return min_qty

    qty = int(math.floor(raw))
    return max(qty, min_qty)


__all__ = ["calc_position_size"]
