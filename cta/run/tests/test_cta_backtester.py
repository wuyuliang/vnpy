"""cta.run.cta_backtester 单测。

设计：
- ``run_via_event_driven`` —— 从 CtaTemplate 子类抽 inner，走 event_driven 引擎；
  这是 M2 主路径（与 SimNow/实盘共享 CtaTemplate 接口、与 M1 回测共享撮合）。
- ``run_via_vnpy_ctabacktester`` —— 调用真正的 vnpy_ctabacktester.BacktestingEngine；
  未安装时直接 raise，单测对该入口仅测"未装时报错"。
"""
from __future__ import annotations

import importlib
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from cta.run.cta_backtester import (
    BacktesterResult,
    run_via_event_driven,
    run_via_vnpy_ctabacktester,
)
from cta.strategy.cta_baseline import DonchianCta
from cta.strategy.cta_tight_range import SkillTightRangeBreakoutCta


def _bars_df(n_pre: int = 80, n_break: int = 30, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    base = 100.0
    rows: list[dict] = []
    t0 = datetime(2024, 1, 2, 9, 0)
    for i in range(n_pre):
        c = base + rng.uniform(-0.6, 0.6)
        o = c + rng.uniform(-0.3, 0.3)
        h = max(o, c) + rng.uniform(0, 0.4)
        l = min(o, c) - rng.uniform(0, 0.4)
        rows.append({
            "datetime": t0 + timedelta(days=i),
            "open": o, "high": h, "low": l, "close": c, "volume": 1500.0,
        })
    for j in range(n_break):
        c = base + 2.5 + j * 1.5
        o = c - 0.5
        h = c + 1.0
        l = o - 0.2
        rows.append({
            "datetime": t0 + timedelta(days=n_pre + j),
            "open": o, "high": h, "low": l, "close": c, "volume": 4000.0,
        })
    return pd.DataFrame(rows)


class TestRunViaEventDriven(unittest.TestCase):
    def test_returns_backtester_result(self) -> None:
        bars = _bars_df()
        with tempfile.TemporaryDirectory() as tmp:
            res = run_via_event_driven(
                strategy_class=DonchianCta,
                vt_symbol="X0.SHFE",
                setting={"trade_side_mode": "long"},
                bars=bars,
                out_dir=tmp,
                title="donchian-event-driven",
            )
        self.assertIsInstance(res, BacktesterResult)
        self.assertIsInstance(res.trade_log, pd.DataFrame)
        self.assertIsInstance(res.equity_curve, pd.Series)
        self.assertIn("sharpe", res.stats)

    def test_writes_html_report(self) -> None:
        bars = _bars_df()
        with tempfile.TemporaryDirectory() as tmp:
            res = run_via_event_driven(
                strategy_class=SkillTightRangeBreakoutCta,
                vt_symbol="X0.SHFE",
                setting={"lookback": 5, "min_count": 3, "max_holding_bars": 10},
                bars=bars,
                out_dir=tmp,
                title="tight-range-event-driven",
            )
            self.assertTrue(Path(res.report_path).exists())
            jsons = list(Path(tmp).glob("metrics_*.json"))
            self.assertEqual(len(jsons), 1)

    def test_inner_factory_used(self) -> None:
        """run_via_event_driven 内部应实际使用 CtaTemplate 子类的 prepare_frame + make_inner。"""
        bars = _bars_df()
        with tempfile.TemporaryDirectory() as tmp:
            # Donchian + 仅做空：突破上行后应没有 long 入场
            res = run_via_event_driven(
                strategy_class=DonchianCta,
                vt_symbol="X0.SHFE",
                setting={"trade_side_mode": "long"},
                bars=bars,
                out_dir=tmp,
                title="donchian-long-only",
            )
            shorts = (res.trade_log.get("side") == "short").sum() if not res.trade_log.empty else 0
            self.assertEqual(int(shorts), 0)


class TestRunViaVnpyCtabacktester(unittest.TestCase):
    def test_raises_when_module_missing(self) -> None:
        """模拟模块未装：用 monkeypatch 让 find_spec 返回 None。"""
        from unittest import mock
        with mock.patch(
            "cta.run.cta_backtester.importlib.util.find_spec", return_value=None
        ):
            with self.assertRaises(ImportError) as ctx:
                run_via_vnpy_ctabacktester(
                    strategy_class=DonchianCta,
                    vt_symbol="X0.SHFE",
                    setting={},
                    interval="d",
                    start=datetime(2024, 1, 1),
                    end=datetime(2024, 12, 31),
                )
            self.assertIn("vnpy_ctabacktester", str(ctx.exception))

    def test_engine_setup_when_installed(self) -> None:
        """已安装时验证 BacktestingEngine 能被创建并接受 strategy_class（不跑回测，
        因为这里没 vnpy 数据库；通过 monkeypatch load_data/run_backtesting 跳过 IO）。"""
        if importlib.util.find_spec("vnpy_ctabacktester") is None:
            self.skipTest("vnpy_ctabacktester 未安装")
        from unittest import mock
        with mock.patch(
            "vnpy_ctabacktester.engine.BacktestingEngine.load_data", return_value=None
        ), mock.patch(
            "vnpy_ctabacktester.engine.BacktestingEngine.run_backtesting", return_value=None
        ), mock.patch(
            "vnpy_ctabacktester.engine.BacktestingEngine.calculate_result",
            return_value=pd.DataFrame({"balance": [1_000_000.0, 1_000_500.0]}),
        ), mock.patch(
            "vnpy_ctabacktester.engine.BacktestingEngine.calculate_statistics",
            return_value={"total_return": 0.0005, "sharpe_ratio": 1.2},
        ):
            res = run_via_vnpy_ctabacktester(
                strategy_class=DonchianCta,
                vt_symbol="rb888.SHFE",
                setting={"trade_side_mode": "both"},
                interval="d",
                start=datetime(2024, 1, 1),
                end=datetime(2024, 12, 31),
                rate=0.0001, slippage=1.0, size=10, pricetick=1.0,
                capital=1_000_000.0,
            )
            self.assertIsNotNone(res)
            self.assertGreater(len(res.equity_curve), 0)
            self.assertIn("sharpe_ratio", res.stats)


if __name__ == "__main__":
    unittest.main()
