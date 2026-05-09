"""cta.live.pnl_tracker 单测。"""
from __future__ import annotations

import unittest
from datetime import date, datetime
from types import SimpleNamespace

from cta.live.pnl_tracker import DailyPnlTracker


def _trade(direction: str, offset: str, price: float, volume: float,
           dt: datetime | None = None,
           symbol: str = "rb888", exchange: str = "SHFE") -> SimpleNamespace:
    return SimpleNamespace(
        symbol=symbol,
        exchange=SimpleNamespace(value=exchange),
        direction=SimpleNamespace(value=direction),
        offset=SimpleNamespace(value=offset),
        price=price,
        volume=volume,
        datetime=dt or datetime(2024, 1, 2, 9, 30),
    )


class TestDailyPnlTracker(unittest.TestCase):
    def test_no_trades_zero(self) -> None:
        t = DailyPnlTracker(contract_size_resolver=lambda s: 10.0)
        self.assertEqual(t.get_pnl(), 0.0)

    def test_long_open_then_close_realizes_pnl(self) -> None:
        t = DailyPnlTracker(contract_size_resolver=lambda s: 10.0)
        t.on_trade(_trade("long",  "open",  100.0, 1))
        self.assertEqual(t.get_pnl(), 0.0)  # 仅开仓未实现
        t.on_trade(_trade("short", "close", 105.0, 1))
        # gross = (105 - 100) * 1 * 10 = 50
        self.assertAlmostEqual(t.get_pnl(), 50.0)

    def test_short_pnl_signs(self) -> None:
        t = DailyPnlTracker(contract_size_resolver=lambda s: 10.0)
        t.on_trade(_trade("short", "open",  100.0, 1))
        t.on_trade(_trade("long",  "close", 95.0, 1))
        # short: gross = (100 - 95) * 1 * 10 = 50
        self.assertAlmostEqual(t.get_pnl(), 50.0)

    def test_partial_close(self) -> None:
        t = DailyPnlTracker(contract_size_resolver=lambda s: 10.0)
        t.on_trade(_trade("long", "open",  100.0, 3))
        t.on_trade(_trade("short", "close", 110.0, 1))
        # 1 lot closed at 10 profit * 10 = 100
        self.assertAlmostEqual(t.get_pnl(), 100.0)
        t.on_trade(_trade("short", "close", 105.0, 2))
        # 2 lots closed at 5 profit * 10 = 100
        self.assertAlmostEqual(t.get_pnl(), 200.0)

    def test_multiple_open_then_close_fifo(self) -> None:
        t = DailyPnlTracker(contract_size_resolver=lambda s: 10.0)
        t.on_trade(_trade("long", "open", 100.0, 1))
        t.on_trade(_trade("long", "open", 110.0, 1))
        t.on_trade(_trade("short", "close", 115.0, 2))
        # FIFO: (115-100)*1*10 + (115-110)*1*10 = 150 + 50 = 200
        self.assertAlmostEqual(t.get_pnl(), 200.0)

    def test_commission_deducts_realized_pnl(self) -> None:
        t = DailyPnlTracker(
            contract_size_resolver=lambda s: 10.0,
            commission_resolver=lambda s, price, volume: 1.0 * volume,
        )
        t.on_trade(_trade("long",  "open",  100.0, 1))
        t.on_trade(_trade("short", "close", 105.0, 1))
        # gross = 50; commission entry+exit = 1+1 = 2
        self.assertAlmostEqual(t.get_pnl(), 48.0)

    def test_reset_for_new_day(self) -> None:
        t = DailyPnlTracker(contract_size_resolver=lambda s: 10.0)
        t.on_trade(_trade("long", "open", 100.0, 1, dt=datetime(2024, 1, 2, 9, 30)))
        t.on_trade(_trade("short", "close", 105.0, 1, dt=datetime(2024, 1, 2, 14, 30)))
        self.assertGreater(t.get_pnl(), 0)
        t.reset_for_new_day(date(2024, 1, 3))
        self.assertEqual(t.get_pnl(), 0.0)

    def test_get_pnl_callable_for_risk_filter(self) -> None:
        """DailyPnlTracker.get_pnl 必须是无参 callable，可直接传给 make_risk_filter。"""
        t = DailyPnlTracker()
        cb = t.get_pnl
        self.assertTrue(callable(cb))
        self.assertEqual(cb(), 0.0)


if __name__ == "__main__":
    unittest.main()
