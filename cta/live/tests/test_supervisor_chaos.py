"""P2-15: Supervisor 断线重连 chaos test.

模拟 gateway 在生命周期中：connected → disconnect → reconnect → fail → success.
验收（roadmap §2.3）：断线时不静默放过；连续失败到 cap 时 give_up；不丢单。
"""
from __future__ import annotations

import threading
import unittest
from dataclasses import dataclass, field
from typing import Any

from cta.live.supervisor import Supervisor


@dataclass
class _FakeGateway:
    connected: bool = True
    query_account_raises: bool = False

    def query_account(self) -> None:
        if self.query_account_raises:
            raise RuntimeError("gateway not connected")


@dataclass
class _FakeMainEngine:
    """模拟 vnpy MainEngine 的 get_gateway / connect 接口。"""
    gateway: _FakeGateway | None
    connect_calls: list[tuple[dict, str]] = field(default_factory=list)
    connect_failures_remaining: int = 0  # 前 N 次 connect 失败

    def get_gateway(self, name: str) -> _FakeGateway | None:
        return self.gateway

    def connect(self, setting: dict, gateway_name: str) -> None:
        self.connect_calls.append((dict(setting), str(gateway_name)))
        if self.connect_failures_remaining > 0:
            self.connect_failures_remaining -= 1
            raise ConnectionError("simulated connect failure")
        if self.gateway is not None:
            self.gateway.connected = True
            self.gateway.query_account_raises = False


class TestSupervisorChaos(unittest.TestCase):
    def test_step_ok_when_connected(self) -> None:
        gw = _FakeGateway(connected=True)
        eng = _FakeMainEngine(gateway=gw)
        s = Supervisor(main_engine=eng, gateway_name="CTP", connect_setting={"u": "x"})
        result = s.step()
        self.assertEqual(result["event"], "ok")
        self.assertEqual(eng.connect_calls, [])

    def test_step_reconnects_when_disconnected(self) -> None:
        gw = _FakeGateway(connected=False)
        eng = _FakeMainEngine(gateway=gw)
        s = Supervisor(main_engine=eng, gateway_name="CTP", connect_setting={"u": "x"})
        result = s.step()
        self.assertEqual(result["event"], "reconnect")
        self.assertEqual(len(eng.connect_calls), 1)
        self.assertEqual(result["count"], 1)

    def test_step_handles_repeated_failures_then_recovers(self) -> None:
        """前 3 次 connect 失败 → 第 4 次成功 → 4 次内总 supervisor.step 返回正确 event。"""
        gw = _FakeGateway(connected=False)
        eng = _FakeMainEngine(gateway=gw, connect_failures_remaining=3)
        s = Supervisor(main_engine=eng, gateway_name="CTP", connect_setting={"u": "x"})

        results = [s.step() for _ in range(4)]
        # 前 3 次 reconnect_failed
        for i in range(3):
            self.assertEqual(results[i]["event"], "reconnect_failed", f"step {i} event")
        # 第 4 次：connect 成功（failures_remaining=0），gw.connected=True → "reconnect"
        self.assertEqual(results[3]["event"], "reconnect")
        self.assertEqual(len(eng.connect_calls), 4)

    def test_step_give_up_after_max_reconnects(self) -> None:
        """模拟 connect 无限失败，超 max_reconnects=2 时 give_up。"""
        gw = _FakeGateway(connected=False)
        eng = _FakeMainEngine(gateway=gw, connect_failures_remaining=999)
        s = Supervisor(
            main_engine=eng, gateway_name="CTP",
            connect_setting={"u": "x"}, max_reconnects=2,
        )
        # 触发 2 次 reconnect 尝试
        r1 = s.step()
        r2 = s.step()
        # 第 3 次：达到 cap → give_up
        r3 = s.step()
        self.assertEqual(r1["event"], "reconnect_failed")
        self.assertEqual(r2["event"], "reconnect_failed")
        self.assertEqual(r3["event"], "give_up")
        self.assertEqual(r3["count"], 2)

    def test_step_event_when_gateway_missing(self) -> None:
        """gateway 未注册时报 gateway_missing，不调 connect。"""
        eng = _FakeMainEngine(gateway=None)
        s = Supervisor(main_engine=eng, gateway_name="CTP", connect_setting={"u": "x"})
        result = s.step()
        self.assertEqual(result["event"], "gateway_missing")
        self.assertEqual(eng.connect_calls, [])

    def test_query_account_probe_when_connected_attr_missing(self) -> None:
        """gateway 没有 connected 字段 → 走 query_account 探活。"""
        gw = _FakeGateway()
        # 删 connected 让逻辑走 fallback
        delattr(gw, "connected")
        # query_account 不抛 → 视为已连接
        gw.query_account_raises = False
        eng = _FakeMainEngine(gateway=gw)
        s = Supervisor(main_engine=eng, gateway_name="CTP", connect_setting={"u": "x"})
        result = s.step()
        self.assertEqual(result["event"], "ok")

    def test_loop_until_stop_event(self) -> None:
        """supervisor.loop 在 stop_event 触发前持续轮询；触发后立即退出。"""
        gw = _FakeGateway(connected=True)
        eng = _FakeMainEngine(gateway=gw)
        s = Supervisor(
            main_engine=eng, gateway_name="CTP",
            connect_setting={"u": "x"}, check_interval=0.01,
        )
        stop = threading.Event()

        def fire_stop() -> None:
            import time
            time.sleep(0.05)
            stop.set()

        t = threading.Thread(target=fire_stop)
        t.start()
        n_steps = s.loop(stop)
        t.join()
        self.assertGreater(n_steps, 0)


if __name__ == "__main__":
    unittest.main()
