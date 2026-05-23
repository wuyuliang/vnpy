"""CTP order lifecycle state machine (P2-17).

CTP 单据 6 种 OrderStatus（vnpy.trader.constant.Status）：
- SUBMITTING  : 提交中
- NOTTRADED   : 未成交（已到交易所，挂在订单簿）
- PARTTRADED  : 部分成交
- ALLTRADED   : 全部成交
- CANCELLED   : 已撤销
- REJECTED    : 拒绝（资金不足 / 涨跌停 / 错单等）

合法状态转移图（DAG）：
    SUBMITTING ─┬─→ NOTTRADED ─┬─→ PARTTRADED ─→ ALLTRADED
                │                ├─→ ALLTRADED
                │                └─→ CANCELLED
                ├─→ REJECTED
                └─→ CANCELLED      (撤单在挂单前到达)

非法转移：例如 ALLTRADED → NOTTRADED、REJECTED → 任何状态、CANCELLED → ALLTRADED 等。
此模块提供 ``OrderLifecycle`` 状态机校验 + ``apply_event`` 接口。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field

logger = logging.getLogger(__name__)


# 不依赖 vnpy 真实 enum，便于无 vnpy 测试。
STATUSES: tuple[str, ...] = (
    "submitting",
    "nottraded",
    "parttraded",
    "alltraded",
    "cancelled",
    "rejected",
)

# 合法状态转移表（包含自环：相同状态 update 是合法的 idempotent）
_LEGAL_TRANSITIONS: dict[str, set[str]] = {
    "submitting": {"submitting", "nottraded", "rejected", "cancelled"},
    "nottraded": {"nottraded", "parttraded", "alltraded", "cancelled"},
    "parttraded": {"parttraded", "alltraded", "cancelled"},
    "alltraded": {"alltraded"},
    "cancelled": {"cancelled"},
    "rejected": {"rejected"},
}

# 终态：不再变化
TERMINAL: frozenset[str] = frozenset({"alltraded", "cancelled", "rejected"})


@dataclass(frozen=True)
class OrderLifecycleEvent:
    order_id: str
    new_status: str
    traded_volume: float = 0.0
    rejection_reason: str = ""


@dataclass
class OrderLifecycle:
    """单笔订单的状态机；apply_event 更新或 raise on illegal transition。"""

    order_id: str
    total_volume: float
    status: str = "submitting"
    traded_volume: float = 0.0
    rejection_reason: str = ""
    history: list[tuple[str, float]] = field(default_factory=list)

    def __post_init__(self) -> None:
        st = str(self.status).strip().lower()
        if st not in STATUSES:
            raise ValueError(f"invalid initial status {st!r}")
        if self.total_volume <= 0:
            raise ValueError("total_volume must be > 0")
        self.status = st
        if not self.history:
            self.history.append((self.status, float(self.traded_volume)))

    def is_terminal(self) -> bool:
        return self.status in TERMINAL

    def apply_event(self, event: OrderLifecycleEvent) -> None:
        """状态机推进。非法转移 raise；超量成交 raise。"""
        if str(event.order_id) != self.order_id:
            raise ValueError(f"order_id mismatch: {event.order_id} vs {self.order_id}")
        new_st = str(event.new_status).strip().lower()
        if new_st not in STATUSES:
            raise ValueError(f"invalid new status {new_st!r}")
        if new_st not in _LEGAL_TRANSITIONS[self.status]:
            raise RuntimeError(
                f"illegal transition: {self.status} → {new_st} (order={self.order_id})"
            )
        # 成交量校验
        new_traded = float(event.traded_volume)
        if new_traded < self.traded_volume - 1e-9:
            raise ValueError(
                f"traded_volume decreased: {self.traded_volume} → {new_traded} (order={self.order_id})"
            )
        if new_traded > self.total_volume + 1e-9:
            raise ValueError(
                f"traded_volume exceeds total: {new_traded} > {self.total_volume}"
            )
        # alltraded 时 traded_volume 应等于 total_volume
        if new_st == "alltraded" and abs(new_traded - self.total_volume) > 1e-6:
            # 自动补齐
            new_traded = self.total_volume
        self.status = new_st
        self.traded_volume = new_traded
        if new_st == "rejected" and event.rejection_reason:
            self.rejection_reason = str(event.rejection_reason)
        self.history.append((self.status, self.traded_volume))


__all__ = [
    "OrderLifecycle",
    "OrderLifecycleEvent",
    "STATUSES",
    "TERMINAL",
]
