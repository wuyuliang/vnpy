"""P2-20: timezone 一致性测试.

验证 UTC vs Asia/Shanghai vs naive 转换在全链路上对齐。
"""
from __future__ import annotations

import unittest
from datetime import datetime, timezone, timedelta

import pandas as pd

from cta.sim.timezone_helpers import (
    SHANGHAI_TZ,
    ensure_naive_shanghai,
    from_utc,
    is_timezone_consistent,
    to_utc,
)


class TestTimezoneRoundTrip(unittest.TestCase):
    def test_naive_assumed_shanghai(self) -> None:
        ts = pd.Timestamp("2024-03-25 10:00:00")  # naive
        utc = to_utc(ts)
        self.assertEqual(utc.tz.utcoffset(None), timedelta(0))
        # 10:00 上海 → 02:00 UTC
        self.assertEqual(utc.hour, 2)
        self.assertEqual(utc.day, 25)

    def test_naive_input_stays_naive(self) -> None:
        ts = pd.Timestamp("2024-03-25 10:00:00")
        result = ensure_naive_shanghai(ts)
        self.assertIsNone(result.tzinfo)
        self.assertEqual(result.hour, 10)

    def test_aware_utc_converted_to_naive_shanghai(self) -> None:
        ts = pd.Timestamp("2024-03-25 02:00:00", tz="UTC")
        result = ensure_naive_shanghai(ts)
        self.assertIsNone(result.tzinfo)
        self.assertEqual(result.hour, 10)  # +8 hours

    def test_aware_eastern_converted(self) -> None:
        # NY 时间 22:00 = +8 时区次日 11:00
        ts = pd.Timestamp("2024-03-25 22:00:00", tz="US/Eastern")
        result = ensure_naive_shanghai(ts)
        # NY 22:00 EDT (UTC-4) = UTC 02:00 next day = Shanghai 10:00 next day
        self.assertEqual(result.date().isoformat(), "2024-03-26")

    def test_from_utc_to_naive_shanghai(self) -> None:
        utc_ts = pd.Timestamp("2024-03-25 02:00:00", tz="UTC")
        result = from_utc(utc_ts)
        self.assertIsNone(result.tzinfo)
        self.assertEqual(result.hour, 10)

    def test_from_utc_naive_input_treated_as_utc(self) -> None:
        naive = pd.Timestamp("2024-03-25 02:00:00")
        result = from_utc(naive)
        self.assertEqual(result.hour, 10)

    def test_round_trip_naive_to_utc_back(self) -> None:
        ts = pd.Timestamp("2024-03-25 10:00:00")
        round_trip = from_utc(to_utc(ts))
        self.assertEqual(round_trip, ts)


class TestTimezoneConsistency(unittest.TestCase):
    def test_all_naive_consistent(self) -> None:
        series = pd.Series(pd.date_range("2024-03-25", periods=5, freq="D"))
        consistent, msg = is_timezone_consistent(series)
        self.assertTrue(consistent)
        self.assertIn("naive", msg)

    def test_all_aware_utc_consistent(self) -> None:
        series = pd.Series(pd.date_range("2024-03-25", periods=5, freq="D", tz="UTC"))
        consistent, _ = is_timezone_consistent(series)
        self.assertTrue(consistent)

    def test_mixed_naive_and_aware_flagged(self) -> None:
        """object dtype Series 保留独立 tz；is_timezone_consistent 检出 mixed。"""
        series = pd.Series(
            [
                pd.Timestamp("2024-03-25 10:00:00"),
                pd.Timestamp("2024-03-25 10:00:00", tz="UTC"),
            ],
            dtype=object,
        )
        consistent, msg = is_timezone_consistent(series)
        self.assertFalse(consistent)
        self.assertIn("mixed", msg)

    def test_empty_series_returns_true(self) -> None:
        consistent, _ = is_timezone_consistent(pd.Series(dtype="datetime64[ns]"))
        self.assertTrue(consistent)


class TestSimLiveDataAlignment(unittest.TestCase):
    """端到端：vnpy bar (naive Shanghai) → OOT parquet → 对账无 tz 漂移。"""

    def test_vnpy_bar_compatible_with_oot_parquet(self) -> None:
        """vnpy bar.datetime 是 naive，与 OOT parquet 一致。"""
        # 模拟 vnpy bar
        bar_dt = datetime(2024, 3, 25, 14, 30, 0)
        # 模拟 parquet 读取
        parquet_dt = pd.Timestamp("2024-03-25 14:30:00")
        # 两者应该 equal（都是 naive Shanghai 14:30）
        normalized = ensure_naive_shanghai(bar_dt)
        self.assertEqual(normalized, parquet_dt)

    def test_external_utc_source_converted_correctly(self) -> None:
        """假设有外盘 source 用 UTC，转换后与本地 naive 一致。"""
        # 美东盘开始 09:30 EDT = 21:30 Shanghai = 13:30 UTC
        external_utc = pd.Timestamp("2024-03-25 13:30:00", tz="UTC")
        local_naive = ensure_naive_shanghai(external_utc)
        self.assertEqual(local_naive.hour, 21)
        self.assertEqual(local_naive.minute, 30)


if __name__ == "__main__":
    unittest.main()
