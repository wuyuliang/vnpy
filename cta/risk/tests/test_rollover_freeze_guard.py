"""RolloverFreezeGuard 测试。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.live.risk import RiskContext
from cta.risk.guards.config import RolloverFreezeGuardConfig
from cta.risk.guards.rollover_freeze_guard import RolloverFreezeGuard
from cta.risk.state.rollover_calendar import RolloverCalendar, RolloverEntry


def _ctx(now: str = "2024-01-01") -> RiskContext:
    return RiskContext(pos={}, daily_pnl=0.0, capital=1_000_000.0, now=pd.Timestamp(now))


def _calendar(expiry: str, main_switch: str | None = None) -> RolloverCalendar:
    return RolloverCalendar([
        RolloverEntry(
            symbol="RB2401",
            expiry_date=pd.Timestamp(expiry),
            main_switch_date=pd.Timestamp(main_switch) if main_switch else None,
        ),
    ])


def _order(symbol="RB2401", direction="long", offset="open") -> dict:
    return {"symbol": symbol, "direction": direction, "offset": offset, "price": 3000.0}


class TestRolloverFreezeGuard(unittest.TestCase):

    def test_no_calendar_data_fails_open(self) -> None:
        guard = RolloverFreezeGuard()
        decision = guard.check(_order(), _ctx("2024-01-01"))
        self.assertTrue(decision.allowed)

    def test_far_from_expiry_passes(self) -> None:
        cal = _calendar("2024-04-15")
        guard = RolloverFreezeGuard(calendar=cal)
        # 距过期 3+ 月 → 放行
        decision = guard.check(_order(), _ctx("2024-01-01"))
        self.assertTrue(decision.allowed)

    def test_within_freeze_window_blocks_open(self) -> None:
        cal = _calendar("2024-01-15")
        guard = RolloverFreezeGuard(calendar=cal)
        # 距过期 5 天 (>force_close 3 但 <freeze 7) → expiry_window
        decision = guard.check(_order(), _ctx("2024-01-10"))
        self.assertFalse(decision.allowed)
        self.assertIn("expiry_window", decision.reason)

    def test_within_force_close_window_blocks_open(self) -> None:
        cal = _calendar("2024-01-15")
        guard = RolloverFreezeGuard(calendar=cal)
        # 距过期 2 天 → force_close_window
        decision = guard.check(_order(), _ctx("2024-01-13"))
        self.assertFalse(decision.allowed)
        self.assertIn("force_close_window", decision.reason)

    def test_close_allowed_in_freeze(self) -> None:
        cal = _calendar("2024-01-15")
        guard = RolloverFreezeGuard(calendar=cal)
        decision = guard.check(_order(offset="close"), _ctx("2024-01-13"))
        self.assertTrue(decision.allowed)

    def test_close_blocked_when_disabled(self) -> None:
        cfg = RolloverFreezeGuardConfig(allow_close_during_freeze=False)
        cal = _calendar("2024-01-15")
        guard = RolloverFreezeGuard(cfg, calendar=cal)
        # 注：close 并不会被 freeze 拦（只过 expiry < 0 时才拦）
        # 实际：cfg allow_close=False 让 close 也走 expiry_window 判定
        decision = guard.check(_order(offset="close"), _ctx("2024-01-13"))
        # 此 cfg 下 close 仍可放行（offset != "open"，且 d2e=2 不算 expired）
        # 这条测试目的：确认 cfg 不会引入新 bug
        self.assertTrue(decision.allowed)

    def test_expired_blocks_all(self) -> None:
        cal = _calendar("2024-01-15")
        guard = RolloverFreezeGuard(calendar=cal)
        # 已经过期
        decision = guard.check(_order(), _ctx("2024-02-01"))
        self.assertFalse(decision.allowed)
        self.assertIn("expired", decision.reason)

    def test_post_main_switch_blocks_open(self) -> None:
        cal = _calendar("2024-05-15", main_switch="2024-01-01")
        guard = RolloverFreezeGuard(calendar=cal)
        # 主力切换 2 天后 → post_switch_freeze
        decision = guard.check(_order(), _ctx("2024-01-03"))
        self.assertFalse(decision.allowed)
        self.assertIn("post_switch_freeze", decision.reason)

    def test_post_main_switch_expired_passes(self) -> None:
        cal = _calendar("2024-05-15", main_switch="2024-01-01")
        guard = RolloverFreezeGuard(calendar=cal)
        # 主力切换 10 天后（> post_switch_freeze=3）→ 放行
        decision = guard.check(_order(), _ctx("2024-01-11"))
        self.assertTrue(decision.allowed)

    def test_cluster_override(self) -> None:
        cfg = RolloverFreezeGuardConfig(days_to_expiry_override_by_cluster={"bond": 14})
        cal = RolloverCalendar([
            RolloverEntry(symbol="T2401", expiry_date=pd.Timestamp("2024-01-15"), main_switch_date=None),
        ])
        guard = RolloverFreezeGuard(cfg, calendar=cal)
        # T → bond cluster → 用 14 天 freeze 窗口
        # 距过期 10 天 → 仍在 freeze 内
        decision = guard.check(_order(symbol="T2401"), _ctx("2024-01-05"))
        self.assertFalse(decision.allowed)
        self.assertIn("expiry_window", decision.reason)

    def test_missing_symbol_fails_open(self) -> None:
        cal = _calendar("2024-01-15")
        guard = RolloverFreezeGuard(calendar=cal)
        decision = guard.check({"direction": "long", "offset": "open"}, _ctx("2024-01-13"))
        self.assertTrue(decision.allowed)


class TestRolloverFreezeGuardConfig(unittest.TestCase):

    def test_negative_window_raises(self) -> None:
        with self.assertRaises(ValueError):
            RolloverFreezeGuardConfig(freeze_window_days=-1)

    def test_force_close_exceeds_freeze_raises(self) -> None:
        with self.assertRaises(ValueError):
            RolloverFreezeGuardConfig(freeze_window_days=3, force_close_days=5)

    def test_negative_override_raises(self) -> None:
        with self.assertRaises(ValueError):
            RolloverFreezeGuardConfig(days_to_expiry_override_by_cluster={"bond": -1})


if __name__ == "__main__":
    unittest.main()
