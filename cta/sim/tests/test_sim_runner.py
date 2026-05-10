"""cta.sim.sim_runner 单测。

不实际启动 vnpy MainEngine / CtpGateway，通过 ``main_engine_factory`` 注入 fake 验证调用流程。
"""
from __future__ import annotations

import unittest
from typing import Any

from cta.sim.sim_runner import SIMNOW_DEFAULT, SimnowSetting, SimRunConfig, run_sim


class _FakeStrategy:
    """模拟一个被 add_strategy 创建的实例，提供 vnpy 标准接口。"""

    def __init__(self, name: str, vt_symbol: str) -> None:
        self.strategy_name = name
        self.vt_symbol = vt_symbol
        self.order_filter = None
        self.trade_recorder = None
        self.pnl_tracker = None
        self.load_bar_calls: list[tuple] = []

    def load_bar(self, days: int, interval: Any = None, callback: Any = None,
                 use_database: bool = False) -> None:
        self.load_bar_calls.append((days, interval, use_database))


class FakeCtaEngine:
    """Fake，模拟 vnpy_ctastrategy.CtaEngine 真实接口：
    - ``add_strategy`` 第一参数是**字符串类名**，从 ``self.classes`` 查类
    - 兼容直接传 class object（向后兼容老测试）
    """

    def __init__(self) -> None:
        self.strategies: dict[str, _FakeStrategy] = {}
        self.classes: dict[str, type] = {}
        self.added: list[tuple[type, str, str, dict]] = []
        self.inited: list[str] = []
        self.started: list[str] = []

    def add_strategy(self, class_or_name: Any, name: str, vt_symbol: str, setting: dict) -> None:
        if isinstance(class_or_name, str):
            cls = self.classes.get(class_or_name)
            if cls is None:
                raise KeyError(f"strategy class not registered: {class_or_name}")
        else:
            cls = class_or_name
        self.added.append((cls, name, vt_symbol, dict(setting)))
        self.strategies[name] = _FakeStrategy(name, vt_symbol)

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
        self.assertEqual(me.cta_engine.added, [
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


class TestVnpyCompatibility(unittest.TestCase):
    """sim_runner 必须按 vnpy 真实规范工作：先注册类，再用字符串名 add_strategy。"""

    def test_registers_class_in_classes_dict(self) -> None:
        from cta.sim.sim_runner import SimRunConfig, SimnowSetting, run_sim
        me = FakeMainEngine()
        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb",
            vt_symbol="rb888.SHFE",
            setting={},
        )
        run_sim(cfg, SimnowSetting(userid="u", password="p"),
                main_engine_factory=lambda: me)
        # vnpy 规范：cta_engine.classes[ClassName] = StrategyClass
        self.assertIn(_DummyStrategyClass.__name__, me.cta_engine.classes)
        self.assertIs(me.cta_engine.classes[_DummyStrategyClass.__name__], _DummyStrategyClass)

    def test_strategy_added_under_class_name_string(self) -> None:
        """在 vnpy 真实环境下若不先注册，``self.classes.get(class_object) → None`` 静默失败；
        sim_runner 必须用字符串 class_name 调用 add_strategy。"""
        from cta.sim.sim_runner import SimRunConfig, SimnowSetting, run_sim

        # 不预先注册 → 真实 vnpy 会拒绝；sim_runner 须自己注册
        me = FakeMainEngine()
        # 模拟真实 vnpy：classes 为空时不接受 class object，只接受字符串
        original_add = me.cta_engine.add_strategy

        def strict_add(class_or_name, name, vt, setting):
            if not isinstance(class_or_name, str):
                raise TypeError("vnpy add_strategy expects str class_name")
            return original_add(class_or_name, name, vt, setting)

        me.cta_engine.add_strategy = strict_add  # type: ignore[assignment]

        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb",
            vt_symbol="rb888.SHFE",
            setting={},
        )
        # 应当不抛错 — sim_runner 必须先注册类、再传字符串
        run_sim(cfg, SimnowSetting(userid="u", password="p"),
                main_engine_factory=lambda: me)
        self.assertIn("rb", me.cta_engine.strategies)


