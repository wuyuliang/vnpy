from __future__ import annotations

from vnpy_ctastrategy import (
    CtaTemplate,
    StopOrder,
    TickData,
    BarData,
    TradeData,
    OrderData,
    ArrayManager
)


class Ma20_60TrendStrategy(CtaTemplate):

    fast_window = 20
    slow_window = 60
    fixed_size = 1

    fast_ma = 0.0
    slow_ma = 0.0

    parameters = ["fast_window", "slow_window", "fixed_size"]
    variables = ["fast_ma", "slow_ma"]

    def __init__(self, cta_engine, strategy_name: str, vt_symbol: str, setting: dict):
        super().__init__(cta_engine, strategy_name, vt_symbol, setting)
        self.am = ArrayManager(200)

    def on_init(self) -> None:
        """
        初始化策略时加载历史K线。
        slow_window 至少 60，因此这里多加载一些。
        """
        self.write_log("策略初始化")
        self.load_bar(80)

    def on_start(self) -> None:
        self.write_log("策略启动")

    def on_stop(self) -> None:
        self.write_log("策略停止")

    def on_tick(self, tick: TickData) -> None:
        pass

    def on_bar(self, bar: BarData) -> None:
        """
        直接按日线bar计算。
        """
        self.cancel_all()

        am = self.am
        am.update_bar(bar)
        if not am.inited:
            return

        fast_ma_array = am.sma(self.fast_window, array=True)
        slow_ma_array = am.sma(self.slow_window, array=True)

        self.fast_ma = float(fast_ma_array[-1])
        self.slow_ma = float(slow_ma_array[-1])

        fast_ma_prev = float(fast_ma_array[-2])
        slow_ma_prev = float(slow_ma_array[-2])

        cross_over = (fast_ma_prev <= slow_ma_prev) and (self.fast_ma > self.slow_ma)
        cross_below = (fast_ma_prev >= slow_ma_prev) and (self.fast_ma < self.slow_ma)

        # 金叉：目标持多
        if cross_over:
            if self.pos < 0:
                self.cover(bar.close_price, abs(self.pos))
            if self.pos <= 0:
                self.buy(bar.close_price, self.fixed_size)

        # 死叉：目标持空
        elif cross_below:
            if self.pos > 0:
                self.sell(bar.close_price, abs(self.pos))
            if self.pos >= 0:
                self.short(bar.close_price, self.fixed_size)

        self.put_event()

    def on_order(self, order: OrderData) -> None:
        pass

    def on_trade(self, trade: TradeData) -> None:
        self.put_event()

    def on_stop_order(self, stop_order: StopOrder) -> None:
        pass
