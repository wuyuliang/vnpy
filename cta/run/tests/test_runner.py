"""cta.run.runner 单测（TDD：先于实现）。"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.run.runner import RunnerResult, run_event_driven_backtest
from cta.skills.data_backtest.event_driven_backtest import EngineConfig
from cta.skills.data_backtest.transaction_cost import estimate_cost


class _DummyLongStrategy:
    """简单策略：第 i=0 开多，第 i=5 平仓。"""

    def on_bar(self, i: int, bar: pd.Series, position: int) -> list[dict]:
        sym_meta = {
            "symbol": "X0.DCE",
            "multiplier": 10.0,
            "commission_rate": 0.0001,
            "tick_size": 1.0,
        }
        if i == 0 and position == 0:
            return [{"side": "long", "lots": 1, "order_type": "market", **sym_meta}]
        if i == 5 and position > 0:
            return [{"side": "flat", "lots": 1, "order_type": "market", **sym_meta}]
        return []


def _bars(n: int = 30, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(seed)
    closes = 100 + rng.normal(0.5, 1.0, size=n).cumsum()
    return pd.DataFrame(
        {
            "datetime": pd.date_range("2024-01-02", periods=n, freq="D"),
            "open": closes - 0.3,
            "high": closes + 0.5,
            "low": closes - 0.5,
            "close": closes,
            "volume": rng.integers(1000, 5000, size=n).astype(float),
        }
    )


class TestRunner(unittest.TestCase):
    def test_returns_runner_result(self) -> None:
        bars = _bars()
        with tempfile.TemporaryDirectory() as tmp:
            res = run_event_driven_backtest(
                strategy=_DummyLongStrategy(),
                bars=bars,
                out_dir=tmp,
                title="dummy-long",
            )
        self.assertIsInstance(res, RunnerResult)
        self.assertIsInstance(res.trade_log, pd.DataFrame)
        self.assertIsInstance(res.equity_curve, pd.Series)
        self.assertIsInstance(res.stats, dict)
        self.assertGreaterEqual(len(res.trade_log), 1)
        self.assertGreater(len(res.equity_curve), 0)

    def test_writes_html_report(self) -> None:
        bars = _bars()
        with tempfile.TemporaryDirectory() as tmp:
            res = run_event_driven_backtest(
                strategy=_DummyLongStrategy(),
                bars=bars,
                out_dir=tmp,
                title="dummy-long",
            )
            self.assertTrue(Path(res.report_path).exists())
            self.assertGreater(Path(res.report_path).stat().st_size, 1000)
            # metrics_*.json 应有
            jsons = list(Path(tmp).glob("metrics_*.json"))
            self.assertEqual(len(jsons), 1)
            metrics = json.loads(jsons[0].read_text(encoding="utf-8"))
            self.assertIn("sortino", metrics)

    def test_engine_cfg_propagates(self) -> None:
        """传入 limit_move_pct/liquidity_ratio 时引擎应使用它们。"""
        bars = _bars()
        # 把第 1 根 bar 改成一字涨停板（>= 7%）
        bars.loc[1, "open"] = bars.loc[0, "close"] * 1.10
        bars.loc[1, "high"] = bars.loc[1, "open"]
        bars.loc[1, "low"] = bars.loc[1, "open"]
        bars.loc[1, "close"] = bars.loc[1, "open"]
        with tempfile.TemporaryDirectory() as tmp:
            res = run_event_driven_backtest(
                strategy=_DummyLongStrategy(),
                bars=bars,
                engine_cfg=EngineConfig(limit_move_pct=0.07),
                out_dir=tmp,
                title="limit-up-blocked",
            )
        # 第 0 根开多被一字板拒绝 → 没有交易
        self.assertEqual(len(res.trade_log), 0)

    def test_cost_fn_applied(self) -> None:
        """显式传入 cost_fn 时，net_pnl 与 gross_pnl 之差应等于 cost。"""
        bars = _bars()
        with tempfile.TemporaryDirectory() as tmp:
            res = run_event_driven_backtest(
                strategy=_DummyLongStrategy(),
                bars=bars,
                cost_fn=estimate_cost,
                out_dir=tmp,
                title="with-cost",
            )
        self.assertGreaterEqual(len(res.trade_log), 1)
        row = res.trade_log.iloc[0]
        self.assertAlmostEqual(
            float(row["gross_pnl"]) - float(row["net_pnl"]), float(row["cost"]), places=6
        )
        self.assertGreater(float(row["cost"]), 0.0)


if __name__ == "__main__":
    unittest.main()
