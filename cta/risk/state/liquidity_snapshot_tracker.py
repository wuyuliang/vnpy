"""Per-symbol 流动性指标滚动追踪：为 §16.2 LiquidityFloorGuard 提供事前流动性查询。

设计：
- 内存维护 N 日 volume / open_interest deque（默认 20 日）
- on_bar(symbol, dt, volume, open_interest, close)：每根日线 / 60min K 线调一次
- update_quote(symbol, bid_price, ask_price, tick_size)：可选，sim/live 实时 quote 更新
- snapshot(symbol) → LiquiditySnapshot 含 volume_ratio / spread_ticks / turnover_ratio

fail-open：
- symbol 没有历史数据 → snapshot 全字段为 None，guard 自动 fail-open
- volume_ratio 分母（中位数）为 0 → 跳过该指标
- bid >= ask（异常 quote）→ spread_ticks=None
"""
from __future__ import annotations

import logging
from collections import deque
from dataclasses import dataclass
from typing import Iterable

import numpy as np
import pandas as pd

from cta.risk.base import normalize_symbol

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class LiquiditySnapshot:
    """单 symbol 的流动性指标快照。任何字段 None 表示数据不足，guard 应 fail-open。"""

    symbol: str
    volume_ratio: float | None      # 当前 volume / N 日中位数
    spread_ticks: float | None      # (ask-bid)/tick_size
    turnover_ratio: float | None    # volume × close / open_interest
    sample_count: int


class LiquiditySnapshotTracker:
    """N 日 volume / OI / 最新 quote 滚动状态。"""

    def __init__(self, *, window_size: int = 20) -> None:
        if window_size < 1:
            raise ValueError(f"window_size must be >= 1, got {window_size}")
        self.window_size = int(window_size)
        # symbol -> deque of (volume, oi, close)
        self._history: dict[str, deque[tuple[float, float, float]]] = {}
        # symbol -> (bid, ask, tick_size)
        self._quotes: dict[str, tuple[float, float, float]] = {}

    # ── update ──────────────────────────────────────────────────────

    def on_bar(
        self,
        *,
        symbol: str,
        volume: float,
        open_interest: float,
        close: float,
    ) -> None:
        """每根 K 线更新一次。NaN / 负值跳过。"""
        sym = normalize_symbol(symbol)
        if not sym:
            return
        try:
            v = float(volume)
            oi = float(open_interest)
            cl = float(close)
        except (TypeError, ValueError):
            return
        if not (np.isfinite(v) and np.isfinite(oi) and np.isfinite(cl)):
            return
        if v < 0 or oi < 0 or cl <= 0:
            return
        dq = self._history.setdefault(sym, deque(maxlen=self.window_size))
        dq.append((v, oi, cl))

    def update_quote(
        self,
        *,
        symbol: str,
        bid_price: float,
        ask_price: float,
        tick_size: float,
    ) -> None:
        sym = normalize_symbol(symbol)
        if not sym:
            return
        try:
            b = float(bid_price)
            a = float(ask_price)
            t = float(tick_size)
        except (TypeError, ValueError):
            return
        if not (np.isfinite(b) and np.isfinite(a) and np.isfinite(t)):
            return
        if t <= 0 or a <= 0 or b <= 0 or a < b:
            return
        self._quotes[sym] = (b, a, t)

    def replay(self, bars: Iterable[dict]) -> int:
        """批量回放 on_bar；返回处理的有效条数。OOT 单测 / 回放使用。"""
        n = 0
        for row in bars:
            self.on_bar(
                symbol=str(row.get("symbol", "")),
                volume=float(row.get("volume", 0.0) or 0.0),
                open_interest=float(row.get("open_interest", 0.0) or 0.0),
                close=float(row.get("close", 0.0) or 0.0),
            )
            n += 1
        return n

    # ── query ───────────────────────────────────────────────────────

    def snapshot(self, symbol: str) -> LiquiditySnapshot:
        sym = normalize_symbol(symbol)
        dq = self._history.get(sym)
        if not dq:
            return LiquiditySnapshot(
                symbol=sym, volume_ratio=None, spread_ticks=None,
                turnover_ratio=None, sample_count=0,
            )
        # volume_ratio = 当前 volume / 历史中位数
        volumes = [v for v, _, _ in dq]
        cur_vol = float(volumes[-1])
        median_vol = float(np.median(volumes))
        volume_ratio: float | None = None
        if median_vol > 0:
            volume_ratio = cur_vol / median_vol
        # turnover_ratio = cur_vol × cur_close / cur_oi
        cur_v, cur_oi, cur_close = dq[-1]
        turnover_ratio: float | None = None
        if cur_oi > 0:
            turnover_ratio = (cur_v * cur_close) / cur_oi
        # spread_ticks 来自 quote
        spread_ticks: float | None = None
        quote = self._quotes.get(sym)
        if quote is not None:
            bid, ask, tick = quote
            if ask > bid and tick > 0:
                spread_ticks = (ask - bid) / tick
        return LiquiditySnapshot(
            symbol=sym,
            volume_ratio=volume_ratio,
            spread_ticks=spread_ticks,
            turnover_ratio=turnover_ratio,
            sample_count=int(len(dq)),
        )

    def known_symbols(self) -> list[str]:
        return list(self._history.keys())


__all__ = ["LiquiditySnapshot", "LiquiditySnapshotTracker"]
