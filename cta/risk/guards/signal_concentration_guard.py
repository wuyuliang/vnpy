"""§17.3 SignalConcentrationGuard：同类信号并发上限。

防止 cross_sectional rotation 一次 30+ 同质 momentum 信号同时下单。

判定（仅 open）：
    增量后 signal_type 计数 > max_per_signal_type_per_bar  → 拒
    增量后 cluster 计数      > max_per_cluster_per_bar      → 拒

设计：本 guard **内部维护** per-bar 累计计数（无需 caller 显式传计数）。caller 在
新 bar 开始时调 ``reset_for_new_bar(bar_dt)``，bar 内多次 check 会累计。

state 持久化：内存即可（per-bar 累计，跨进程重启自动 reset）。
"""
from __future__ import annotations

import logging
from collections import defaultdict
from datetime import date

import pandas as pd

from cta.live.risk import RiskContext, RiskDecision, _BaseRule
from cta.risk.base import normalize_cluster
from cta.risk.guards.config import SignalConcentrationGuardConfig

logger = logging.getLogger(__name__)


class SignalConcentrationGuard(_BaseRule):
    """同类信号并发上限。"""

    name: str = "signal_concentration"

    def __init__(self, cfg: SignalConcentrationGuardConfig | None = None) -> None:
        self.cfg = cfg or SignalConcentrationGuardConfig()
        self._signal_count: dict[str, int] = defaultdict(int)
        self._cluster_count: dict[str, int] = defaultdict(int)
        self._current_bar_key: tuple[date, pd.Timestamp] | None = None

    # ── update ──────────────────────────────────────────────────────

    def reset_for_new_bar(self, bar_dt: pd.Timestamp) -> None:
        """caller 在新 bar 开始时调一次。"""
        self._signal_count.clear()
        self._cluster_count.clear()
        self._current_bar_key = (pd.Timestamp(bar_dt).date(), pd.Timestamp(bar_dt))

    def reset_for_new_session(self) -> None:
        """caller 在新交易日开盘时调（reset_at_session_open=True 时）。"""
        self._signal_count.clear()
        self._cluster_count.clear()
        self._current_bar_key = None

    # ── check ───────────────────────────────────────────────────────

    def check(self, order: dict, ctx: RiskContext) -> RiskDecision:
        offset = str(order.get("offset", "")).strip().lower()
        if offset != "open":
            return RiskDecision(True, "")
        # 自动 reset：若 ctx.now 与 _current_bar_key 不同 bar/日 → reset
        now = pd.Timestamp(getattr(ctx, "now", pd.Timestamp.now()))
        bar_key = (now.date(), now.floor("min"))
        if self._current_bar_key is None or bar_key != self._current_bar_key:
            self._signal_count.clear()
            self._cluster_count.clear()
            self._current_bar_key = bar_key
        signal_type = str(order.get("signal_type", "")).strip().lower() or "_unknown"
        cluster = normalize_cluster(order.get("cluster", "")) or "_unknown"
        # 增量后计数
        new_signal = self._signal_count[signal_type] + 1
        new_cluster = self._cluster_count[cluster] + 1
        if new_signal > self.cfg.max_per_signal_type_per_bar:
            return RiskDecision(
                False,
                f"{self.name}:signal_full:{signal_type}:"
                f"{new_signal}>{self.cfg.max_per_signal_type_per_bar}",
            )
        if new_cluster > self.cfg.max_per_cluster_per_bar:
            return RiskDecision(
                False,
                f"{self.name}:cluster_full:{cluster}:"
                f"{new_cluster}>{self.cfg.max_per_cluster_per_bar}",
            )
        # 通过：写入计数（"接受"该 order）
        self._signal_count[signal_type] = new_signal
        self._cluster_count[cluster] = new_cluster
        return RiskDecision(True, "")


__all__ = ["SignalConcentrationGuard"]
