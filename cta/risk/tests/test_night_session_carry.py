"""NightSessionCarryRule 测试。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.sizing.config import NightSessionCarryConfig
from cta.risk.sizing.night_session_carry import NightSessionCarryRule


def _ctx(bar_dt: str, cluster=None, symbol="RB0") -> SignalContext:
    cand = {"symbol": symbol}
    if cluster:
        cand["cluster"] = cluster
    return SignalContext(candidate=cand, portfolio={"equity": 1_000_000.0},
                          bar_dt=pd.Timestamp(bar_dt))


class TestNightSessionCarryRule(unittest.TestCase):

    def setUp(self) -> None:
        self.sc = NightSessionCarryRule()

    def test_not_friday_no_scale(self) -> None:
        # 2026-01-12 是周一
        new_lots, reason = self.sc.scale(_ctx("2026-01-12 15:00"), 10)
        self.assertEqual(new_lots, 10)
        self.assertIn("not_friday", reason)

    def test_friday_morning_no_scale(self) -> None:
        # 2026-01-16 周五 但时间 < 14:30
        new_lots, reason = self.sc.scale(_ctx("2026-01-16 10:00"), 10)
        self.assertEqual(new_lots, 10)
        self.assertIn("before_taper_time", reason)

    def test_friday_after_taper_black(self) -> None:
        # 2026-01-16 周五 14:31，cluster=black → mult=0.5
        new_lots, reason = self.sc.scale(_ctx("2026-01-16 14:31", cluster="black"), 10)
        self.assertEqual(new_lots, 5)
        self.assertIn("mult=0.50", reason)

    def test_friday_after_taper_bond(self) -> None:
        # cluster=bond → 0.3 → 10*0.3=3
        new_lots, _ = self.sc.scale(_ctx("2026-01-16 14:31", cluster="bond"), 10)
        self.assertEqual(new_lots, 3)

    def test_friday_after_taper_precious(self) -> None:
        # cluster=precious → 0.6 → 10*0.6=6
        new_lots, _ = self.sc.scale(_ctx("2026-01-16 14:31", cluster="precious"), 10)
        self.assertEqual(new_lots, 6)

    def test_disabled_passes_through(self) -> None:
        cfg = NightSessionCarryConfig(enable_friday_taper=False)
        sc = NightSessionCarryRule(cfg)
        new_lots, reason = sc.scale(_ctx("2026-01-16 15:00", cluster="black"), 10)
        self.assertEqual(new_lots, 10)
        self.assertIn("disabled", reason)

    def test_default_mult_when_unknown_cluster(self) -> None:
        cfg = NightSessionCarryConfig(
            weekend_carry_mult_by_cluster={},
            default_mult=0.4,
        )
        sc = NightSessionCarryRule(cfg)
        new_lots, _ = sc.scale(_ctx("2026-01-16 14:31", cluster="unknown_cluster"), 10)
        self.assertEqual(new_lots, 4)

    def test_cluster_inferred_from_symbol(self) -> None:
        # 不传 cluster，T0 自动推断为 bond
        new_lots, reason = self.sc.scale(_ctx("2026-01-16 14:31", symbol="T0"), 10)
        # bond → 0.3
        self.assertEqual(new_lots, 3)
        self.assertIn("cluster=bond", reason)

    def test_round_down_preserves_min(self) -> None:
        new_lots, _ = self.sc.scale(_ctx("2026-01-16 14:31", cluster="black"), 1)
        # 1 × 0.5 = 0.5 → 1（保护）
        self.assertEqual(new_lots, 1)

    def test_lots_zero_input(self) -> None:
        new_lots, reason = self.sc.scale(_ctx("2026-01-16 14:31"), 0)
        self.assertEqual(new_lots, 0)
        self.assertIn("lots_already_zero", reason)


class TestNightSessionCarryConfig(unittest.TestCase):

    def test_invalid_hhmm_raises(self) -> None:
        with self.assertRaises(ValueError):
            NightSessionCarryConfig(friday_taper_after_hhmm="25:00")
        with self.assertRaises(ValueError):
            NightSessionCarryConfig(friday_taper_after_hhmm="not_a_time")

    def test_invalid_mult_raises(self) -> None:
        with self.assertRaises(ValueError):
            NightSessionCarryConfig(weekend_carry_mult_by_cluster={"black": 1.5})

    def test_invalid_default_raises(self) -> None:
        with self.assertRaises(ValueError):
            NightSessionCarryConfig(default_mult=0.0)


if __name__ == "__main__":
    unittest.main()
