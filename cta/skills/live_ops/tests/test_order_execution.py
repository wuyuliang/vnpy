"""order_execution.py tests."""
from __future__ import annotations

import unittest

from cta.skills.live_ops.order_execution import (
    ExecConfig,
    kill_switch,
    on_order_event,
    reconcile,
    submit_order,
)


class _MockGateway:
    def __init__(self) -> None:
        self.orders = []

    def send_order(self, order: dict) -> str:
        oid = f"OID{len(self.orders)+1}"
        self.orders.append((oid, order))
        return oid


class TestOrderExecution(unittest.TestCase):
    def test_submit_order(self) -> None:
        gw = _MockGateway()
        oid = submit_order({"symbol": "rb", "lots": 1}, gw, ExecConfig())
        self.assertTrue(oid.startswith("OID"))

    def test_on_event_and_reconcile(self) -> None:
        state = {"filled": 0}
        on_order_event({"type": "trade", "lots": 2}, state)
        self.assertEqual(state["filled"], 2)
        diff = reconcile({"rb": 1}, {"rb": 2})
        self.assertEqual(len(diff), 1)

    def test_kill_switch(self) -> None:
        out = kill_switch("unit_test")
        self.assertEqual(out["enabled"], True)


if __name__ == "__main__":
    unittest.main()

