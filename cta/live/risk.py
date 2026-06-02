"""实盘 / 仿真前置风控规则与 RiskGuard 总开关。

设计
----
所有规则实现 ``check(order: dict, ctx: RiskContext) -> RiskDecision`` 协议。
``RiskGuard.evaluate(order, ctx)`` 顺序执行所有规则，**任意一条失败即整体拒绝**
（短路），返回首条失败的 ``RiskDecision``。

订单字典约定字段（最小集）：
    vt_symbol : str        合约
    direction : str        long / short
    offset    : str        open / close / closetoday / closeyesterday
    volume    : float      手数
    price     : float      价格

挂入 vnpy 流程
--------------
在 CtaTemplate 子类的 ``send_order`` 包装层 / 或在 ``MainEngine`` 注册的
order_request 拦截 hook 中调用 ``guard.evaluate(...)``，被拒时 ``return []``
（与 vnpy 的 ``trading=False`` 路径一致）。
"""
from __future__ import annotations

import collections
import threading
import time
from dataclasses import dataclass, field
from typing import Callable

import pandas as pd
from cta.portfolio_logic.config import CapsConfig, RiskThrottleConfig
from cta.portfolio_logic.risk_throttle import RiskThrottle


@dataclass
class RiskContext:
    pos: dict[str, float]
    daily_pnl: float
    capital: float
    now: pd.Timestamp
    drawdown_pct: float = 0.0
    weekly_return_pct: float = 0.0
    monthly_return_pct: float = 0.0
    open_positions_total: int = 0
    open_positions_by_cluster: dict[str, int] = field(default_factory=dict)


@dataclass
class RiskDecision:
    allowed: bool
    reason: str


class _BaseRule:
    name: str = ""

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:  # pragma: no cover
        raise NotImplementedError


@dataclass
class MaxOrderSize(_BaseRule):
    """单笔最大下单手数（按 vt_symbol 配置）。无配置 → 不限。"""

    limits: dict[str, float]
    name: str = "max_order_size"

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        sym = str(order.get("vt_symbol", ""))
        cap = self.limits.get(sym)
        vol = float(order.get("volume", 0.0))
        if cap is not None and vol > float(cap):
            return RiskDecision(False, f"{self.name}:{sym}={vol}>{cap}")
        return RiskDecision(True, "")


@dataclass
class MaxPositionLimit(_BaseRule):
    """开仓后总持仓不能超过限制（仅检查 ``offset=open``）。"""

    limits: dict[str, float]
    name: str = "max_position"

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        if str(order.get("offset", "")).lower() != "open":
            return RiskDecision(True, "")
        sym = str(order.get("vt_symbol", ""))
        cap = self.limits.get(sym)
        if cap is None:
            return RiskDecision(True, "")
        cur = abs(float(ctx.pos.get(sym, 0.0)))
        nxt = cur + float(order.get("volume", 0.0))
        if nxt > float(cap):
            return RiskDecision(False, f"{self.name}:{sym}={nxt}>{cap}")
        return RiskDecision(True, "")


@dataclass
class DailyLossLimit(_BaseRule):
    """日内累计 PnL 跌破 ``-max_loss`` 时禁止开仓；平仓不阻止以减小风险敞口。"""

    max_loss: float
    name: str = "daily_loss"

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        if str(order.get("offset", "")).lower() != "open":
            return RiskDecision(True, "")
        if ctx.daily_pnl <= -abs(float(self.max_loss)):
            return RiskDecision(
                False, f"{self.name}:pnl={ctx.daily_pnl:.2f}<-{self.max_loss}"
            )
        return RiskDecision(True, "")


@dataclass
class OrderRateLimit(_BaseRule):
    """限制每秒最大委托数（防穿仓 / 保护 gateway）。"""

    max_per_second: int
    name: str = "order_rate"
    _stamps: collections.deque = field(default_factory=collections.deque, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)
    _time_fn: Callable[[], float] = field(default=time.time, init=False, repr=False)

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        with self._lock:
            now = float(self._time_fn())
            # 清掉 1 秒前的 stamp
            while self._stamps and now - self._stamps[0] > 1.0:
                self._stamps.popleft()
            if len(self._stamps) >= self.max_per_second:
                return RiskDecision(False, f"{self.name}:>={self.max_per_second}/s")
            self._stamps.append(now)
            return RiskDecision(True, "")


