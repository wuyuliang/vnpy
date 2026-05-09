"""日内已实现 PnL 累加器。

设计目标
--------
为 ``cta.live.risk.make_risk_filter(daily_pnl_provider=...)`` 提供数据源。
浮动 PnL 不在这里处理（vnpy 账户系统更准确，需要 tick 数据）；这里只追踪
**已实现** PnL：当一笔 close 成交把先前 open 配对掉时累加 ``(exit-entry)*size*lots``。

约定
----
- 接收 vnpy ``TradeData`` 对象（``direction.value`` / ``offset.value`` / ``price`` /
  ``volume`` / ``symbol`` / ``exchange.value``）
- 跨 vt_symbol 各自维护持仓 FIFO 队列
- ``contract_size_resolver(vt_symbol)`` 返回该合约 multiplier；默认 1.0（股票场景）
- ``commission_resolver(vt_symbol, price, volume)`` 返回单边成本；默认 0
- ``reset_for_new_day(date)`` 清零累计 PnL（持仓队列保留，跨日不平仓）
"""
from __future__ import annotations

from collections import defaultdict, deque
from datetime import date
from typing import Any, Callable


class DailyPnlTracker:
    def __init__(
        self,
        *,
        contract_size_resolver: Callable[[str], float] | None = None,
        commission_resolver: Callable[[str, float, float], float] | None = None,
    ) -> None:
        self._size_of: Callable[[str], float] = contract_size_resolver or (lambda s: 1.0)
        self._commission_of: Callable[[str, float, float], float] = (
            commission_resolver or (lambda s, p, v: 0.0)
        )
        # vt_symbol -> direction("long"/"short") -> deque of {price, vol}
        self._queues: dict[str, dict[str, deque]] = defaultdict(
            lambda: {"long": deque(), "short": deque()}
        )
        self._realized_pnl: float = 0.0
        self._date: date | None = None

    def _vt_symbol(self, trade: Any) -> str:
        sym = getattr(trade, "symbol", "")
        ex = getattr(trade, "exchange", "")
        ex_val = getattr(ex, "value", ex) if ex else ""
        return f"{sym}.{ex_val}" if ex_val else str(sym)

    def on_trade(self, trade: Any) -> None:
        vt = self._vt_symbol(trade)
        direction = str(getattr(getattr(trade, "direction", ""), "value", trade.__dict__.get("direction", ""))).lower()
        offset = str(getattr(getattr(trade, "offset", ""), "value", trade.__dict__.get("offset", ""))).lower()
        price = float(getattr(trade, "price", 0.0) or 0.0)
        volume = float(getattr(trade, "volume", 0.0) or 0.0)
        if volume <= 0:
            return

        size = float(self._size_of(vt))
        comm = float(self._commission_of(vt, price, volume))

        queues = self._queues[vt]
        if "open" in offset:
            side = "long" if "long" in direction else "short"
            queues[side].append({"price": price, "vol": volume})
            self._realized_pnl -= comm   # 开仓手续费
            return

        if "close" not in offset:
            return  # 未知 offset

        # close: 配对相反方向
        counter = "long" if "short" in direction else "short"
        q = queues[counter]
        remaining = volume
        while remaining > 1e-9 and q:
            head = q[0]
            used = min(head["vol"], remaining)
            entry = float(head["price"])
            gross = (price - entry) * used * size
            if counter == "short":
                gross = -gross
            self._realized_pnl += gross
            head["vol"] -= used
            remaining -= used
            if head["vol"] <= 1e-9:
                q.popleft()
        self._realized_pnl -= comm   # 平仓手续费

    def get_pnl(self) -> float:
        return float(self._realized_pnl)

    def reset_for_new_day(self, today: date | None = None) -> None:
        self._realized_pnl = 0.0
        self._date = today


__all__ = ["DailyPnlTracker"]
