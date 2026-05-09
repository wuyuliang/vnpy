"""三个 baseline CtaTemplate 子类（Donchian/ATR/BreakoutPullback）单测。"""
from __future__ import annotations

import unittest
from datetime import datetime, timedelta

import numpy as np

from vnpy.trader.constant import Exchange, Interval
from vnpy.trader.object import BarData

from cta.strategy.cta_baseline import (
    AtrBreakoutCta,
    BreakoutPullbackCta,
    DonchianCta,
)
from cta.strategy.tests.test_cta_adapter import FakeCtaEngine


def _bar(t: int, o: float, h: float, l: float, c: float, v: float = 1500.0) -> BarData:
    return BarData(
        gateway_name="TEST",
        symbol="X0",
        exchange=Exchange.SHFE,
        datetime=datetime(2024, 1, 2, 9, 0) + timedelta(days=t),
        interval=Interval.DAILY,
        open_price=o,
        high_price=h,
        low_price=l,
        close_price=c,
        volume=v,
    )


def _trend_up_bars(n_pre: int = 80, n_break: int = 30) -> list[BarData]:
    """前 n_pre 根震荡，后 n_break 根明显上涨突破。
    n_pre 至少 ~70 以让 Donchian 55 / ATR_MA20 + ATR14 等指标稳定。
    """
    rng = np.random.default_rng(0)
    base = 100.0
    bars: list[BarData] = []
    for i in range(n_pre):
        c = base + rng.uniform(-0.6, 0.6)
        o = c + rng.uniform(-0.3, 0.3)
        h = max(o, c) + rng.uniform(0, 0.4)
        l = min(o, c) - rng.uniform(0, 0.4)
        bars.append(_bar(i, o, h, l, c, v=1500.0))
    for j in range(n_break):
        c = base + 2.5 + j * 1.5
        o = c - 0.5
        h = c + 1.0
        l = o - 0.2
        bars.append(_bar(n_pre + j, o, h, l, c, v=4000.0))
    return bars


class _CommonChecks:
    cls = None

    def _new(self, **setting):
        eng = FakeCtaEngine()
        a = self.cls(eng, "test", "X0.SHFE", setting)
        a.trading = True
        a.on_init()
        a.on_start()
        return a, eng

    def test_subclass_of_cta_template(self) -> None:
        from vnpy_ctastrategy import CtaTemplate
        self.assertTrue(issubclass(self.cls, CtaTemplate))

    def test_smoke_runs_without_error(self) -> None:
        a, eng = self._new()
        for bar in _trend_up_bars(n_pre=80, n_break=20):
            a.on_bar(bar)
        bad = [m for m in eng.logs if "error" in m.lower() or "failed" in m.lower()]
        self.assertEqual(bad, [], f"unexpected errors: {bad}")

    def test_setting_overrides_trade_side_mode(self) -> None:
        a, _ = self._new(trade_side_mode="long")
        self.assertEqual(a.trade_side_mode, "long")


class TestDonchianCta(unittest.TestCase, _CommonChecks):
    cls = DonchianCta

    def test_emits_long_breakout(self) -> None:
        from vnpy.trader.constant import Direction, Offset
        a, eng = self._new(trade_side_mode="long")
        for bar in _trend_up_bars(n_pre=80, n_break=30):
            a.on_bar(bar)
        opens = [o for o in eng.orders if o["offset"] == Offset.OPEN]
        long_opens = [o for o in opens if o["direction"] == Direction.LONG]
        self.assertGreaterEqual(len(long_opens), 1)


class TestAtrBreakoutCta(unittest.TestCase, _CommonChecks):
    cls = AtrBreakoutCta


class TestBreakoutPullbackCta(unittest.TestCase, _CommonChecks):
    cls = BreakoutPullbackCta

    def test_extra_parameters_listed(self) -> None:
        for p in ("max_holding_bars", "trailing_stop_atr_mult", "initial_stop_atr_mult"):
            self.assertIn(p, BreakoutPullbackCta.parameters)


if __name__ == "__main__":
    unittest.main()
