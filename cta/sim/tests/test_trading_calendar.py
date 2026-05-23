"""P2-18: 夜盘 / 节假日 / 涨跌停板边界测试."""
from __future__ import annotations

import unittest
from datetime import date

import pandas as pd

from cta.sim.trading_calendar import (
    is_at_price_limit,
    is_holiday,
    is_trading_day,
    is_trading_session,
    is_weekend,
)


class TestSession(unittest.TestCase):
    def test_day_session_window(self) -> None:
        s = is_trading_session(pd.Timestamp("2024-03-25 10:00:00"))
        self.assertTrue(s.is_trading_time)
        self.assertEqual(s.session, "day")

    def test_night_session_late_evening(self) -> None:
        s = is_trading_session(pd.Timestamp("2024-03-25 22:30:00"))
        self.assertTrue(s.is_trading_time)
        self.assertEqual(s.session, "night")

    def test_night_session_after_midnight(self) -> None:
        s = is_trading_session(pd.Timestamp("2024-03-26 01:30:00"))
        self.assertTrue(s.is_trading_time)
        self.assertEqual(s.session, "night")

    def test_off_session_morning(self) -> None:
        # 凌晨 5 点不交易
        s = is_trading_session(pd.Timestamp("2024-03-26 05:00:00"))
        self.assertFalse(s.is_trading_time)
        self.assertEqual(s.session, "off")

    def test_off_session_evening_gap(self) -> None:
        # 16:00 已收盘，21:00 未开夜盘
        s = is_trading_session(pd.Timestamp("2024-03-25 18:00:00"))
        self.assertFalse(s.is_trading_time)

    def test_no_night_session_for_index(self) -> None:
        s = is_trading_session(pd.Timestamp("2024-03-25 22:30:00"), has_night_session=False)
        self.assertFalse(s.is_trading_time)


class TestHolidaysWeekend(unittest.TestCase):
    def test_weekend_saturday(self) -> None:
        self.assertTrue(is_weekend(pd.Timestamp("2024-03-23")))  # 周六
        self.assertTrue(is_weekend(pd.Timestamp("2024-03-24")))  # 周日
        self.assertFalse(is_weekend(pd.Timestamp("2024-03-25")))  # 周一

    def test_holiday_spring_festival(self) -> None:
        # 2024 春节假期
        spring_festival = {
            date(2024, 2, 9), date(2024, 2, 10), date(2024, 2, 11),
            date(2024, 2, 12), date(2024, 2, 13), date(2024, 2, 14),
            date(2024, 2, 15), date(2024, 2, 16), date(2024, 2, 17),
        }
        self.assertTrue(is_holiday(pd.Timestamp("2024-02-12"), spring_festival))
        self.assertFalse(is_holiday(pd.Timestamp("2024-03-25"), spring_festival))

    def test_is_trading_day_combines_weekend_and_holiday(self) -> None:
        holidays = {date(2024, 2, 12)}  # 春节
        # 周末
        self.assertFalse(is_trading_day(pd.Timestamp("2024-03-23")))
        # 节假日
        self.assertFalse(is_trading_day(pd.Timestamp("2024-02-12"), holidays=holidays))
        # 正常工作日
        self.assertTrue(is_trading_day(pd.Timestamp("2024-03-25"), holidays=holidays))


class TestPriceLimits(unittest.TestCase):
    def test_rb_up_limit(self) -> None:
        # black cluster limit_pct = 0.06
        is_lim, dir_ = is_at_price_limit(
            symbol="RB0", current_price=4240, prev_close=4000,
        )
        self.assertTrue(is_lim)
        self.assertEqual(dir_, "up")

    def test_rb_down_limit(self) -> None:
        is_lim, dir_ = is_at_price_limit(
            symbol="RB0", current_price=3760, prev_close=4000,
        )
        self.assertTrue(is_lim)
        self.assertEqual(dir_, "down")

    def test_rb_normal_no_limit(self) -> None:
        is_lim, dir_ = is_at_price_limit(
            symbol="RB0", current_price=4100, prev_close=4000,
        )
        self.assertFalse(is_lim)
        self.assertEqual(dir_, "")

    def test_index_limit_pct_10pct(self) -> None:
        # index limit_pct = 0.10
        is_lim, dir_ = is_at_price_limit(
            symbol="IF0", current_price=3300, prev_close=3000,
        )
        self.assertTrue(is_lim)
        self.assertEqual(dir_, "up")

    def test_bond_limit_pct_2pct(self) -> None:
        # bond limit_pct = 0.02
        is_lim, dir_ = is_at_price_limit(
            symbol="T0", current_price=100.5, prev_close=100.0,
        )
        # 0.5% < 2% → 不触
        self.assertFalse(is_lim)
        is_lim2, _ = is_at_price_limit(
            symbol="T0", current_price=102.1, prev_close=100.0,
        )
        # 2.1% > 2% → 触
        self.assertTrue(is_lim2)

    def test_cluster_specific_limits_match_config(self) -> None:
        """涨跌停 pct 与 [config/symbol_cluster_config.py CLUSTER_LIMIT_PCT] 一致。"""
        # black 6%
        self.assertTrue(is_at_price_limit(symbol="RB0", current_price=4240.5, prev_close=4000)[0])
        # 不到 6% 时不触
        self.assertFalse(is_at_price_limit(symbol="RB0", current_price=4230, prev_close=4000)[0])


if __name__ == "__main__":
    unittest.main()
