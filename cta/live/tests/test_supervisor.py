"""cta.live.supervisor 单测。"""
from __future__ import annotations

import threading
import time
import unittest

from cta.live.supervisor import Supervisor


class FakeGateway:
    def __init__(self, name: str = "CTP", connected: bool = True) -> None:
        self.gateway_name = name
        self.connected = connected
        self.connect_calls = 0

    def query_account(self) -> None:  # vnpy 风格
        if not self.connected:
            raise ConnectionError("disconnected")


class FakeMainEngine:
    def __init__(self, gateway: FakeGateway) -> None:
        self.gateway = gateway
        self.connect_settings: list[tuple[dict, str]] = []

    def get_gateway(self, name: str):
        return self.gateway if self.gateway.gateway_name == name else None

    def connect(self, setting: dict, gateway_name: str) -> None:
        self.connect_settings.append((dict(setting), gateway_name))
        # 模拟成功重连
        self.gateway.connected = True
        self.gateway.connect_calls += 1


class TestSupervisor(unittest.TestCase):
    def test_step_ok_when_connected(self) -> None:
        gw = FakeGateway(connected=True)
        me = FakeMainEngine(gw)
        sup = Supervisor(me, gateway_name="CTP", connect_setting={"用户名": "u"})
        ev = sup.step()
        self.assertEqual(ev["event"], "ok")
        self.assertEqual(gw.connect_calls, 0)

    def test_step_reconnects_when_disconnected(self) -> None:
        gw = FakeGateway(connected=False)
        me = FakeMainEngine(gw)
        sup = Supervisor(me, gateway_name="CTP", connect_setting={"用户名": "u"})
        ev = sup.step()
        self.assertEqual(ev["event"], "reconnect")
        self.assertEqual(gw.connect_calls, 1)

    def test_max_reconnects_cap(self) -> None:
        gw = FakeGateway(connected=False)

        class _AlwaysDownEngine(FakeMainEngine):
            def connect(self, setting, gateway_name):
                self.connect_settings.append((dict(setting), gateway_name))
                # 模拟一直连不上：connected 保持 False
                self.gateway.connect_calls += 1

        me = _AlwaysDownEngine(gw)
        sup = Supervisor(me, gateway_name="CTP", connect_setting={"用户名": "u"}, max_reconnects=3)
        events = [sup.step() for _ in range(5)]
        # 前 3 次 reconnect，第 4-5 次 give_up
        types = [e["event"] for e in events]
        self.assertEqual(types.count("reconnect"), 3)
        self.assertEqual(types.count("give_up"), 2)

    def test_no_gateway_returns_missing_event(self) -> None:
        gw = FakeGateway(name="OTHER")
        me = FakeMainEngine(gw)
        sup = Supervisor(me, gateway_name="CTP", connect_setting={})
        ev = sup.step()
        self.assertEqual(ev["event"], "gateway_missing")

    def test_loop_respects_stop_event(self) -> None:
        gw = FakeGateway(connected=True)
        me = FakeMainEngine(gw)
        sup = Supervisor(me, gateway_name="CTP", connect_setting={}, check_interval=0.05)
        stop = threading.Event()

        def stopper():
            time.sleep(0.18)
            stop.set()

        t = threading.Thread(target=stopper)
        t.start()
        n = sup.loop(stop_event=stop)
        t.join()
        self.assertGreater(n, 0)


if __name__ == "__main__":
    unittest.main()
