"""ProfitGiveBackGuard + tracker + sizer tests (W9)."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.live.risk import RiskContext
from cta.risk.base import SignalContext
from cta.risk.guards.config import ProfitGiveBackConfig
from cta.risk.guards.profit_give_back_guard import ProfitGiveBackGuard
from cta.risk.sizing.profit_give_back import ProfitGiveBackSizer
from cta.risk.state.intraday_profit_tracker import IntradayProfitTracker


def _risk_ctx(now: str, daily_pnl: float) -> RiskContext:
    return RiskContext(
        pos={},
        daily_pnl=daily_pnl,
        capital=1_000_000.0,
        now=pd.Timestamp(now),
    )


def _signal_ctx(now: str, daily_pnl: float) -> SignalContext:
    return SignalContext(
        candidate={"symbol": "RB0", "interval": "day", "cluster": "black"},
        portfolio={"equity": 1_000_000.0, "daily_pnl": daily_pnl},
        bar_dt=pd.Timestamp(now),
    )


def _order(offset: str = "open") -> dict:
    return {"symbol": "RB0", "direction": "long", "offset": offset, "price": 3200.0}


class TestProfitGiveBackGuard(unittest.TestCase):
    def test_trigger_then_block_new_open(self) -> None:
        cfg = ProfitGiveBackConfig(
            activation_pnl_pct=0.03,
            give_back_ratio=0.50,
            block_new_opens_after_trigger=True,
        )
        tracker = IntradayProfitTracker(cfg)
        guard = ProfitGiveBackGuard(cfg, tracker=tracker)

        # 日内先冲到 +4%
        d1 = guard.check(_order("open"), _risk_ctx("2026-01-05 10:00:00", daily_pnl=40_000.0))
        self.assertTrue(d1.allowed)
        # 回吐到 +1.8%（回吐 2.2%，超过 peak*50%=2.0%）
        d2 = guard.check(_order("open"), _risk_ctx("2026-01-05 11:00:00", daily_pnl=18_000.0))
        self.assertFalse(d2.allowed)
        self.assertIn("profit_give_back", d2.reason)
        # 平仓默认允许
        d3 = guard.check(_order("close"), _risk_ctx("2026-01-05 11:01:00", daily_pnl=18_000.0))
        self.assertTrue(d3.allowed)

    def test_next_day_auto_reset(self) -> None:
        cfg = ProfitGiveBackConfig(
            activation_pnl_pct=0.03,
            give_back_ratio=0.50,
            block_new_opens_after_trigger=True,
        )
        tracker = IntradayProfitTracker(cfg)
        guard = ProfitGiveBackGuard(cfg, tracker=tracker)
        guard.check(_order("open"), _risk_ctx("2026-01-05 10:00:00", daily_pnl=40_000.0))
        guard.check(_order("open"), _risk_ctx("2026-01-05 11:00:00", daily_pnl=18_000.0))
        # 次日 reset 后不应继续阻断
        d = guard.check(_order("open"), _risk_ctx("2026-01-06 09:10:00", daily_pnl=0.0))
        self.assertTrue(d.allowed)


class TestProfitGiveBackSizer(unittest.TestCase):
    def test_sizer_returns_zero_when_triggered(self) -> None:
        cfg = ProfitGiveBackConfig(
            activation_pnl_pct=0.03,
            give_back_ratio=0.50,
            block_new_opens_after_trigger=True,
        )
        tracker = IntradayProfitTracker(cfg)
        sizer = ProfitGiveBackSizer(cfg, tracker=tracker)
        # 触发
        sizer.scale(_signal_ctx("2026-01-05 10:00:00", 40_000.0), 10)
        new_lots, reason = sizer.scale(_signal_ctx("2026-01-05 11:00:00", 18_000.0), 10)
        self.assertEqual(new_lots, 0)
        self.assertIn("triggered", reason)


class TestProfitGiveBackConfig(unittest.TestCase):
    def test_invalid_activation_raises(self) -> None:
        with self.assertRaises(ValueError):
            ProfitGiveBackConfig(activation_pnl_pct=-0.01)

    def test_invalid_ratio_raises(self) -> None:
        with self.assertRaises(ValueError):
            ProfitGiveBackConfig(give_back_ratio=1.2)


if __name__ == "__main__":
    unittest.main()
