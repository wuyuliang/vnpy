"""SkillTightRangeBreakoutCta — TightRange 策略的 CtaTemplate 子类。

该子类继承 ``LegacyCtaAdapter``，复用 ``cta.strategy.skill_tight_range_breakout``
中的 v1 实现（``prepare_strategy_frame`` 计算特征 + ``SkillTightRangeBreakoutStrategy``
事件循环），仅做接口适配；策略核心逻辑不重写，与 event-driven 回测共享同一份代码。

合约元数据（multiplier / tick_size / commission_rate / slippage_ticks）从 vnpy
``setting`` 读入；若 ``size`` / ``pricetick`` 未填，则尝试调用 ``cta_engine.get_size`` /
``get_pricetick`` 兜底。
"""
from __future__ import annotations

from dataclasses import fields
from typing import Any

import pandas as pd

from cta.config.skill_tight_range_breakout_config import StrategyConfig
from cta.strategy.cta_adapter import LegacyCtaAdapter
from cta.strategy.skill_tight_range_breakout import (
    ContractSpec,
    SkillTightRangeBreakoutStrategy,
    prepare_strategy_frame,
)


def _strategy_config_fields() -> list[str]:
    return [f.name for f in fields(StrategyConfig)]


class SkillTightRangeBreakoutCta(LegacyCtaAdapter):
    """vnpy CtaTemplate 适配的窄幅整理放量突破策略。"""

    # 把 StrategyConfig 全部字段暴露为 vnpy parameters
    parameters: list[str] = _strategy_config_fields() + [
        "multiplier", "tick_size", "commission_rate", "slippage_ticks", "capital_base",
    ]

    # StrategyConfig 默认值（被 setting 覆盖）
    lookback: int = 10
    alpha: float = 1.5
    min_count: int = 5
    min_breakout_score: float = 0.30
    lots: int = 1
    risk_per_trade_pct: float = 0.005
    initial_stop_atr_mult: float = 1.5
    trailing_stop_atr_mult: float = 2.2
    max_holding_bars: int = 20
    align_trend_direction: bool = True
    trade_side_mode: str = "both"

    # ContractSpec 默认值
    multiplier: float = 10.0
    tick_size: float = 1.0
    commission_rate: float = 0.0001
    slippage_ticks: float = 1.5
    capital_base: float = 1_000_000.0

    history_size: int = 600  # 600 根 bar 足以覆盖 lookback=10 + ATR14 + tight_range 滚动窗

    def _build_strategy_config(self) -> StrategyConfig:
        return StrategyConfig(
            lookback=int(self.lookback),
            alpha=float(self.alpha),
            min_count=int(self.min_count),
            min_breakout_score=float(self.min_breakout_score),
            lots=int(self.lots),
            risk_per_trade_pct=float(self.risk_per_trade_pct),
            initial_stop_atr_mult=float(self.initial_stop_atr_mult),
            trailing_stop_atr_mult=float(self.trailing_stop_atr_mult),
            max_holding_bars=int(self.max_holding_bars),
            align_trend_direction=bool(self.align_trend_direction),
            trade_side_mode=str(self.trade_side_mode),
        )

    def _build_contract(self) -> ContractSpec:
        symbol, _, exchange = self.vt_symbol.partition(".")
        # 优先从 cta_engine 拿真实合约元数据；返回 0/None/异常时回退 setting 字段值
        tick_size = 0.0
        try:
            v = self.cta_engine.get_pricetick(self)
            tick_size = float(v) if v else 0.0
        except Exception:  # noqa: BLE001
            tick_size = 0.0
        if tick_size <= 0:
            tick_size = float(self.tick_size or 1.0)

        multiplier = 0.0
        try:
            v = self.cta_engine.get_size(self)
            multiplier = float(v) if v else 0.0
        except Exception:  # noqa: BLE001
            multiplier = 0.0
        if multiplier <= 0:
            multiplier = float(self.multiplier or 1.0)

        return ContractSpec(
            symbol=symbol or "",
            exchange=exchange or "",
            multiplier=multiplier,
            tick_size=tick_size,
            commission_rate=float(self.commission_rate),
            slippage_ticks=float(self.slippage_ticks),
        )

    def prepare_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        return prepare_strategy_frame(df, self._build_strategy_config(), interval="day")

    def make_inner(self, frame: pd.DataFrame) -> Any:
        return SkillTightRangeBreakoutStrategy(
            frame=frame,
            cfg=self._build_strategy_config(),
            contract=self._build_contract(),
            capital_base=float(self.capital_base),
        )


__all__ = ["SkillTightRangeBreakoutCta"]
