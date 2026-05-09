"""cta/strategy/cta_adapter.py 单测（TDD）。

测试不依赖 vnpy_ctabacktester / vnpy_ctp，仅用 vnpy_ctastrategy（CtaTemplate）+ vnpy.trader.object（BarData）。
所有 ``cta_engine`` 调用走 FakeCtaEngine 拦截，便于断言订单流。
"""
from __future__ import annotations

import unittest
from datetime import datetime
from typing import Any

import pandas as pd

from vnpy.trader.constant import Direction, Exchange, Interval, Offset
from vnpy.trader.object import BarData

from cta.strategy.cta_adapter import LegacyCtaAdapter, bars_to_df


class FakeCtaEngine:
    """模拟 vnpy_ctastrategy 的 cta_engine 行为。"""

    def __init__(self, pricetick: float = 1.0, size: int = 10) -> None:
        self.orders: list[dict] = []
        self.cancels: list[Any] = []
        self.cancel_all_called: int = 0
        self.logs: list[str] = []
        self._pricetick = pricetick
        self._size = size

    def send_order(self, strategy, direction, offset, price, volume, stop, lock, net):
        self.orders.append(
            {
                "direction": direction,
                "offset": offset,
                "price": float(price),
                "volume": float(volume),
                "stop": bool(stop),
            }
        )
        # 模拟成交：触发 strategy.pos 变更
        delta = float(volume) if direction == Direction.LONG else -float(volume)
        if offset == Offset.OPEN:
            strategy.pos += delta
        elif offset == Offset.CLOSE:
            strategy.pos += delta
        return [f"id-{len(self.orders)}"]

    def cancel_order(self, strategy, vt_orderid):
        self.cancels.append(vt_orderid)

    def cancel_all(self, strategy):
        self.cancel_all_called += 1

    def write_log(self, msg, strategy):
        self.logs.append(msg)

    def get_engine_type(self):
        from vnpy_ctastrategy.base import EngineType
        return EngineType.BACKTESTING

    def get_pricetick(self, strategy):
        return self._pricetick

    def get_size(self, strategy):
        return self._size


class _DummyInner:
    """V1-style strategy: emit fixed scripted orders by bar index."""

    def __init__(self, frame: pd.DataFrame, scripted: list[list[dict]]) -> None:
        self.frame = frame
        self._scripted = scripted

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict]:
        if i < len(self._scripted):
            return list(self._scripted[i])
        return []


class _ScriptedAdapter(LegacyCtaAdapter):
    parameters: list[str] = []
    history_size: int = 50

    def prepare_frame(self, df: pd.DataFrame) -> pd.DataFrame:
        return df

    def make_inner(self, frame: pd.DataFrame):
        return _DummyInner(frame, scripted=self.scripted)


def _bar(t: int, close: float = 100.0) -> BarData:
    return BarData(
        gateway_name="TEST",
        symbol="X0",
        exchange=Exchange.SHFE,
        datetime=datetime(2024, 1, 2, 9, 0) + pd.Timedelta(minutes=t).to_pytimedelta(),
        interval=Interval.MINUTE,
        open_price=close - 0.3,
        high_price=close + 0.5,
        low_price=close - 0.5,
        close_price=close,
        volume=1000.0,
    )


class TestBarsToDf(unittest.TestCase):
    def test_columns_and_order(self) -> None:
        bars = [_bar(0, 100), _bar(1, 101), _bar(2, 102)]
        df = bars_to_df(bars)
        for c in ("datetime", "open", "high", "low", "close", "volume"):
            self.assertIn(c, df.columns)
        self.assertEqual(list(df["close"]), [100, 101, 102])

    def test_empty(self) -> None:
        df = bars_to_df([])
        self.assertEqual(len(df), 0)


