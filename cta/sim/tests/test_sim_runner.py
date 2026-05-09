"""cta.sim.sim_runner 单测。

不实际启动 vnpy MainEngine / CtpGateway，通过 ``main_engine_factory`` 注入 fake 验证调用流程。
"""
from __future__ import annotations

import unittest
from typing import Any

from cta.sim.sim_runner import SIMNOW_DEFAULT, SimnowSetting, SimRunConfig, run_sim


class FakeCtaEngine:
    def __init__(self) -> None:
        self.strategies: list[tuple[type, str, str, dict]] = []
        self.inited: list[str] = []
        self.started: list[str] = []

    def add_strategy(self, cls: type, name: str, vt_symbol: str, setting: dict) -> None:
        self.strategies.append((cls, name, vt_symbol, dict(setting)))

    def init_strategy(self, name: str) -> None:
        self.inited.append(name)

    def start_strategy(self, name: str) -> None:
        self.started.append(name)

    def init_engine(self) -> None:  # vnpy_ctastrategy.CtaEngine 的真实方法
        pass


class FakeMainEngine:
    def __init__(self) -> None:
        self.gateways: list[Any] = []
        self.apps: list[Any] = []
        self.connections: list[tuple[dict, str]] = []
        self.cta_engine = FakeCtaEngine()
        self._engines = {"CtaStrategy": self.cta_engine}
        self.closed = False

    def add_gateway(self, gw_class) -> None:
        self.gateways.append(gw_class)

    def add_app(self, app_class) -> None:
        self.apps.append(app_class)

    def connect(self, setting: dict, gateway_name: str) -> None:
        self.connections.append((dict(setting), gateway_name))

    def get_engine(self, name: str):
        return self._engines.get(name)

    def close(self) -> None:
        self.closed = True


class _DummyStrategyClass:
    pass


class TestRunSim(unittest.TestCase):
    def _new_simnow(self) -> SimnowSetting:
        return SimnowSetting(userid="000001", password="abc", brokerid="9999")

    def test_connects_to_simnow(self) -> None:
        me = FakeMainEngine()
        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="dummy_rb",
            vt_symbol="rb888.SHFE",
            setting={"trade_side_mode": "long"},
        )
        run_sim(cfg, self._new_simnow(), main_engine_factory=lambda: me)
        self.assertEqual(len(me.connections), 1)
        setting, gw = me.connections[0]
        self.assertEqual(gw, "CTP")
        self.assertIn("用户名", setting)
        self.assertEqual(setting["用户名"], "000001")
        # 默认地址应使用 SIMNOW_DEFAULT 中的服务器
        self.assertIn(SIMNOW_DEFAULT["td_address"], setting.values())

    def test_adds_inits_and_starts_strategy(self) -> None:
        me = FakeMainEngine()
        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="dummy_rb",
            vt_symbol="rb888.SHFE",
            setting={"lookback": 5},
        )
        run_sim(cfg, self._new_simnow(), main_engine_factory=lambda: me)
        self.assertEqual(me.cta_engine.strategies, [
            (_DummyStrategyClass, "dummy_rb", "rb888.SHFE", {"lookback": 5})
        ])
        self.assertEqual(me.cta_engine.inited, ["dummy_rb"])
        self.assertEqual(me.cta_engine.started, ["dummy_rb"])

    def test_returns_main_engine(self) -> None:
        me = FakeMainEngine()
        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="t",
            vt_symbol="rb888.SHFE",
            setting={},
        )
        ret = run_sim(cfg, self._new_simnow(), main_engine_factory=lambda: me)
        self.assertIs(ret, me)

    def test_custom_addresses_propagate(self) -> None:
        me = FakeMainEngine()
        sim = SimnowSetting(
            userid="u", password="p",
            td_address="tcp://1.2.3.4:5",
            md_address="tcp://6.7.8.9:0",
        )
        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass, strategy_name="t",
            vt_symbol="rb888.SHFE", setting={},
        )
        run_sim(cfg, sim, main_engine_factory=lambda: me)
        setting, _ = me.connections[0]
        self.assertEqual(setting["交易服务器"], "tcp://1.2.3.4:5")
        self.assertEqual(setting["行情服务器"], "tcp://6.7.8.9:0")


class TestSimnowDefaults(unittest.TestCase):
    def test_default_addresses_present(self) -> None:
        for k in ("brokerid", "auth_code", "appid", "td_address", "md_address"):
            self.assertIn(k, SIMNOW_DEFAULT)


if __name__ == "__main__":
    unittest.main()
