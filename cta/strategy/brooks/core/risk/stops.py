"""止损引擎:初始止损 / 移动止损 / 失败快退 / 最长持仓。

对每一笔开仓维护一个 StopState,每根新 bar 调用 `StopEngine.update` 更新状态,
`should_exit` 返回 True 表示需要平仓。

所有判断在 bar close 后做,避免使用 bar 内部的 tick 级信息。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from enum import Enum

logger = logging.getLogger(__name__)


class ExitReason(str, Enum):
    NONE = "none"
    ATR_STOP = "atr_stop"
    TRAILING_STOP = "trailing_stop"
    FAIL_EXIT = "fail_exit"
    MAX_HOLDING = "max_holding"


@dataclass
class StopState:
    entry_price: float
    entry_idx: int                 # 入场时的 bar 序号
    direction: int = 1             # 1 = long only in v3
    atr: float = 0.0
    initial_stop: float = 0.0      # 初始止损价
    trailing_stop: float = 0.0     # 当前移动止损价
    peak_price: float = 0.0        # 多头:入场后最高价
    bars_held: int = 0

    def __post_init__(self) -> None:
        self.peak_price = self.entry_price


@dataclass
class StopEngine:
    stop_atr_mult: float = 1.0
    trailing_atr_mult: float = 2.0
    fail_exit_bars: int = 3
    max_holding_bars: int = 30

    def init_state(
        self,
        entry_price: float,
        entry_idx: int,
        atr: float,
        direction: int = 1,
    ) -> StopState:
        st = StopState(
            entry_price=entry_price, entry_idx=entry_idx,
            direction=direction, atr=atr,
        )
        st.initial_stop = entry_price - self.stop_atr_mult * atr
        st.trailing_stop = st.initial_stop
        logger.debug("StopEngine init: entry=%.2f stop=%.2f atr=%.3f",
                     entry_price, st.initial_stop, atr)
        return st

    def on_bar(
        self,
        st: StopState,
        bar_high: float,
        bar_low: float,
        bar_close: float,
    ) -> tuple[ExitReason, float]:
        """处理一根新 bar,返回 (退出原因, 退出价)。NONE 表示继续持仓。"""
        st.bars_held += 1
        st.peak_price = max(st.peak_price, bar_high)

        # 初始止损
        if bar_low <= st.initial_stop:
            logger.debug("stop: atr_stop hit low=%.2f init=%.2f",
                         bar_low, st.initial_stop)
            return ExitReason.ATR_STOP, st.initial_stop

        # 更新移动止损(只上移)
        new_trail = st.peak_price - self.trailing_atr_mult * st.atr
        if new_trail > st.trailing_stop:
            st.trailing_stop = new_trail

        # 移动止损
        if bar_low <= st.trailing_stop and st.trailing_stop > st.initial_stop:
            logger.debug("stop: trailing_stop hit low=%.2f trail=%.2f",
                         bar_low, st.trailing_stop)
            return ExitReason.TRAILING_STOP, st.trailing_stop

        # 失败快退:入场后 N 根 bar,peak 未突破入场价视为失败
        if st.bars_held == self.fail_exit_bars and st.peak_price <= st.entry_price:
            logger.debug("stop: fail_exit bars_held=%d peak=%.2f entry=%.2f",
                         st.bars_held, st.peak_price, st.entry_price)
            return ExitReason.FAIL_EXIT, bar_close

        # 最长持仓
        if st.bars_held >= self.max_holding_bars:
            logger.debug("stop: max_holding bars_held=%d", st.bars_held)
            return ExitReason.MAX_HOLDING, bar_close

        return ExitReason.NONE, 0.0


__all__ = ["StopEngine", "StopState", "ExitReason"]