class TestServeForever(unittest.TestCase):
    """``serve_forever`` 阻塞主线程直到收到外部信号；停止时 close main_engine。"""

    def test_blocks_until_stop_event(self) -> None:
        import threading
        from cta.sim.sim_runner import serve_forever
        me = FakeMainEngine()
        stop = threading.Event()

        def _set_stop():
            import time
            time.sleep(0.05)
            stop.set()

        threading.Thread(target=_set_stop).start()
        serve_forever(me, stop_event=stop, install_signal_handlers=False)
        self.assertTrue(me.closed)

    def test_close_called_even_on_exception(self) -> None:
        import threading
        from cta.sim.sim_runner import serve_forever

        class _RaisingEngine(FakeMainEngine):
            def close(self):
                self.closed = True
                raise RuntimeError("close fail")

        me = _RaisingEngine()
        stop = threading.Event()
        stop.set()
        # 不应抛出，函数内部应吞 close 异常
        serve_forever(me, stop_event=stop, install_signal_handlers=False)
        self.assertTrue(me.closed)


class TestSimRunnerIntegration(unittest.TestCase):
    """端到端集成：warmup + 风控 + 记录器 + PnL tracker 一并挂上。"""

    def _simnow(self) -> SimnowSetting:
        return SimnowSetting(userid="u", password="p")

    def _new_engine(self) -> FakeMainEngine:
        return FakeMainEngine()

    def test_warmup_calls_load_bar(self) -> None:
        from cta.sim.sim_runner import SimRunConfig, run_sim
        me = self._new_engine()
        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb",
            vt_symbol="rb888.SHFE",
            setting={},
            warmup_days=5,
            warmup_interval="1m",
        )
        run_sim(cfg, self._simnow(), main_engine_factory=lambda: me)
        s = me.cta_engine.strategies["rb"]
        self.assertEqual(len(s.load_bar_calls), 1)
        self.assertEqual(s.load_bar_calls[0][0], 5)

    def test_no_warmup_when_zero(self) -> None:
        from cta.sim.sim_runner import SimRunConfig, run_sim
        me = self._new_engine()
        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb",
            vt_symbol="rb888.SHFE",
            setting={},
            warmup_days=0,
        )
        run_sim(cfg, self._simnow(), main_engine_factory=lambda: me)
        s = me.cta_engine.strategies["rb"]
        self.assertEqual(s.load_bar_calls, [])

    def test_risk_guard_attached_as_order_filter(self) -> None:
        from cta.live.risk import MaxOrderSize, RiskGuard
        from cta.sim.sim_runner import SimRunConfig, run_sim
        me = self._new_engine()
        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb",
            vt_symbol="rb888.SHFE",
            setting={},
            risk_guard=RiskGuard([MaxOrderSize({"rb888.SHFE": 1})]),
            capital=1_000_000.0,
        )
        run_sim(cfg, self._simnow(), main_engine_factory=lambda: me)
        s = me.cta_engine.strategies["rb"]
        self.assertIsNotNone(s.order_filter)
        self.assertTrue(callable(s.order_filter))
        # PnL tracker 应自动挂上以供风控 daily_pnl_provider 使用
        self.assertIsNotNone(s.pnl_tracker)

    def test_kill_switch_blocks_orders(self) -> None:
        from cta.live.kill_switch import KillSwitch
        from cta.live.risk import RiskGuard
        from cta.sim.sim_runner import SimRunConfig, run_sim
        me = self._new_engine()
        ks = KillSwitch()
        ks.activate("manual")
        cfg = SimRunConfig(
            strategy_class=_DummyStrategyClass,
            strategy_name="rb",
            vt_symbol="rb888.SHFE",
            setting={},
            risk_guard=RiskGuard([]),
            kill_switch=ks,
        )
        run_sim(cfg, self._simnow(), main_engine_factory=lambda: me)
        s = me.cta_engine.strategies["rb"]
        # order_filter 被装上并应直接拦截
        order = {"side": "long", "lots": 1, "order_type": "market", "price": 100.0}
        self.assertFalse(s.order_filter(order, s))

    def test_trade_recorder_attached_when_dir_given(self) -> None:
        import tempfile
        from cta.sim.sim_runner import SimRunConfig, run_sim
        me = self._new_engine()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = SimRunConfig(
                strategy_class=_DummyStrategyClass,
                strategy_name="rb",
                vt_symbol="rb888.SHFE",
                setting={},
                trade_recorder_dir=tmp,
            )
            run_sim(cfg, self._simnow(), main_engine_factory=lambda: me)
            s = me.cta_engine.strategies["rb"]
            self.assertIsNotNone(s.trade_recorder)


if __name__ == "__main__":
    unittest.main()
