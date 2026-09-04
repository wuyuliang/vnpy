"""Online shell that delegates every decision to the shared cycle_v1 core."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from vnpy.trader.object import BarData, TickData
from vnpy.trader.utility import BarGenerator
from vnpy_ctastrategy import CtaTemplate

from ..core.strategy import BrooksCycleV1Core, StrategyDecision, StrategyInput


class BrooksCycleV1Online:
    def __init__(self, core: BrooksCycleV1Core) -> None:
        self.core = core

    def on_snapshot(self, item: StrategyInput) -> StrategyDecision:
        return self.core.on_snapshot(item)


class BrooksCycleV1Strategy(CtaTemplate):
    """Independent vn.py dry-run registration; real order transport is fail-closed."""

    author = "Brooks cycle v1"
    enabled: bool = False
    send_orders: bool = False
    parameters = ["enabled", "send_orders"]
    variables = ["pos", "last_candidate_count", "last_plan_count"]

    def __init__(
        self,
        cta_engine: object,
        strategy_name: str,
        vt_symbol: str,
        setting: dict[str, Any],
    ) -> None:
        runtime = dict(setting)
        core = runtime.pop("_core", None)
        snapshot_builder = runtime.pop("_snapshot_builder", None)
        super().__init__(cta_engine, strategy_name, vt_symbol, runtime)
        if not isinstance(core, BrooksCycleV1Core):
            raise ValueError("BrooksCycleV1Strategy requires a shared _core")
        if not callable(snapshot_builder):
            raise ValueError("BrooksCycleV1Strategy requires a causal _snapshot_builder")
        self.online = BrooksCycleV1Online(core)
        self.snapshot_builder: Callable[[BarData], StrategyInput] = snapshot_builder
        self.bg = BarGenerator(self.on_bar)
        self.last_candidate_count = 0
        self.last_plan_count = 0

    def on_init(self) -> None:
        self.write_log("Brooks cycle v1 initializing")
        self.load_bar(30)

    def on_start(self) -> None:
        if self.send_orders:
            raise RuntimeError(
                "cycle_v1 real-order transport is not promoted; use decision-only dry-run"
            )
        if not self.enabled:
            self.write_log("Brooks cycle v1 is disabled; no orders will be sent")
        else:
            self.write_log("Brooks cycle v1 decision-only dry-run started")

    def on_stop(self) -> None:
        self.cancel_all()

    def on_tick(self, tick: TickData) -> None:
        self.bg.update_tick(tick)

    def on_bar(self, bar: BarData) -> None:
        if not self.enabled:
            return
        if self.send_orders:
            raise RuntimeError("cycle_v1 real-order transport remains fail-closed")
        decision = self.online.on_snapshot(self.snapshot_builder(bar))
        self.last_candidate_count = len(decision.setup_decisions)
        self.last_plan_count = len(decision.order_plans)
        self.write_log(str(decision.to_audit_dict()))
        self.put_event()


__all__ = ["BrooksCycleV1Online", "BrooksCycleV1Strategy"]
