"""HolidayPositionReducer 测试。"""
from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.sizing.config import HolidayPositionReducerConfig
from cta.risk.sizing.holiday_position_reducer import HolidayPositionReducer


def _ctx(symbol="RB0", cluster=None, bar_dt="2026-01-15") -> SignalContext:
    cand = {"symbol": symbol}
    if cluster:
        cand["cluster"] = cluster
    return SignalContext(candidate=cand, portfolio={"equity": 1_000_000.0},
                          bar_dt=pd.Timestamp(bar_dt))


# 春节假期 2026-02-15 ~ 2026-02-21（假设）
HOLIDAYS = [date(2026, 2, 16), date(2026, 2, 17), date(2026, 2, 18),
            date(2026, 2, 19), date(2026, 2, 20)]


class TestHolidayPositionReducer(unittest.TestCase):

    def test_no_holidays_no_scale(self) -> None:
        sizer = HolidayPositionReducer()    # empty holidays
        new_lots, reason = sizer.scale(_ctx(), 10)
        self.assertEqual(new_lots, 10)
        self.assertIn("no_calendar", reason)

    def test_far_from_holiday_no_scale(self) -> None:
        sizer = HolidayPositionReducer(holidays=HOLIDAYS)
        # 距春节 32 天 → 不缩
        new_lots, _ = sizer.scale(_ctx(bar_dt="2026-01-15"), 10)
        self.assertEqual(new_lots, 10)

    def test_eve_of_holiday_floor(self) -> None:
        cfg = HolidayPositionReducerConfig(
            pre_holiday_taper_days=5, floor_mult=0.2,
        )
        sizer = HolidayPositionReducer(cfg, holidays=HOLIDAYS)
        # 距春节 0 天 → floor=0.2 → 10*0.2=2
        new_lots, reason = sizer.scale(_ctx(bar_dt="2026-02-16"), 10)
        self.assertEqual(new_lots, 2)
        self.assertIn("mult=0.20", reason)

    def test_taper_two_days_before(self) -> None:
        cfg = HolidayPositionReducerConfig(pre_holiday_taper_days=5, floor_mult=0.2)
        sizer = HolidayPositionReducer(cfg, holidays=HOLIDAYS)
        # 距 5 天的中间：2026-02-14 距 2026-02-16 = 2 天 → step=2/5 → mult=0.2+0.4*0.8=0.52
        new_lots, _ = sizer.scale(_ctx(bar_dt="2026-02-14"), 10)
        # 10 × 0.52 = 5.2 → floor 5
        self.assertEqual(new_lots, 5)

    def test_post_holiday_warmup(self) -> None:
        cfg = HolidayPositionReducerConfig(
            pre_holiday_taper_days=5, post_holiday_warmup_days=2, floor_mult=0.2,
        )
        sizer = HolidayPositionReducer(cfg, holidays=HOLIDAYS)
        # 节后第 1 天（2026-02-21，最后一个节假日 2026-02-20）→ d_after=1
        # step=1/2 → mult=0.2+0.5*0.8=0.6 → 10*0.6=6
        new_lots, _ = sizer.scale(_ctx(bar_dt="2026-02-21"), 10)
        self.assertEqual(new_lots, 6)

    def test_round_down_to_zero_preserves_one(self) -> None:
        cfg = HolidayPositionReducerConfig(pre_holiday_taper_days=5, floor_mult=0.2)
        sizer = HolidayPositionReducer(cfg, holidays=HOLIDAYS)
        # 1 × 0.2 = 0.2 → 但 mult>0 → 保 1
        new_lots, _ = sizer.scale(_ctx(bar_dt="2026-02-16"), 1)
        self.assertEqual(new_lots, 1)

    def test_inverse_for_bond_cluster(self) -> None:
        cfg = HolidayPositionReducerConfig(
            pre_holiday_taper_days=5, floor_mult=0.2, inverse_for_clusters=("bond",),
        )
        sizer = HolidayPositionReducer(cfg, holidays=HOLIDAYS)
        # 节前 0 天 → 普通 mult=0.2；inverse → mult=1+0.2-0.2=1.0 → 10*1.0=10
        new_lots, _ = sizer.scale(
            _ctx(symbol="T0", cluster="bond", bar_dt="2026-02-16"), 10,
        )
        self.assertEqual(new_lots, 10)

    def test_lots_zero_input(self) -> None:
        sizer = HolidayPositionReducer(holidays=HOLIDAYS)
        new_lots, reason = sizer.scale(_ctx(), 0)
        self.assertEqual(new_lots, 0)
        self.assertIn("lots_already_zero", reason)


class TestHolidayPositionReducerConfig(unittest.TestCase):

    def test_negative_days_raises(self) -> None:
        with self.assertRaises(ValueError):
            HolidayPositionReducerConfig(pre_holiday_taper_days=-1)

    def test_invalid_floor_raises(self) -> None:
        with self.assertRaises(ValueError):
            HolidayPositionReducerConfig(floor_mult=0.0)
        with self.assertRaises(ValueError):
            HolidayPositionReducerConfig(floor_mult=1.5)


if __name__ == "__main__":
    unittest.main()