@dataclass
class PortfolioThrottleRule(_BaseRule):
    """Reuse portfolio_logic.RiskThrottle in live pre-trade checks."""

    base_caps: CapsConfig
    cfg: RiskThrottleConfig
    name: str = "portfolio_throttle"
    _engine: RiskThrottle = field(init=False, repr=False)
    _current_level: object | None = field(default=None, init=False, repr=False)
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def __post_init__(self) -> None:
        self._engine = RiskThrottle(self.cfg)

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        if str(order.get("offset", "")).lower() != "open":
            return RiskDecision(True, "")
        with self._lock:
            snap = self._engine.make_snapshot(
                drawdown_pct=float(getattr(ctx, "drawdown_pct", 0.0)),
                weekly_return_pct=float(getattr(ctx, "weekly_return_pct", 0.0)),
                monthly_return_pct=float(getattr(ctx, "monthly_return_pct", 0.0)),
                equity=float(getattr(ctx, "capital", 0.0)),
            )
            level = self._engine.compute(snap, self._current_level)
            self._current_level = level
            caps = self._engine.apply_to_caps(self.base_caps, level)
        total_open = int(getattr(ctx, "open_positions_total", 0))
        if total_open + 1 > int(caps.max_total_positions):
            return RiskDecision(False, f"{self.name}:throttle_total>{caps.max_total_positions}")
        cluster = str(order.get("cluster", "other")).strip().lower() or "other"
        by_cluster = dict(getattr(ctx, "open_positions_by_cluster", {}) or {})
        cluster_open = int(by_cluster.get(cluster, 0))
        if cluster_open + 1 > int(caps.max_total_per_cluster):
            return RiskDecision(False, f"{self.name}:throttle_cluster>{caps.max_total_per_cluster}")
        return RiskDecision(True, "")


class RiskGuard:
    """风控总开关。短路：第一条失败即整体拒绝。"""

    def __init__(self, rules: list[_BaseRule]) -> None:
        self.rules = list(rules)

    def evaluate(self, order: dict, ctx: RiskContext) -> RiskDecision:
        for rule in self.rules:
            d = rule.check(order, ctx)
            if not d.allowed:
                return d
        return RiskDecision(True, "")


def make_risk_filter(
    guard: RiskGuard,
    *,
    capital: float = 1_000_000.0,
    daily_pnl_provider: Callable[[], float] | None = None,
) -> Callable[[dict, object], bool]:
    """构造可挂入 ``LegacyCtaAdapter.order_filter`` 的 pre-trade hook。

    把 v1 订单字典翻译为风控订单字典：
        side  -> direction (long/short) + offset (open/close)
        lots  -> volume
    并构造 ``RiskContext``（``pos`` 取自 adapter.pos，``daily_pnl`` 由调用方注入）。
    """

    def _filter(order: dict, adapter: object) -> bool:
        side = str(order.get("side", "")).lower()
        if side == "long":
            direction, offset = "long", "open"
        elif side == "short":
            direction, offset = "short", "open"
        elif side == "flat":
            cur = float(getattr(adapter, "pos", 0.0) or 0.0)
            if abs(cur) < 1e-9:
                return True
            direction = "short" if cur > 0 else "long"
            offset = "close"
        else:
            return True  # 未知 side 不拦截

        risk_order = {
            "vt_symbol": str(getattr(adapter, "vt_symbol", "")),
            "direction": direction,
            "offset": offset,
            "volume": float(order.get("lots", 0)),
            "price": float(order.get("price", 0.0)),
        }
        ctx = RiskContext(
            pos={risk_order["vt_symbol"]: float(getattr(adapter, "pos", 0.0) or 0.0)},
            daily_pnl=float(daily_pnl_provider()) if daily_pnl_provider else 0.0,
            capital=float(capital),
            now=pd.Timestamp.now("UTC"),
        )
        d = guard.evaluate(risk_order, ctx)
        if not d.allowed:
            log = getattr(adapter, "write_log", None)
            if callable(log):
                log(f"risk rejected: {d.reason}")
        return d.allowed

    return _filter


__all__ = [
    "DailyLossLimit",
    "MaxOrderSize",
    "MaxPositionLimit",
    "OrderRateLimit",
    "PortfolioThrottleRule",
    "RiskContext",
    "RiskDecision",
    "RiskGuard",
    "make_risk_filter",
]
