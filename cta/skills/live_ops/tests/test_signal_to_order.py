"""signal_to_order.py tests."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.skills.live_ops.signal_to_order import Signal, orderize


class TestSignalToOrder(unittest.TestCase):
    def test_open_order(self) -> None:
        sig = Signal(
            ts=pd.Timestamp("2024-01-01"),
            symbol="rb888.SHFE",
            side="long",
            lots=2,
            reason="test_open",
            stop_price=99.0,
        )
        orders = orderize(
            sig=sig,
            existing_positions={},
            meta={"exchange_rule": "shfe"},
            active_contract="rb2405",
        )
        self.assertGreaterEqual(len(orders), 1)
        self.assertEqual(orders[0].open_close, "open")

    def test_close_order(self) -> None:
        sig = Signal(
            ts=pd.Timestamp("2024-01-01"),
            symbol="rb888.SHFE",
            side="flat",
            lots=1,
            reason="test_close",
        )
        positions = {"rb888.SHFE": {"side": "long", "today_lots": 1, "yesterday_lots": 0}}
        orders = orderize(
            sig=sig,
            existing_positions=positions,
            meta={"exchange_rule": "shfe"},
            active_contract="rb2405",
        )
        self.assertEqual(len(orders), 1)
        self.assertIn(orders[0].open_close, {"close_today", "close_yesterday", "close"})


    def test_zero_lots_logs_warning(self) -> None:
        """lots<=0 不得静默丢弃，需 log.warning 以便实盘审计。"""
        import logging
        sig = Signal(
            ts=pd.Timestamp("2024-01-01"),
            symbol="rb888.SHFE",
            side="long",
            lots=0,
            reason="test_zero",
        )
        with self.assertLogs("cta.skills.live_ops.signal_to_order",
                             level=logging.WARNING) as cm:
            orders = orderize(sig, {}, {"exchange_rule": "shfe"}, "rb2405")
        self.assertEqual(orders, [])
        self.assertTrue(any("lots" in m.lower() for m in cm.output))


if __name__ == "__main__":
    unittest.main()

