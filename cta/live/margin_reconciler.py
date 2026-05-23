"""Margin / cash reconciler (P2-19).

CTP ``query_account`` 返回真实账户资金（balance / available / frozen / margin），
本模块把它与本地 ``DailyPnlTracker`` + 仓位估算对账，差异超 ``tolerance_pct`` 即告警。

接口契约（roadmap §2.3）：
    reconciler = MarginReconciler(tolerance_pct=0.01)
    snapshot = AccountSnapshot.from_vnpy_query(account_data)
    local_estimate = MarginEstimate(capital=..., realized_pnl=..., margin=...)
    diff = reconciler.compare(snapshot, local_estimate)
    if diff.is_critical():
        kill_switch.activate(reason=f"margin_drift_{diff.balance_diff_pct:.2%}")
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class AccountSnapshot:
    """CTP query_account 返回（vnpy AccountData 简化版）。"""
    balance: float        # 账户权益
    available: float      # 可用资金
    margin: float         # 占用保证金
    frozen: float = 0.0   # 冻结资金（委托中）

    @classmethod
    def from_vnpy_query(cls, account_data: object) -> "AccountSnapshot":
        """从 vnpy ``AccountData`` 构造（兼容 dict / 对象）。"""
        if isinstance(account_data, dict):
            return cls(
                balance=float(account_data.get("balance", 0.0)),
                available=float(account_data.get("available", 0.0)),
                margin=float(account_data.get("margin", 0.0)),
                frozen=float(account_data.get("frozen", 0.0)),
            )
        return cls(
            balance=float(getattr(account_data, "balance", 0.0)),
            available=float(getattr(account_data, "available", 0.0)),
            margin=float(getattr(account_data, "margin", 0.0)),
            frozen=float(getattr(account_data, "frozen", 0.0)),
        )


@dataclass(frozen=True)
class MarginEstimate:
    """本地估算的账户状态（基于 DailyPnlTracker + open positions）。"""
    initial_capital: float
    realized_pnl: float
    unrealized_pnl: float
    estimated_margin: float

    @property
    def balance(self) -> float:
        return self.initial_capital + self.realized_pnl + self.unrealized_pnl

    @property
    def available(self) -> float:
        return self.balance - self.estimated_margin


@dataclass(frozen=True)
class ReconcileDiff:
    """对账差异结果。"""
    balance_diff: float
    balance_diff_pct: float
    available_diff: float
    available_diff_pct: float
    margin_diff: float
    margin_diff_pct: float
    is_critical: bool
    tolerance_pct: float

    def summary(self) -> str:
        return (
            f"balance_diff={self.balance_diff:+.2f} ({self.balance_diff_pct:+.4%}) | "
            f"available_diff={self.available_diff:+.2f} ({self.available_diff_pct:+.4%}) | "
            f"margin_diff={self.margin_diff:+.2f} ({self.margin_diff_pct:+.4%})"
        )


def _safe_pct(numer: float, denom: float) -> float:
    if abs(denom) < 1e-9:
        return 0.0 if abs(numer) < 1e-9 else float("inf")
    return numer / denom


class MarginReconciler:
    """CTP account vs 本地估算对账器。"""

    def __init__(self, *, tolerance_pct: float = 0.01) -> None:
        self.tolerance_pct = float(tolerance_pct)

    def compare(
        self,
        snapshot: AccountSnapshot,
        local: MarginEstimate,
    ) -> ReconcileDiff:
        """对账并返回 diff 结果。``is_critical=True`` 时 caller 应触发 kill_switch。"""
        balance_diff = snapshot.balance - local.balance
        available_diff = snapshot.available - local.available
        margin_diff = snapshot.margin - local.estimated_margin

        balance_pct = _safe_pct(balance_diff, snapshot.balance)
        available_pct = _safe_pct(available_diff, snapshot.available)
        margin_pct = _safe_pct(margin_diff, snapshot.margin)

        is_critical = (
            abs(balance_pct) > self.tolerance_pct
            or abs(available_pct) > self.tolerance_pct
            or abs(margin_pct) > self.tolerance_pct * 2.0  # 保证金对账容忍度更宽
        )
        return ReconcileDiff(
            balance_diff=balance_diff,
            balance_diff_pct=balance_pct,
            available_diff=available_diff,
            available_diff_pct=available_pct,
            margin_diff=margin_diff,
            margin_diff_pct=margin_pct,
            is_critical=is_critical,
            tolerance_pct=self.tolerance_pct,
        )


__all__ = [
    "AccountSnapshot",
    "MarginEstimate",
    "MarginReconciler",
    "ReconcileDiff",
]
