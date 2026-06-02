"""P1-7 回归：rotation 主循环下单 wire（sim/live 共用）。"""
from __future__ import annotations

import unittest
from dataclasses import dataclass
from typing import Any

import pandas as pd

from cta.portfolio_logic.cross_sectional_rotation_executor import RotationOrderIntent
from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.sim.adapters.rotation_order_wire import (
    RotationOrderWireConfig,
    dispatch_rotation_intents,
    wire_rotation_main_loop,
)
from cta.sim.sim_runner import SimRunConfig, SimnowSetting, run_sim


@dataclass
class _FakeBar:
    datetime: pd.Timestamp
    close_price: float


def _intent(
    symbol: str,
    side: str = "long",
    target_notional: float = 200_000.0,
    candidate: dict[str, Any] | None = None,
) -> RotationOrderIntent:
    return RotationOrderIntent(
        symbol=symbol,
        exchange="CFFEX",
        side=side,
        signal_type="cross_sectional_momentum",
        signal_datetime=pd.Timestamp("2026-01-05 15:00:00"),
        entry_datetime=pd.Timestamp("2026-01-06 09:31:00"),
        planned_exit_datetime=pd.Timestamp("2026-01-13 15:00:00"),
        stop_price=3950.0,
        target_weight=0.2,
        target_notional=target_notional,
        candidate=dict(candidate or {"entry_price_hint": 4000.0}),
    )


class _FakeStepper:
    def __init__(self, intents: list[RotationOrderIntent]) -> None:
        self._intents = intents
        self.calls: list[pd.Timestamp] = []

    def step(self, t: pd.Timestamp, portfolio_state: PortfolioState) -> list[RotationOrderIntent]:  # noqa: ARG002
        self.calls.append(pd.Timestamp(t))
        return list(self._intents)


class _FakeStrategy:
    def __init__(self, vt_symbol: str = "IF0.CFFEX") -> None:
        self.vt_symbol = vt_symbol
        self.on_bar_calls = 0
        self.sent_orders: list[dict[str, Any]] = []

    def on_bar(self, bar: _FakeBar) -> None:  # noqa: ARG002
        self.on_bar_calls += 1

    def buy(self, price: float, volume: int, stop: bool = False) -> None:
        self.sent_orders.append({"side": "long", "price": price, "volume": int(volume), "stop": bool(stop)})

    def short(self, price: float, volume: int, stop: bool = False) -> None:
        self.sent_orders.append({"side": "short", "price": price, "volume": int(volume), "stop": bool(stop)})


class _DispatchStrategy(_FakeStrategy):
    def __init__(self, vt_symbol: str = "IF0.CFFEX") -> None:
        super().__init__(vt_symbol=vt_symbol)
        self.dispatched: list[tuple[dict[str, Any], _FakeBar]] = []

    def _dispatch_order(self, order: dict[str, Any], bar: _FakeBar) -> None:
        self.dispatched.append((dict(order), bar))


