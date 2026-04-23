"""trade_evaluation.py tests."""
from __future__ import annotations

import tempfile
import unittest

import pandas as pd

from cta.skills.data_backtest.trade_evaluation import (
    ReportConfig,
    summarize_trades,
    write_report,
)


class TestTradeEvaluation(unittest.TestCase):
    def test_summarize_trades(self) -> None:
        trade_log = pd.DataFrame(
            {
                "net_pnl": [100.0, -50.0, 120.0, -20.0],
                "gross_pnl": [110.0, -40.0, 130.0, -10.0],
            }
        )
        equity = pd.Series([10000.0, 10100.0, 10050.0, 10170.0, 10150.0])
        s = summarize_trades(trade_log, equity)
        self.assertIn("total_pnl", s)
        self.assertIn("sharpe", s)

    def test_write_report(self) -> None:
        trade_log = pd.DataFrame({"net_pnl": [10.0, -5.0], "gross_pnl": [12.0, -4.0]})
        equity = pd.Series([1000.0, 1010.0, 1005.0])
        with tempfile.TemporaryDirectory() as tmpdir:
            path = write_report(
                trade_log=trade_log,
                equity=equity,
                cfg=ReportConfig(out_dir=tmpdir, include_plots=False),
            )
            self.assertTrue(path.endswith(".md"))


if __name__ == "__main__":
    unittest.main()

