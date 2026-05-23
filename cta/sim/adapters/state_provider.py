"""State provider: ma_alignment / regime_label / realized_vol lookup for sim/live.

为什么需要这个 adapter
----------------------
- ``PositionTrendState`` 构造需要 3 个 lookup callable
- ``trend_aware_trade_filter`` / ``ma_cross_gate`` 需要 candidate 行带 ma_alignment /
  regime_label / realized_vol_rank 列
- sim/live 是 per-bar/per-trade 调用，与 OOT 的 batch DataFrame 不同

本模块给 sim 主循环提供统一的 query 接口（基于 [OnlineFeatureLoader]
(../../live/online_feature.py)），让 OOT/sim/live 三层用同一份特征数据。
"""
from __future__ import annotations

import logging
from typing import Protocol

import numpy as np
import pandas as pd

from cta.live.online_feature import OnlineFeatureLoader

logger = logging.getLogger(__name__)


class StateProvider(Protocol):
    """sim 主循环提供 ma_alignment / regime_label / realized_vol 查询。"""

    def lookup_ma_alignment(self, symbol: str, dt: pd.Timestamp) -> int: ...
    def lookup_regime_label(self, symbol: str, dt: pd.Timestamp) -> str: ...
    def lookup_realized_vol(self, symbol: str, dt: pd.Timestamp) -> float: ...


class FeatureBasedStateProvider:
    """从 OnlineFeatureLoader 提供 state 查询；缺失时 fallback 到中性值。

    优先列名（OnlineFeatureLoader 已读 parquet）：
    - ma_alignment: ``generic_ma_alignment`` → ``ma_alignment``
    - regime_label: ``regime_label``
    - realized_vol: ``realized_vol_20d`` → ``atr_pct`` → fallback 0.02
    """

    def __init__(
        self,
        loader: OnlineFeatureLoader,
        *,
        interval: str = "day",
        ma_align_columns: tuple[str, ...] = ("generic_ma_alignment", "ma_alignment"),
        regime_label_columns: tuple[str, ...] = ("regime_label",),
        vol_columns: tuple[str, ...] = ("realized_vol_20d", "atr_pct"),
        fallback_vol: float = 0.02,
    ) -> None:
        self.loader = loader
        self.interval = str(interval)
        self.ma_align_columns = tuple(ma_align_columns)
        self.regime_label_columns = tuple(regime_label_columns)
        self.vol_columns = tuple(vol_columns)
        self.fallback_vol = float(fallback_vol)

    def _read_row(self, symbol: str, dt: pd.Timestamp) -> pd.Series | None:
        return self.loader.load_at(symbol=symbol, interval=self.interval, dt=dt)

    @staticmethod
    def _pick(row: pd.Series, candidates: tuple[str, ...]) -> object:
        for col in candidates:
            if col in row.index:
                v = row[col]
                if v is not None and (isinstance(v, str) or np.isfinite(float(v))):
                    return v
        return None

    def lookup_ma_alignment(self, symbol: str, dt: pd.Timestamp) -> int:
        row = self._read_row(symbol, dt)
        if row is None:
            return 0
        v = self._pick(row, self.ma_align_columns)
        if v is None:
            return 0
        try:
            return int(round(float(v)))
        except (TypeError, ValueError):
            return 0

    def lookup_regime_label(self, symbol: str, dt: pd.Timestamp) -> str:
        row = self._read_row(symbol, dt)
        if row is None:
            return ""
        v = self._pick(row, self.regime_label_columns)
        if v is None:
            return ""
        return str(v).strip().lower()

    def lookup_realized_vol(self, symbol: str, dt: pd.Timestamp) -> float:
        row = self._read_row(symbol, dt)
        if row is None:
            return self.fallback_vol
        v = self._pick(row, self.vol_columns)
        if v is None:
            return self.fallback_vol
        try:
            return float(v)
        except (TypeError, ValueError):
            return self.fallback_vol


__all__ = ["FeatureBasedStateProvider", "StateProvider"]
