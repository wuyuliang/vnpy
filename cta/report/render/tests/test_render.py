"""cta/report/render 子模块单测。"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.report.render import (
    HtmlReportConfig,
    block_bootstrap,
    capacity_curve,
    extended_metrics,
    write_html_report,
)
from cta.report.render.factor_analysis import ALPHALENS_AVAILABLE, run_alphalens
from cta.report.render.plots import (
    drawdown_fig,
    equity_curve_fig,
    exposure_fig,
    monthly_heatmap_fig,
    trade_pnl_hist_fig,
)


def _fixture(seed: int = 0) -> tuple[pd.DataFrame, pd.DataFrame, pd.Series]:
    rng = np.random.default_rng(seed)
    n = 200
    dates = pd.date_range("2024-01-01", periods=n, freq="D")
    rets = rng.normal(0.0, 1.0, size=n).cumsum()
    bars = pd.DataFrame(
        {
            "datetime": dates,
            "open": 100 + rets,
            "high": 101 + rets,
            "low": 99 + rets,
            "close": 100.5 + rets,
            "volume": rng.integers(1000, 5000, size=n).astype(float),
        }
    )
    trade_log = pd.DataFrame(
        [
            {"entry_i": 5, "exit_i": 12, "side": "long", "lots": 2,
             "entry_price": float(bars["close"].iloc[5]),
             "exit_price": float(bars["close"].iloc[12]),
             "gross_pnl": 50.0, "cost": 5.0, "net_pnl": 45.0},
            {"entry_i": 30, "exit_i": 45, "side": "short", "lots": 1,
             "entry_price": float(bars["close"].iloc[30]),
             "exit_price": float(bars["close"].iloc[45]),
             "gross_pnl": -20.0, "cost": 3.0, "net_pnl": -23.0},
            {"entry_i": 80, "exit_i": 95, "side": "long", "lots": 1,
             "entry_price": float(bars["close"].iloc[80]),
             "exit_price": float(bars["close"].iloc[95]),
             "gross_pnl": 30.0, "cost": 3.0, "net_pnl": 27.0},
        ]
    )
    pnl_per_bar = pd.Series(np.zeros(n), name="equity")
    pnl_per_bar.iloc[12] = 45.0
    pnl_per_bar.iloc[45] = -23.0
    pnl_per_bar.iloc[95] = 27.0
    equity = pnl_per_bar.cumsum()
    return trade_log, bars, equity


class TestExtendedMetrics(unittest.TestCase):
    def test_keys(self) -> None:
        trade_log, bars, equity = _fixture()
        m = extended_metrics(trade_log, equity, dates=bars["datetime"].tolist())
        for k in (
            "sharpe", "sortino", "calmar", "mdd", "winrate", "pf",
            "mdd_duration", "win_streak", "lose_streak",
            "avg_holding_bars", "turnover_per_bar", "monthly_pnl",
        ):
            self.assertIn(k, m)

    def test_monthly_keys(self) -> None:
        trade_log, bars, equity = _fixture()
        m = extended_metrics(trade_log, equity, dates=bars["datetime"].tolist())
        self.assertIsInstance(m["monthly_pnl"], dict)
        # 至少应该有一个月份键
        self.assertGreater(len(m["monthly_pnl"]), 0)

    def test_streaks(self) -> None:
        trade_log, _, equity = _fixture()
        m = extended_metrics(trade_log, equity)
        # 三笔交易：win, loss, win → 最长连胜 1，最长连亏 1
        self.assertEqual(m["win_streak"], 1)
        self.assertEqual(m["lose_streak"], 1)


class TestPlots(unittest.TestCase):
    def test_figs_returned(self) -> None:
        trade_log, bars, equity = _fixture()
        self.assertIsNotNone(equity_curve_fig(equity, dates=bars["datetime"].tolist()))
        self.assertIsNotNone(drawdown_fig(equity))
        self.assertIsNotNone(trade_pnl_hist_fig(trade_log))
        self.assertIsNotNone(exposure_fig(trade_log, n_bars=len(equity)))

    def test_monthly_heatmap_handles_empty(self) -> None:
        self.assertIsNone(monthly_heatmap_fig({}))


class TestMonteCarlo(unittest.TestCase):
    def test_block_bootstrap_basic(self) -> None:
        rng = np.random.default_rng(0)
        rets = rng.normal(0.0, 1.0, size=200)
        res = block_bootstrap(rets, n_iter=100, block_size=5, seed=42)
        self.assertEqual(res.n_iter, 100)
        self.assertEqual(res.samples_final_equity.size, 100)
        self.assertEqual(res.samples_max_dd.size, 100)
        for q in (0.05, 0.5, 0.95):
            self.assertIn(q, res.final_equity)
            self.assertIn(q, res.max_drawdown)

    def test_empty_input(self) -> None:
        res = block_bootstrap(pd.Series([], dtype=float), n_iter=10)
        self.assertEqual(res.n_iter, 0)


class TestCapacity(unittest.TestCase):
    def test_capacity_curve_shape(self) -> None:
        trade_log, bars, _ = _fixture()
        cap = capacity_curve(
            trade_log,
            bars,
            capital_levels=[1e5, 1e6, 1e7],
            multiplier=10.0,
            risk_per_trade=0.01,
            liquidity_ratio=0.1,
        )
        self.assertEqual(len(cap), 3)
        self.assertIn("total_pnl", cap.columns)
        self.assertIn("fill_ratio", cap.columns)
        self.assertIn("n_capped", cap.columns)

    def test_capacity_empty_trades(self) -> None:
        empty = pd.DataFrame(columns=["entry_i", "lots", "entry_price", "net_pnl"])
        bars = pd.DataFrame({"volume": [1.0, 2.0, 3.0]})
        cap = capacity_curve(
            empty, bars, capital_levels=[1e5], multiplier=10.0
        )
        self.assertEqual(len(cap), 0)


class TestFactorAnalysis(unittest.TestCase):
    def test_alphalens_optional(self) -> None:
        # 当 alphalens 缺失时，应返回 available=False 而非抛错。
        if not ALPHALENS_AVAILABLE:
            res = run_alphalens(
                factor=pd.Series(dtype=float),
                prices=pd.DataFrame(),
            )
            self.assertFalse(res["available"])
            self.assertIn("alphalens", res["error"])


class TestHtmlReport(unittest.TestCase):
    def test_write_html_report(self) -> None:
        trade_log, bars, equity = _fixture()
        with tempfile.TemporaryDirectory() as tmp:
            cfg = HtmlReportConfig(
                out_dir=tmp,
                title="UnitTest",
                monte_carlo_iter=50,
                capacity_levels=(1e5, 1e6),
                capacity_multiplier=10.0,
            )
            out = write_html_report(
                trade_log=trade_log,
                equity=equity,
                bars=bars,
                cfg=cfg,
            )
            p = Path(out)
            self.assertTrue(p.exists())
            self.assertGreater(p.stat().st_size, 1000)
            content = p.read_text(encoding="utf-8")
            self.assertIn("UnitTest", content)
            self.assertIn("Equity curve", content)
            # 同目录应有 metrics_*.json
            jsons = list(Path(tmp).glob("metrics_*.json"))
            self.assertEqual(len(jsons), 1)


if __name__ == "__main__":
    unittest.main()
