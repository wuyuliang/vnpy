"""LegacyCtaAdapter — 把现有 ``on_bar(i, bar, position)`` 接口的 v1 策略
适配到 ``vnpy_ctastrategy.CtaTemplate``，使得同一份核心策略逻辑可在：

- ``cta.skills.data_backtest.event_driven_backtest`` （研究用，单进程极快）
- ``vnpy_ctabacktester.BacktestingEngine`` （准实盘对齐回测）
- ``vnpy_ctp`` SimNow / 实盘
- 三处复用，避免逻辑分叉造成的回测↔实盘背离。

子类约定
--------
1. ``parameters: list[str]`` — vnpy ``setting`` 中允许覆盖的字段名。
2. ``prepare_frame(df) -> pd.DataFrame`` — 把 OHLCV 预处理为含特征的 DataFrame
   （和 v1 ``prepare_strategy_frame`` 等价的逻辑）。
3. ``make_inner(frame) -> object`` — 实例化 v1 策略（提供 ``on_bar(i, bar, pos)`` 接口）。
   返回对象的 ``frame`` 属性需可写（adapter 在 stream 模式下会更新它）。

Adapter 行为
------------
- 在 ``on_bar`` 中累积一个限长 buffer（默认 1000 根 bar）。
- 每根 bar 调用 ``prepare_frame`` 重建特征，再调用 ``inner.on_bar(i, last, pos)``。
- 把 v1 订单字典翻译成 ``buy/sell/short/cover``（``order_type='stop'`` → ``stop=True``）。
- 任何来自 ``prepare_frame`` / ``make_inner`` / ``inner.on_bar`` 的异常被捕获并写日志，
  不向上传播，避免 cta_engine 崩溃。

风控接入
--------
通过 ``order_filter`` 类属性 / 实例属性注入 pre-trade hook：

    adapter.order_filter = make_risk_filter(RiskGuard([...]))

签名：``Callable[[order_dict, adapter], bool]``。返回 ``False`` 时 adapter 跳过该订单
（不调 buy/sell/short/cover），用于风控、kill switch、合规过滤等场景。

不做的事
--------
- 不做多品种持仓拆分（CtaTemplate 单 vt_symbol 模型）。
- 不调用 ``cancel_all`` 之外的撤单（v1 接口没有撤单语义）。
"""
from __future__ import annotations

from typing import Any, Callable

import pandas as pd

from vnpy.trader.object import BarData
from vnpy_ctastrategy import CtaTemplate


def bars_to_df(bars: list[BarData]) -> pd.DataFrame:
    """Convert a list of vnpy BarData into a pandas DataFrame compatible with
    v1 strategies (columns datetime/open/high/low/close/volume/open_interest/turnover).
    """
    if not bars:
        return pd.DataFrame(
            columns=["datetime", "open", "high", "low", "close", "volume", "open_interest", "turnover"]
        )
    return pd.DataFrame(
        {
            "datetime": [b.datetime for b in bars],
            "open": [float(b.open_price) for b in bars],
            "high": [float(b.high_price) for b in bars],
            "low": [float(b.low_price) for b in bars],
            "close": [float(b.close_price) for b in bars],
            "volume": [float(b.volume) for b in bars],
            "open_interest": [float(b.open_interest) for b in bars],
            "turnover": [float(b.turnover) for b in bars],
        }
    )


