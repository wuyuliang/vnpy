"""三个 baseline CtaTemplate 子类：

- ``DonchianCta``         — Donchian 通道突破
- ``AtrBreakoutCta``      — ATR 通道突破
- ``BreakoutPullbackCta`` — 突破回拉延续

均共享 ``prepare_master_feature_frame`` 计算特征，只在 ``make_inner`` 中
派生不同 v1 策略对象。
"""
from __future__ import annotations

from typing import Any

import pandas as pd

from cta.strategy.baseline_skill_suite import (
    ATRBreakoutBaselineStrategy,
    BreakoutPullbackBaselineStrategy,
    DonchianBaselineStrategy,
    prepare_master_feature_frame,
)
from cta.strategy.cta_adapter import LegacyCtaAdapter
from cta.strategy.skill_tight_range_breakout import ContractSpec


class _BaselineCtaBase(LegacyCtaAdapter):
    """通用 baseline adapter：共享 contract 和 prepare_frame。"""

    parameters: list[str] = [
        "trade_side_mode",
        "multiplier",
        "tick_size",
        "commission_rate",
        "slippage_ticks",
    ]

    trade_side_mode: str = "both"
    multiplier: float = 10.0
    tick_size: float = 1.0
    commission_rate: float = 0.0001
    slippage_ticks: float = 1.5

    history_size: int = 600
    interval: str = "day"

    def _build_contract(self) -> ContractSpec:
        symbol, _, exchange = self.vt_symbol.partition(".")
        tick = float(self.tick_size)
        if tick <= 0:
            try:
                tick = float(self.cta_engine.get_pricetick(self) or 1.0)
            except Exception:  # noqa: BLE001
                tick = 1.0
        mult = float(self.multiplier)
        if mult <= 0:
            try:
                mult = float(self.cta_engine.get_size(self) or 1.0)
            except Exception:  # noqa: BLE001
                mult = 1.0
        return ContractSpec(
            symbol=symbol or "",
            exchange=exchange or "",
            multiplier=mult,
            tick_size=tick,
            commission_rate=float(self.commission_rate),
            slippage_ticks=float(self.slippage_ticks),
        )

    def prepare_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        return prepare_master_feature_frame(df, interval=self.interval)


class DonchianCta(_BaselineCtaBase):
    """Donchian 通道突破。"""

    def make_inner(self, frame: pd.DataFrame) -> Any:
        return DonchianBaselineStrategy(
            frame=frame,
            contract=self._build_contract(),
            trade_side_mode=str(self.trade_side_mode),
        )


class AtrBreakoutCta(_BaselineCtaBase):
    """ATR 通道突破。"""

    def make_inner(self, frame: pd.DataFrame) -> Any:
        return ATRBreakoutBaselineStrategy(
            frame=frame,
            contract=self._build_contract(),
            trade_side_mode=str(self.trade_side_mode),
        )


class BreakoutPullbackCta(_BaselineCtaBase):
    """突破回拉延续策略。"""

    parameters: list[str] = _BaselineCtaBase.parameters + [
        "max_holding_bars",
        "trailing_stop_atr_mult",
        "initial_stop_atr_mult",
    ]

    max_holding_bars: int = 30
    trailing_stop_atr_mult: float = 2.0
    initial_stop_atr_mult: float = 1.2

    def make_inner(self, frame: pd.DataFrame) -> Any:
        return BreakoutPullbackBaselineStrategy(
            frame=frame,
            contract=self._build_contract(),
            trade_side_mode=str(self.trade_side_mode),
            max_holding_bars=int(self.max_holding_bars),
            trailing_stop_atr_mult=float(self.trailing_stop_atr_mult),
            initial_stop_atr_mult=float(self.initial_stop_atr_mult),
        )


__all__ = ["AtrBreakoutCta", "BreakoutPullbackCta", "DonchianCta"]
