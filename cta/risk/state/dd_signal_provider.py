"""Drawdown signal provider：包装 EquityTracker，给三层组件提供 dd 信号。

设计：
- cumulative_dd_pct: 复用 ``EquityTracker.current_drawdown_pct``（从 running_high 起算）
- weekly_dd_pct: 新算 —— 近 5 个交易日内的 running_high 到当下 equity 的回撤
- effective_dd_pct: max(cumulative, weekly)（用户决策）

支持两种构造：
1. ``DdSignalProvider(equity_tracker)``：直接包装现有 EquityTracker；
2. ``DdSignalProvider.from_static(cumulative_dd_pct, weekly_dd_pct)``：单测/OOT 时用静态值。

fail-open：empty tracker → 全 0；NaN → 0。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass

import pandas as pd

logger = logging.getLogger(__name__)


@dataclass
class _StaticDdState:
    cumulative_dd_pct: float = 0.0
    weekly_dd_pct: float = 0.0


class DdSignalProvider:
    """Drawdown 信号提供器。"""

    def __init__(
        self,
        equity_tracker=None,
        *,
        weekly_window_days: int = 7,
    ) -> None:
        self._tracker = equity_tracker
        self._static: _StaticDdState | None = None
        self.weekly_window_days = int(weekly_window_days)

    # ── alt constructors ────────────────────────────────────────────

    @classmethod
    def from_static(
        cls,
        *,
        cumulative_dd_pct: float = 0.0,
        weekly_dd_pct: float = 0.0,
    ) -> "DdSignalProvider":
        """用静态值构造（单测/OOT batch 用）。"""
        out = cls(equity_tracker=None)
        out._static = _StaticDdState(
            cumulative_dd_pct=float(cumulative_dd_pct),
            weekly_dd_pct=float(weekly_dd_pct),
        )
        return out

    # ── signals ─────────────────────────────────────────────────────

    def cumulative_dd_pct(self) -> float:
        if self._static is not None:
            return float(self._static.cumulative_dd_pct)
        if self._tracker is None:
            return 0.0
        try:
            v = float(self._tracker.current_drawdown_pct())
            if not pd.notna(v):
                return 0.0
            return max(0.0, v)
        except Exception as exc:  # noqa: BLE001
            logger.debug("cumulative_dd_pct lookup failed: %s", exc)
            return 0.0

    def weekly_dd_pct(self) -> float:
        if self._static is not None:
            return float(self._static.weekly_dd_pct)
        if self._tracker is None:
            return 0.0
        try:
            hist = getattr(self._tracker, "equity_history", None)
            if not hist:
                return 0.0
            last_ts = pd.Timestamp(hist[-1][0])
            current = float(hist[-1][1])
            cutoff = last_ts - pd.Timedelta(days=int(self.weekly_window_days))
            window_max = current
            for ts, eq in reversed(hist):
                if pd.Timestamp(ts) < cutoff:
                    break
                window_max = max(window_max, float(eq))
            if window_max <= 0:
                return 0.0
            return max(0.0, 1.0 - current / window_max)
        except Exception as exc:  # noqa: BLE001
            logger.debug("weekly_dd_pct lookup failed: %s", exc)
            return 0.0

    def effective_dd_pct(self) -> float:
        """max(cumulative, weekly) —— 用户决策。"""
        return max(self.cumulative_dd_pct(), self.weekly_dd_pct())

    # ── update ──────────────────────────────────────────────────────

    def set_static(
        self,
        *,
        cumulative_dd_pct: float | None = None,
        weekly_dd_pct: float | None = None,
    ) -> None:
        """覆盖静态值（OOT 批处理逐 candidate 注入时用）。"""
        if self._static is None:
            self._static = _StaticDdState()
        if cumulative_dd_pct is not None:
            self._static = _StaticDdState(
                cumulative_dd_pct=float(cumulative_dd_pct),
                weekly_dd_pct=float(self._static.weekly_dd_pct),
            )
        if weekly_dd_pct is not None:
            self._static = _StaticDdState(
                cumulative_dd_pct=float(self._static.cumulative_dd_pct),
                weekly_dd_pct=float(weekly_dd_pct),
            )


__all__ = ["DdSignalProvider"]