class TestRotationMainLoopWire(unittest.TestCase):
    def test_wire_calls_stepper_and_dispatches_only_matching_symbol(self) -> None:
        strategy = _FakeStrategy(vt_symbol="IF0.CFFEX")
        stepper = _FakeStepper([_intent("IF0", "long"), _intent("IH0", "short")])
        state = PortfolioState(equity=1_000_000.0)
        wire_rotation_main_loop(
            strategy,
            stepper=stepper,
            portfolio_state=state,
            cfg=RotationOrderWireConfig(default_contract_size=300.0),
        )

        bar = _FakeBar(datetime=pd.Timestamp("2026-01-05 09:31:00"), close_price=4000.0)
        strategy.on_bar(bar)

        self.assertEqual(strategy.on_bar_calls, 1)
        self.assertEqual(stepper.calls, [pd.Timestamp("2026-01-05 09:31:00")])
        self.assertEqual(len(strategy.sent_orders), 1)
        self.assertEqual(strategy.sent_orders[0]["side"], "long")

    def test_wire_prefers_strategy_dispatch_order_when_available(self) -> None:
        strategy = _DispatchStrategy(vt_symbol="IF0.CFFEX")
        stepper = _FakeStepper([_intent("IF0", "short")])
        state = PortfolioState(equity=1_000_000.0)
        wire_rotation_main_loop(
            strategy,
            stepper=stepper,
            portfolio_state=state,
            cfg=RotationOrderWireConfig(default_contract_size=300.0),
        )
        bar = _FakeBar(datetime=pd.Timestamp("2026-01-05 09:31:00"), close_price=4000.0)
        strategy.on_bar(bar)

        self.assertEqual(len(strategy.dispatched), 1)
        order, _ = strategy.dispatched[0]
        self.assertEqual(order["side"], "short")
        self.assertEqual(order["signal_type"], "cross_sectional_momentum")
        self.assertEqual(order["rotation_symbol"], "IF0")

    def test_dispatch_logs_drop_reason_for_symbol_mismatch(self) -> None:
        strategy = _FakeStrategy(vt_symbol="IF0.CFFEX")
        bar = _FakeBar(datetime=pd.Timestamp("2026-01-05 09:31:00"), close_price=4000.0)
        with self.assertLogs("cta.sim.adapters.rotation_order_wire", level="DEBUG") as cm:
            out = dispatch_rotation_intents(
                strategy,
                intents=[_intent("IH0", "long")],
                bar=bar,
                cfg=RotationOrderWireConfig(only_trade_matching_vt_symbol=True, default_contract_size=300.0),
            )
        self.assertEqual(out, [])
        self.assertTrue(any("reason=vt_symbol_mismatch" in line for line in cm.output))

    def test_dispatch_logs_drop_reason_for_invalid_price(self) -> None:
        strategy = _FakeStrategy(vt_symbol="IF0.CFFEX")
        bad_bar = _FakeBar(datetime=pd.Timestamp("2026-01-05 09:31:00"), close_price=float("nan"))
        with self.assertLogs("cta.sim.adapters.rotation_order_wire", level="DEBUG") as cm:
            out = dispatch_rotation_intents(
                strategy,
                intents=[_intent("IF0", "long", candidate={"entry_price_hint": None})],
                bar=bad_bar,
                cfg=RotationOrderWireConfig(only_trade_matching_vt_symbol=True, default_contract_size=300.0),
            )
        self.assertEqual(out, [])
        self.assertTrue(any("reason=invalid_price" in line for line in cm.output))


class _RunSimStrategy(_FakeStrategy):
    def __init__(
        self,
        cta_engine: Any,
        strategy_name: str,
        vt_symbol: str,
        setting: dict[str, Any] | None = None,
    ) -> None:
        super().__init__(vt_symbol=vt_symbol)
        self.cta_engine = cta_engine
        self.strategy_name = strategy_name
        self.setting = dict(setting or {})


class _RunSimCtaEngine:
    def __init__(self) -> None:
        self.classes: dict[str, type] = {}
        self.strategies: dict[str, Any] = {}

    def init_engine(self) -> None:
        return None

    def add_strategy(self, class_or_name: Any, name: str, vt_symbol: str, setting: dict[str, Any]) -> None:
        if isinstance(class_or_name, str):
            cls = self.classes[class_or_name]
        else:
            cls = class_or_name
        self.strategies[name] = cls(self, name, vt_symbol, setting)

    def init_strategy(self, name: str) -> None:  # noqa: ARG002
        return None

    def start_strategy(self, name: str) -> None:  # noqa: ARG002
        return None


class _RunSimMainEngine:
    def __init__(self) -> None:
        self.cta_engine = _RunSimCtaEngine()
        self.connections: list[tuple[dict[str, str], str]] = []

    def connect(self, setting: dict[str, str], gateway_name: str) -> None:
        self.connections.append((dict(setting), str(gateway_name)))

    def get_engine(self, name: str) -> Any:
        if name == "CtaStrategy":
            return self.cta_engine
        return None


class TestRunSimRotationWire(unittest.TestCase):
    def test_run_sim_attaches_rotation_wire_when_enabled(self) -> None:
        stepper = _FakeStepper([_intent("IF0", "long", target_notional=1_200_000.0)])
        cfg = SimRunConfig(
            strategy_class=_RunSimStrategy,
            strategy_name="rotation_if0",
            vt_symbol="IF0.CFFEX",
            setting={},
            enable_rotation_main_loop=True,
            rotation_stepper=stepper,
            rotation_wire_kwargs={"default_contract_size": 300.0},
        )
        me = _RunSimMainEngine()
        run_sim(
            cfg,
            SimnowSetting(userid="u", password="p"),
            main_engine_factory=lambda: me,
        )
        strategy = me.cta_engine.strategies["rotation_if0"]
        bar = _FakeBar(datetime=pd.Timestamp("2026-01-05 09:31:00"), close_price=4000.0)
        strategy.on_bar(bar)

        self.assertEqual(len(strategy.sent_orders), 1)
        # 1,200,000 / (4000*300) = 1 lot
        self.assertEqual(strategy.sent_orders[0]["volume"], 1)


if __name__ == "__main__":
    unittest.main()
