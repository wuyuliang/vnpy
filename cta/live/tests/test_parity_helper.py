"""cta.live.parity_helper 单测。"""
from __future__ import annotations

import unittest
from datetime import datetime
from types import SimpleNamespace

import pandas as pd

from cta.live.parity_helper import (
    backtest_on_same_bars,
    recorder_to_parity_inputs,
    trade_log_to_equity,
)
from cta.live.trade_recorder import TradeRecorder


def _trade(direction: str, offset: str, price: float, volume: float,
           dt: datetime) -> SimpleNamespace:
    return SimpleNamespace(
        symbol="rb888", exchange=SimpleNamespace(value="SHFE"),
        direction=SimpleNamespace(value=direction),
        offset=SimpleNamespace(value=offset),
        price=price, volume=volume, datetime=dt,
        tradeid=f"t-{dt.isoformat()}", orderid=f"o-{dt.isoformat()}",
        gateway_name="CTP",
    )


class TestTradeLogToEquity(unittest.TestCase):
    def test_accumulates_at_exit_i(self) -> None:
        trade_log = pd.DataFrame([
            {"entry_i": 1, "exit_i": 3, "side": "long", "lots": 1,
             "entry_price": 100, "exit_price": 105,
             "gross_pnl": 5, "cost": 0, "net_pnl": 5},
            {"entry_i": 4, "exit_i": 7, "side": "short", "lots": 1,
             "entry_price": 110, "exit_price": 108,
             "gross_pnl": 2, "cost": 0, "net_pnl": 2},
        ])
        eq = trade_log_to_equity(trade_log, n_bars=10)
        self.assertEqual(len(eq), 10)
        self.assertEqual(list(eq), [0, 0, 0, 5, 5, 5, 5, 7, 7, 7])


class TestRecorderToParityInputs(unittest.TestCase):
    def test_round_trip(self) -> None:
        r = TradeRecorder(out_dir=".", vt_symbol="rb888.SHFE")
        r.record(_trade("long",  "open",  100.0, 1, datetime(2024, 1, 2, 9, 30)))
        r.record(_trade("short", "close", 105.0, 1, datetime(2024, 1, 2, 14, 30)))
        dates = pd.Series(pd.date_range("2024-01-02", periods=4, freq="h"))
        # 把成交时间手动映射为 [exit_i=2] 的合成 dates 用作示意
        tl, eq, dts = recorder_to_parity_inputs(
            r,
            bar_dates=dates,
            multiplier=10.0,
            commission=0.0,
        )
        self.assertEqual(len(tl), 1)
        self.assertAlmostEqual(tl.iloc[0]["net_pnl"], 50.0)
        self.assertEqual(len(eq), len(dates))
        self.assertEqual(len(dts), len(dates))
        self.assertGreater(eq.iloc[-1], 0)


class TestBacktestOnSameBars(unittest.TestCase):
    """同 K 线下重新跑回测、获取 backtest 侧 trade_log，供 parity_check 比较。"""

    def test_runs_strategy_class_returns_trade_log(self) -> None:
        import numpy as np
        from cta.strategy.cta_baseline import DonchianCta
        rng = np.random.default_rng(0)
        n_pre, n_break = 80, 25
        rows = []
        base = 100.0
        t0 = datetime(2024, 1, 2)
        for i in range(n_pre):
            c = base + rng.uniform(-0.6, 0.6)
            rows.append({"datetime": t0 + pd.Timedelta(days=i),
                         "open": c, "high": c + 0.5, "low": c - 0.5,
                         "close": c, "volume": 1500.0})
        for j in range(n_break):
            c = base + 2.5 + j * 1.5
            rows.append({"datetime": t0 + pd.Timedelta(days=n_pre + j),
                         "open": c - 0.5, "high": c + 1.0, "low": c - 0.7,
                         "close": c, "volume": 4000.0})
        bars = pd.DataFrame(rows)
        bt_tl = backtest_on_same_bars(
            strategy_class=DonchianCta,
            vt_symbol="RB0.SHFE",
            setting={"trade_side_mode": "long"},
            bars=bars,
        )
        self.assertIsInstance(bt_tl, pd.DataFrame)
        # 列与 event_driven trade_log 一致
        for col in ("entry_i", "exit_i", "side", "lots"):
            self.assertIn(col, bt_tl.columns)


if __name__ == "__main__":
    unittest.main()