class TestAdapterDispatch(unittest.TestCase):
    def _new(self, scripted: list[list[dict]], history_size: int = 50) -> tuple[_ScriptedAdapter, FakeCtaEngine]:
        eng = FakeCtaEngine()
        adapter = _ScriptedAdapter(eng, "test", "X0.SHFE", {})
        adapter.scripted = scripted
        adapter.history_size = history_size
        adapter.trading = True   # 模拟实盘开盘
        adapter.on_init()
        adapter.on_start()
        return adapter, eng

    def test_long_dispatched_as_buy(self) -> None:
        adapter, eng = self._new(
            scripted=[
                [],  # i=0
                [{"side": "long", "lots": 2, "order_type": "market"}],  # i=1
            ]
        )
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        self.assertEqual(len(eng.orders), 1)
        self.assertEqual(eng.orders[0]["direction"], Direction.LONG)
        self.assertEqual(eng.orders[0]["offset"], Offset.OPEN)
        self.assertEqual(eng.orders[0]["volume"], 2.0)

    def test_short_dispatched(self) -> None:
        adapter, eng = self._new(
            scripted=[[], [{"side": "short", "lots": 1, "order_type": "market"}]]
        )
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        self.assertEqual(len(eng.orders), 1)
        self.assertEqual(eng.orders[0]["direction"], Direction.SHORT)
        self.assertEqual(eng.orders[0]["offset"], Offset.OPEN)

    def test_stop_order_uses_stop_flag(self) -> None:
        adapter, eng = self._new(
            scripted=[
                [],
                [{"side": "long", "lots": 1, "order_type": "stop", "price": 110.0}],
            ]
        )
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        self.assertEqual(len(eng.orders), 1)
        self.assertTrue(eng.orders[0]["stop"])
        self.assertEqual(eng.orders[0]["price"], 110.0)

    def test_flat_long_position_uses_sell(self) -> None:
        adapter, eng = self._new(
            scripted=[
                [],
                [{"side": "long", "lots": 1, "order_type": "market"}],
                [{"side": "flat", "lots": 1, "order_type": "market"}],
            ]
        )
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        adapter.on_bar(_bar(2))
        # 第二条订单应为 SHORT/CLOSE = sell()
        self.assertGreaterEqual(len(eng.orders), 2)
        last = eng.orders[-1]
        self.assertEqual(last["direction"], Direction.SHORT)
        self.assertEqual(last["offset"], Offset.CLOSE)

    def test_flat_short_position_uses_cover(self) -> None:
        adapter, eng = self._new(
            scripted=[
                [],
                [{"side": "short", "lots": 1, "order_type": "market"}],
                [{"side": "flat", "lots": 1, "order_type": "market"}],
            ]
        )
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        adapter.on_bar(_bar(2))
        last = eng.orders[-1]
        self.assertEqual(last["direction"], Direction.LONG)
        self.assertEqual(last["offset"], Offset.CLOSE)

    def test_zero_lots_skipped(self) -> None:
        adapter, eng = self._new(
            scripted=[[], [{"side": "long", "lots": 0, "order_type": "market"}]]
        )
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        self.assertEqual(len(eng.orders), 0)

    def test_inner_exception_does_not_propagate(self) -> None:
        class _BadInner:
            def __init__(self, frame): self.frame = frame
            def on_bar(self, i, bar, pos): raise RuntimeError("boom")

        class _BadAdapter(LegacyCtaAdapter):
            history_size = 50

            def prepare_frame(self, df): return df
            def make_inner(self, frame): return _BadInner(frame)

        eng = FakeCtaEngine()
        a = _BadAdapter(eng, "t", "X0.SHFE", {})
        a.trading = True
        a.on_init()
        a.on_bar(_bar(0))
        a.on_bar(_bar(1))   # 触发 inner.on_bar，抛错应被吞
        self.assertEqual(len(eng.orders), 0)
        self.assertTrue(any("inner.on_bar" in m for m in eng.logs))

    def test_history_buffer_truncated(self) -> None:
        adapter, eng = self._new(scripted=[[]] * 200, history_size=10)
        for t in range(20):
            adapter.on_bar(_bar(t))
        # buffer 不应超过 history_size
        self.assertLessEqual(len(adapter._buffer), 10)

    def test_not_trading_skips_send(self) -> None:
        # trading=False 时 buy/sell 不会发出真实订单（CtaTemplate.send_order 实现）
        adapter, eng = self._new(
            scripted=[[], [{"side": "long", "lots": 1, "order_type": "market"}]]
        )
        adapter.trading = False  # 关闭 trading
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        self.assertEqual(len(eng.orders), 0)


class TestAdapterOrderFilter(unittest.TestCase):
    """验证 order_filter pre-trade hook 与 RiskGuard 集成。"""

    def _new(self, scripted, order_filter=None):
        eng = FakeCtaEngine()
        adapter = _ScriptedAdapter(eng, "test", "X0.SHFE", {})
        adapter.scripted = scripted
        if order_filter is not None:
            adapter.order_filter = order_filter
        adapter.trading = True
        adapter.on_init()
        return adapter, eng

    def test_filter_allows_passthrough(self) -> None:
        adapter, eng = self._new(
            scripted=[[], [{"side": "long", "lots": 1, "order_type": "market"}]],
            order_filter=lambda order, ad: True,
        )
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        self.assertEqual(len(eng.orders), 1)

    def test_filter_blocks_send(self) -> None:
        adapter, eng = self._new(
            scripted=[[], [{"side": "long", "lots": 1, "order_type": "market"}]],
            order_filter=lambda order, ad: False,
        )
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        self.assertEqual(len(eng.orders), 0)

    def test_filter_exception_blocks_send(self) -> None:
        def bad_filter(order, ad):
            raise RuntimeError("boom")
        adapter, eng = self._new(
            scripted=[[], [{"side": "long", "lots": 1, "order_type": "market"}]],
            order_filter=bad_filter,
        )
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        self.assertEqual(len(eng.orders), 0)
        self.assertTrue(any("order_filter" in m for m in eng.logs))

    def test_make_risk_filter_blocks_oversize(self) -> None:
        from cta.live.risk import MaxOrderSize, RiskGuard, make_risk_filter

        guard = RiskGuard(rules=[MaxOrderSize(limits={"X0.SHFE": 1})])
        adapter, eng = self._new(
            scripted=[
                [],
                [{"side": "long", "lots": 5, "order_type": "market"}],  # 超 limit
            ],
            order_filter=make_risk_filter(guard),
        )
        adapter.on_bar(_bar(0))
        adapter.on_bar(_bar(1))
        self.assertEqual(len(eng.orders), 0)


class TestAdapterParameters(unittest.TestCase):
    def test_setting_applied(self) -> None:
        class _CustomAdapter(LegacyCtaAdapter):
            parameters = ["lookback", "alpha"]
            lookback: int = 20
            alpha: float = 0.5
            history_size = 50

            def prepare_frame(self, df): return df
            def make_inner(self, frame): return _DummyInner(frame, [])

        eng = FakeCtaEngine()
        a = _CustomAdapter(eng, "t", "X0.SHFE", {"lookback": 30, "alpha": 0.8})
        self.assertEqual(a.lookback, 30)
        self.assertAlmostEqual(a.alpha, 0.8)


if __name__ == "__main__":
    unittest.main()
