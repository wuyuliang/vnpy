"""cta.live.risk 单测。"""
from __future__ import annotations

import time
import threading
import unittest

import pandas as pd

from cta.live.risk import (
    DailyLossLimit,
    MaxOrderSize,
    MaxPositionLimit,
    OrderRateLimit,
    PortfolioThrottleRule,
    RiskContext,
    RiskDecision,
    RiskGuard,
    make_risk_filter,
)
from cta.portfolio_logic.config import CapsConfig, RiskThrottleConfig


def _order(vt_symbol: str = "rb888.SHFE", direction: str = "long",
           offset: str = "open", volume: float = 1.0, price: float = 100.0) -> dict:
    return {
        "vt_symbol": vt_symbol, "direction": direction,
        "offset": offset, "volume": volume, "price": price,
    }


def _ctx(**kw) -> RiskContext:
    base = {
        "pos": {"rb888.SHFE": 0.0},
        "daily_pnl": 0.0,
        "capital": 1_000_000.0,
        "now": pd.Timestamp.now("UTC"),
    }
    base.update(kw)
    return RiskContext(**base)


class TestMaxOrderSize(unittest.TestCase):
    def test_exceed_blocked(self) -> None:
        rule = MaxOrderSize(limits={"rb888.SHFE": 5})
        d = rule.check(_order(volume=10), _ctx())
        self.assertFalse(d.allowed)
        self.assertIn("max_order_size", d.reason)

    def test_within_limit_passes(self) -> None:
        rule = MaxOrderSize(limits={"rb888.SHFE": 5})
        self.assertTrue(rule.check(_order(volume=3), _ctx()).allowed)

    def test_no_rule_for_symbol_passes(self) -> None:
        rule = MaxOrderSize(limits={"hc888.SHFE": 5})
        self.assertTrue(rule.check(_order(vt_symbol="rb888.SHFE", volume=100), _ctx()).allowed)


class TestMaxPositionLimit(unittest.TestCase):
    def test_open_long_exceeding_blocked(self) -> None:
        rule = MaxPositionLimit(limits={"rb888.SHFE": 5})
        ctx = _ctx(pos={"rb888.SHFE": 4.0})
        d = rule.check(_order(direction="long", offset="open", volume=2), ctx)
        self.assertFalse(d.allowed)

    def test_open_long_within_limit_passes(self) -> None:
        rule = MaxPositionLimit(limits={"rb888.SHFE": 5})
        ctx = _ctx(pos={"rb888.SHFE": 2.0})
        d = rule.check(_order(direction="long", offset="open", volume=2), ctx)
        self.assertTrue(d.allowed)

    def test_close_not_blocked(self) -> None:
        """平仓不应被持仓上限阻止。"""
        rule = MaxPositionLimit(limits={"rb888.SHFE": 5})
        ctx = _ctx(pos={"rb888.SHFE": 5.0})
        d = rule.check(_order(direction="short", offset="close", volume=5), ctx)
        self.assertTrue(d.allowed)


class TestDailyLossLimit(unittest.TestCase):
    def test_exceed_blocks_open(self) -> None:
        rule = DailyLossLimit(max_loss=10_000.0)
        ctx = _ctx(daily_pnl=-15_000.0)
        d = rule.check(_order(offset="open"), ctx)
        self.assertFalse(d.allowed)

    def test_close_allowed_even_when_over_limit(self) -> None:
        """超亏后仍允许平仓减小风险。"""
        rule = DailyLossLimit(max_loss=10_000.0)
        ctx = _ctx(daily_pnl=-15_000.0)
        d = rule.check(_order(offset="close"), ctx)
        self.assertTrue(d.allowed)

    def test_within_limit_passes(self) -> None:
        rule = DailyLossLimit(max_loss=10_000.0)
        ctx = _ctx(daily_pnl=-5_000.0)
        d = rule.check(_order(offset="open"), ctx)
        self.assertTrue(d.allowed)


