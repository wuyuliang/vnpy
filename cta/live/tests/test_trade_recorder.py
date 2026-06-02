"""cta.live.trade_recorder 单测。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from types import SimpleNamespace

import pandas as pd

from cta.live.trade_recorder import TradeRecorder


def _trade(direction: str = "long", offset: str = "open",
           price: float = 100.0, volume: float = 1.0,
           dt: datetime | None = None) -> SimpleNamespace:
    return SimpleNamespace(
        gateway_name="CTP",
        symbol="rb888",
        exchange=SimpleNamespace(value="SHFE"),
        orderid="o1",
        tradeid="t1",
        direction=SimpleNamespace(value=direction),
        offset=SimpleNamespace(value=offset),
        price=price,
        volume=volume,
        datetime=dt or datetime(2024, 1, 2, 9, 30),
    )


class TestTradeRecorder(unittest.TestCase):
    def test_record_appends(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = TradeRecorder(out_dir=tmp, vt_symbol="rb888.SHFE")
            r.record(_trade(price=100.0, volume=1))
            r.record(_trade(price=101.0, volume=2, direction="short", offset="close"))
            df = r.to_dataframe()
            self.assertEqual(len(df), 2)
            for col in ("datetime", "vt_symbol", "direction", "offset", "price", "volume", "tradeid"):
                self.assertIn(col, df.columns)
            self.assertEqual(df.iloc[0]["price"], 100.0)
            self.assertEqual(df.iloc[1]["volume"], 2)

    def test_flush_writes_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = TradeRecorder(out_dir=tmp, vt_symbol="rb888.SHFE")
            r.record(_trade())
            path = r.flush()
            p = Path(path)
            self.assertTrue(p.exists())
            self.assertTrue(p.suffix == ".parquet")
            df = pd.read_parquet(p)
            self.assertEqual(len(df), 1)

    def test_flush_idempotent_when_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = TradeRecorder(out_dir=tmp, vt_symbol="rb888.SHFE")
            self.assertEqual(r.flush(), "")  # nothing to write

    def test_to_trade_log_shape(self) -> None:
        """to_trade_log 输出与回测 trade_log 列结构对齐：
        side / lots / entry_price / exit_price / gross_pnl / cost / net_pnl.
        多笔 open+close 应配对成 1 条 trade_log 行。"""
        with tempfile.TemporaryDirectory() as tmp:
            r = TradeRecorder(out_dir=tmp, vt_symbol="rb888.SHFE")
            r.record(_trade(direction="long",  offset="open",  price=100.0, volume=1,
                            dt=datetime(2024, 1, 2, 9, 30)))
            r.record(_trade(direction="short", offset="close", price=105.0, volume=1,
                            dt=datetime(2024, 1, 2, 14, 30)))
            tl = r.to_trade_log(multiplier=10.0)
            self.assertEqual(len(tl), 1)
            row = tl.iloc[0]
            self.assertEqual(row["side"], "long")
            self.assertEqual(row["lots"], 1)
            self.assertEqual(row["entry_price"], 100.0)
            self.assertEqual(row["exit_price"], 105.0)
            # gross_pnl = (105-100) * 1 * 10 = 50
            self.assertAlmostEqual(row["gross_pnl"], 50.0)

    def test_record_converts_aware_datetime_to_naive_shanghai(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = TradeRecorder(out_dir=tmp, vt_symbol="rb888.SHFE")
            aware_utc = datetime(2024, 1, 2, 1, 30, tzinfo=timezone.utc)
            r.record(_trade(dt=aware_utc))
            df = r.to_dataframe()
            ts = pd.Timestamp(df.iloc[0]["datetime"])
            self.assertIsNone(ts.tzinfo)
            self.assertEqual(ts, pd.Timestamp("2024-01-02 09:30:00"))

    def test_record_missing_datetime_falls_back_to_naive_timestamp(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            r = TradeRecorder(out_dir=tmp, vt_symbol="rb888.SHFE")
            tr = _trade()
            tr.datetime = None
            r.record(tr)
            ts = pd.Timestamp(r.to_dataframe().iloc[0]["datetime"])
            self.assertIsNone(ts.tzinfo)


if __name__ == "__main__":
    unittest.main()