class LegacyCtaAdapter(CtaTemplate):
    """Base CtaTemplate that wraps a v1 ``on_bar(i, bar, position)`` strategy."""

    parameters: list[str] = []
    variables: list[str] = []
    history_size: int = 1000
    # Pre-trade hook: ``Callable[[order_dict, adapter], bool]``，返回 False 时跳过 send_order
    order_filter: Callable[[dict, "LegacyCtaAdapter"], bool] | None = None
    # 实盘 / 仿真观测点（M3）：成交流水记录器与日内已实现 PnL 跟踪器
    trade_recorder: Any = None  # cta.live.trade_recorder.TradeRecorder | None
    pnl_tracker: Any = None     # cta.live.pnl_tracker.DailyPnlTracker | None

    def __init__(self, cta_engine: Any, strategy_name: str, vt_symbol: str, setting: dict) -> None:
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self._buffer: list[BarData] = []
        self._inner: Any = None
        self._frame: pd.DataFrame | None = None

    # ------- subclass hooks -------
    def prepare_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        raise NotImplementedError

    def make_inner(self, frame: pd.DataFrame) -> Any:
        raise NotImplementedError

    # ------- CtaTemplate callbacks -------
    def on_init(self) -> None:
        self._buffer = []
        self._inner = None
        self._frame = None
        self.write_log(f"{self.__class__.__name__} init")

    def on_start(self) -> None:
        self.write_log(f"{self.__class__.__name__} start")

    def on_stop(self) -> None:
        self.write_log(f"{self.__class__.__name__} stop")
        self.cancel_all()
        if self.trade_recorder is not None:
            try:
                path = self.trade_recorder.flush()
                if path:
                    self.write_log(f"trade_recorder flushed: {path}")
            except Exception as e:  # noqa: BLE001
                self.write_log(f"trade_recorder flush error: {e}")

    def on_trade(self, trade: Any) -> None:
        """实盘 / 仿真成交回报：转给 trade_recorder 与 pnl_tracker。
        v1 策略的状态由 on_bar 驱动，此处不再调 inner。"""
        if self.trade_recorder is not None:
            try:
                self.trade_recorder.record(trade)
            except Exception as e:  # noqa: BLE001
                self.write_log(f"trade_recorder error: {e}")
        if self.pnl_tracker is not None:
            try:
                self.pnl_tracker.on_trade(trade)
            except Exception as e:  # noqa: BLE001
                self.write_log(f"pnl_tracker error: {e}")

    def on_bar(self, bar: BarData) -> None:
        self._buffer.append(bar)
        if len(self._buffer) > self.history_size:
            del self._buffer[: len(self._buffer) - self.history_size]
        if len(self._buffer) < 2:
            return

        df = bars_to_df(self._buffer)
        try:
            df = self.prepare_frame(df)
        except Exception as e:  # noqa: BLE001
            self.write_log(f"prepare_frame failed: {e}")
            return
        self._frame = df

        if self._inner is None:
            try:
                self._inner = self.make_inner(df)
            except Exception as e:  # noqa: BLE001
                self.write_log(f"make_inner failed: {e}")
                return
        else:
            # 把最新 frame 注入 inner（约定 inner.frame 可写）
            try:
                self._inner.frame = df
            except AttributeError:
                pass

        i = len(df) - 1
        try:
            orders = list(self._inner.on_bar(i, df.iloc[i], int(self.pos)))
        except Exception as e:  # noqa: BLE001
            self.write_log(f"inner.on_bar error: {e}")
            return

        for o in orders:
            self._dispatch_order(o, bar)

    # ------- order translation -------
    def _dispatch_order(self, order: dict, bar: BarData) -> None:
        side = str(order.get("side", "")).lower()
        lots = int(order.get("lots", 0))
        if lots <= 0:
            return
        order_type = str(order.get("order_type", "market")).lower()
        stop = order_type == "stop"
        price = float(order.get("price", bar.close_price))

        if self.order_filter is not None:
            try:
                allowed = bool(self.order_filter(dict(order), self))
            except Exception as e:  # noqa: BLE001
                self.write_log(f"order_filter error: {e}")
                allowed = False
            if not allowed:
                return

        if side == "long":
            self.buy(price, lots, stop=stop)
            return
        if side == "short":
            self.short(price, lots, stop=stop)
            return
        if side == "flat":
            pos = int(self.pos)
            if pos > 0:
                self.sell(price, abs(pos), stop=stop)
            elif pos < 0:
                self.cover(price, abs(pos), stop=stop)


__all__ = ["LegacyCtaAdapter", "bars_to_df"]