class TestOrderRateLimit(unittest.TestCase):
    def test_blocks_after_burst(self) -> None:
        rule = OrderRateLimit(max_per_second=2)
        ctx = _ctx()
        # 同一秒内 3 个订单：第 3 个被阻止
        self.assertTrue(rule.check(_order(), ctx).allowed)
        self.assertTrue(rule.check(_order(), ctx).allowed)
        self.assertFalse(rule.check(_order(), ctx).allowed)

    def test_recovers_after_window(self) -> None:
        rule = OrderRateLimit(max_per_second=2)
        for _ in range(2):
            self.assertTrue(rule.check(_order(), _ctx()).allowed)
        time.sleep(1.1)
        self.assertTrue(rule.check(_order(), _ctx()).allowed)

    def test_concurrent_checks_respect_single_slot_when_time_is_frozen(self) -> None:
        rule = OrderRateLimit(max_per_second=1)
        # 固定时间戳，所有线程命中同一秒窗口
        rule._time_fn = lambda: 100.0  # type: ignore[attr-defined]
        allowed_flags: list[bool] = []
        lock = threading.Lock()

        def _worker() -> None:
            d = rule.check(_order(), _ctx())
            with lock:
                allowed_flags.append(bool(d.allowed))

        threads = [threading.Thread(target=_worker) for _ in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()

        self.assertEqual(sum(1 for x in allowed_flags if x), 1)


class TestRiskGuard(unittest.TestCase):
    def test_first_failure_short_circuits(self) -> None:
        called: list[str] = []

        class _Rule:
            def __init__(self, name, allow): self.name = name; self.allow = allow
            def check(self, order, ctx):
                called.append(self.name)
                return RiskDecision(allowed=self.allow, reason=self.name if not self.allow else "")

        guard = RiskGuard(rules=[_Rule("first_block", False), _Rule("second", True)])
        d = guard.evaluate(_order(), _ctx())
        self.assertFalse(d.allowed)
        self.assertEqual(d.reason, "first_block")
        self.assertEqual(called, ["first_block"])

    def test_all_pass(self) -> None:
        guard = RiskGuard(
            rules=[
                MaxOrderSize(limits={"rb888.SHFE": 5}),
                DailyLossLimit(max_loss=10_000),
            ]
        )
        d = guard.evaluate(_order(volume=2), _ctx())
        self.assertTrue(d.allowed)


class TestPortfolioThrottleRule(unittest.TestCase):
    def test_blocks_open_when_halt_level_cap_zero(self) -> None:
        rule = PortfolioThrottleRule(
            base_caps=CapsConfig(max_total_positions=10, max_total_per_cluster=4),
            cfg=RiskThrottleConfig(),
        )
        ctx = _ctx(
            pos={"rb888.SHFE": 0.0},
            drawdown_pct=0.20,
            weekly_return_pct=0.0,
            monthly_return_pct=0.0,
            open_positions_total=0,
            open_positions_by_cluster={"black": 0},
        )
        d = rule.check(
            {
                "vt_symbol": "rb888.SHFE",
                "direction": "long",
                "offset": "open",
                "volume": 1.0,
                "cluster": "black",
            },
            ctx,
        )
        self.assertFalse(d.allowed)
        self.assertIn("throttle_total", d.reason)

    def test_matches_risk_throttle_caps_semantics(self) -> None:
        rule = PortfolioThrottleRule(
            base_caps=CapsConfig(max_total_positions=10, max_total_per_cluster=4),
            cfg=RiskThrottleConfig(),
        )
        # drawdown=7% -> reduced: total cap=7, cluster cap=2
        ctx = _ctx(
            drawdown_pct=0.07,
            weekly_return_pct=0.0,
            monthly_return_pct=0.0,
            open_positions_total=7,
            open_positions_by_cluster={"black": 1},
        )
        d_total = rule.check(
            {
                "vt_symbol": "rb888.SHFE",
                "direction": "long",
                "offset": "open",
                "volume": 1.0,
                "cluster": "black",
            },
            ctx,
        )
        self.assertFalse(d_total.allowed)
        self.assertIn("throttle_total", d_total.reason)

        ctx2 = _ctx(
            drawdown_pct=0.07,
            weekly_return_pct=0.0,
            monthly_return_pct=0.0,
            open_positions_total=6,
            open_positions_by_cluster={"black": 2},
        )
        d_cluster = rule.check(
            {
                "vt_symbol": "rb888.SHFE",
                "direction": "long",
                "offset": "open",
                "volume": 1.0,
                "cluster": "black",
            },
            ctx2,
        )
        self.assertFalse(d_cluster.allowed)
        self.assertIn("throttle_cluster", d_cluster.reason)


class TestMakeRiskFilter(unittest.TestCase):
    def test_flat_with_zero_position_is_noop(self) -> None:
        # 即使 guard 会拒绝任意订单，flat+pos=0 也应短路为 True（不进 guard）。
        class _AlwaysBlock:
            def check(self, order, ctx):  # noqa: ANN001
                return RiskDecision(False, "blocked")

        guard = RiskGuard(rules=[_AlwaysBlock()])
        f = make_risk_filter(guard, capital=1_000_000.0, daily_pnl_provider=lambda: 0.0)
        adapter = type(
            "A",
            (),
            {
                "pos": 0.0,
                "vt_symbol": "rb888.SHFE",
                "write_log": lambda self, msg: None,  # noqa: ARG005
            },
        )()
        order = {"side": "flat", "lots": 1, "price": 100.0, "order_type": "market"}
        self.assertTrue(f(order, adapter))


if __name__ == "__main__":
    unittest.main()
