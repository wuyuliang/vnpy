"""cta.sim.parity_check 单测。"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.sim.parity_check import (
    ParityResult,
    SignalRecord,
    compare_signals,
    trade_log_to_signals,
)


def _sig(t: str, side: str, lots: int = 1, order_type: str = "market", price: float | None = None) -> SignalRecord:
    return SignalRecord(
        timestamp=pd.Timestamp(t),
        side=side,
        lots=lots,
        order_type=order_type,
        price=price,
    )


class TestCompareSignals(unittest.TestCase):
    def test_identical(self) -> None:
        a = [_sig("2024-01-02 09:00", "long"), _sig("2024-01-03 14:00", "flat")]
        b = list(a)
        res = compare_signals(a, b)
        self.assertEqual(res.matched, 2)
        self.assertEqual(res.mismatched, 0)
        self.assertEqual(res.only_in_a, 0)
        self.assertEqual(res.only_in_b, 0)
        self.assertAlmostEqual(res.mismatch_rate, 0.0)

    def test_one_extra_in_a(self) -> None:
        a = [_sig("2024-01-02 09:00", "long"), _sig("2024-01-03 14:00", "flat")]
        b = [_sig("2024-01-02 09:00", "long")]
        res = compare_signals(a, b)
        self.assertEqual(res.matched, 1)
        self.assertEqual(res.only_in_a, 1)
        self.assertEqual(res.only_in_b, 0)

    def test_direction_diff_counts_as_mismatch(self) -> None:
        a = [_sig("2024-01-02 09:00", "long")]
        b = [_sig("2024-01-02 09:00", "short")]
        res = compare_signals(a, b)
        self.assertEqual(res.matched, 0)
        self.assertEqual(res.mismatched, 1)
        self.assertAlmostEqual(res.mismatch_rate, 1.0)

    def test_time_tolerance(self) -> None:
        a = [_sig("2024-01-02 09:00:00", "long")]
        b = [_sig("2024-01-02 09:00:30", "long")]
        # 容忍 1 分钟内 → 视为同一信号
        res = compare_signals(a, b, time_tolerance=pd.Timedelta("1min"))
        self.assertEqual(res.matched, 1)
        # 容忍 10 秒 → 视为不同
        res2 = compare_signals(a, b, time_tolerance=pd.Timedelta("10s"))
        self.assertEqual(res2.only_in_a, 1)
        self.assertEqual(res2.only_in_b, 1)

    def test_lots_diff_counts_mismatch(self) -> None:
        a = [_sig("2024-01-02 09:00", "long", lots=1)]
        b = [_sig("2024-01-02 09:00", "long", lots=2)]
        res = compare_signals(a, b)
        self.assertEqual(res.mismatched, 1)
        # 详情应包含 lots 差异行
        self.assertGreaterEqual(len(res.details), 1)
        self.assertIn("lots_a", res.details.columns)

    def test_empty_inputs(self) -> None:
        res = compare_signals([], [])
        self.assertIsInstance(res, ParityResult)
        self.assertEqual(res.matched, 0)
        self.assertEqual(res.mismatch_rate, 0.0)


class TestTradeLogToSignals(unittest.TestCase):
    def test_long_trade_yields_two_signals(self) -> None:
        # 一笔 long 交易在 trade_log 中表现为 entry + exit；转换为 2 条信号
        trade_log = pd.DataFrame(
            [
                {
                    "entry_i": 5, "exit_i": 12, "side": "long", "lots": 2,
                    "entry_price": 100.0, "exit_price": 105.0,
                    "gross_pnl": 100.0, "cost": 0, "net_pnl": 100.0,
                }
            ]
        )
        dates = pd.date_range("2024-01-02", periods=20, freq="D")
        sigs = trade_log_to_signals(trade_log, dates)
        self.assertEqual(len(sigs), 2)
        self.assertEqual(sigs[0].side, "long")
        self.assertEqual(sigs[0].lots, 2)
        self.assertEqual(sigs[1].side, "flat")

    def test_empty(self) -> None:
        self.assertEqual(trade_log_to_signals(pd.DataFrame(), pd.date_range("2024-01-01", periods=5)), [])


if __name__ == "__main__":
    unittest.main()
