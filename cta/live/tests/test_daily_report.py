"""cta.live.daily_report 单测。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime
from pathlib import Path

import numpy as np
import pandas as pd

from cta.live.daily_report import DailyReport, write_daily_report


def _live_log() -> pd.DataFrame:
    return pd.DataFrame(
        [
            {"entry_i": 1, "exit_i": 4, "side": "long", "lots": 1,
             "entry_price": 100.0, "exit_price": 102.0,
             "gross_pnl": 20.0, "cost": 2.0, "net_pnl": 18.0},
            {"entry_i": 6, "exit_i": 8, "side": "short", "lots": 1,
             "entry_price": 105.0, "exit_price": 103.0,
             "gross_pnl": 20.0, "cost": 2.0, "net_pnl": 18.0},
        ]
    )


def _equity(n: int = 10) -> pd.Series:
    eq = np.zeros(n)
    eq[4] = 18.0
    eq[8] = 36.0
    return pd.Series(np.maximum.accumulate(eq), name="equity")


def _dates(n: int = 10) -> pd.Series:
    return pd.Series(pd.date_range("2024-01-02", periods=n, freq="D"))


class TestWriteDailyReport(unittest.TestCase):
    def test_returns_daily_report(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = write_daily_report(
                live_trade_log=_live_log(),
                live_equity=_equity(),
                dates=_dates(),
                out_dir=tmp,
                title="day-2024-01-09",
            )
        self.assertIsInstance(res, DailyReport)
        self.assertEqual(res.trades_count, 2)
        self.assertGreater(res.pnl, 0)
        self.assertIn("sharpe", res.metrics)

    def test_writes_markdown_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = write_daily_report(
                live_trade_log=_live_log(),
                live_equity=_equity(),
                dates=_dates(),
                out_dir=tmp,
                title="day-2024-01-09",
            )
            p = Path(res.report_path)
            self.assertTrue(p.exists())
            content = p.read_text(encoding="utf-8")
            self.assertIn("day-2024-01-09", content)
            self.assertIn("trades_count", content)

    def test_parity_section_when_backtest_provided(self) -> None:
        # 回测 trade_log 与 live 一致 → mismatch_rate = 0
        with tempfile.TemporaryDirectory() as tmp:
            res = write_daily_report(
                live_trade_log=_live_log(),
                live_equity=_equity(),
                backtest_trade_log=_live_log(),
                dates=_dates(),
                out_dir=tmp,
                title="parity-test",
            )
        self.assertEqual(res.parity_mismatch_rate, 0.0)

    def test_parity_section_detects_mismatch(self) -> None:
        live = _live_log()
        bt = _live_log()
        bt.loc[0, "side"] = "short"  # 翻转方向 → 失配
        with tempfile.TemporaryDirectory() as tmp:
            res = write_daily_report(
                live_trade_log=live,
                live_equity=_equity(),
                backtest_trade_log=bt,
                dates=_dates(),
                out_dir=tmp,
                title="parity-mismatch",
            )
        self.assertGreater(res.parity_mismatch_rate, 0.0)

    def test_skips_parity_when_no_backtest(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = write_daily_report(
                live_trade_log=_live_log(),
                live_equity=_equity(),
                dates=_dates(),
                out_dir=tmp,
                title="no-bt",
            )
        self.assertIsNone(res.parity_mismatch_rate)


if __name__ == "__main__":
    unittest.main()
