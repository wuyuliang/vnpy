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


    def test_periods_per_year_affects_sharpe(self) -> None:
        """periods_per_year 参数应控制 sqrt 年化系数：分钟线应放大。"""
        trade_log = pd.DataFrame({"net_pnl": [1.0] * 100})
        equity = pd.Series([float(i) for i in range(1, 101)])  # 稳定每 bar +1
        # day 级 (252)
        s_day = summarize_trades(trade_log, equity, periods_per_year=252)
        # minute60 级 (约 252 * 5 = 1260)
        s_min = summarize_trades(trade_log, equity, periods_per_year=1260)
        self.assertAlmostEqual(
            s_min["sharpe"] / s_day["sharpe"],
            (1260.0 / 252.0) ** 0.5,
            places=5,
            msg="periods_per_year 应影响 sharpe 的年化系数",
        )

    def test_annualized_uses_periods_per_year(self) -> None:
        """annualized 收益率应使用 periods_per_year 换算。"""
        trade_log = pd.DataFrame({"net_pnl": [1.0] * 252})
        equity = pd.Series([1000.0 + i for i in range(253)])
        s_day = summarize_trades(trade_log, equity, periods_per_year=252)
        s_min15 = summarize_trades(trade_log, equity, periods_per_year=252 * 16)
        # 同样长度（253 bar）在 day 下 ≈ 1 年，在 15min 下仅 ~1/16 年
        # annualized_day 应远小于 annualized_min15（同样总收益，年化后放大）
        self.assertGreater(s_min15["annualized"], s_day["annualized"])


if __name__ == "__main__":
    unittest.main()

