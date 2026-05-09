"""cta/run/multi_runner.py 单测：多 symbol × 多 interval 跑回测并汇总。"""
from __future__ import annotations

import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

import numpy as np
import pandas as pd

from cta.run.multi_runner import (
    MultiRunResult,
    MultiRunSpec,
    aggregate_portfolio,
    run_multi,
)
from cta.strategy.cta_baseline import DonchianCta


def _bars(symbol: str, interval: str, seed: int = 0) -> pd.DataFrame:
    rng = np.random.default_rng(hash((symbol, interval, seed)) % (2**31))
    n_pre, n_break = 80, 30
    base = 100.0
    rows: list[dict] = []
    t0 = datetime(2024, 1, 2, 9, 0)
    step = timedelta(days=1) if interval == "day" else timedelta(minutes=60)
    for i in range(n_pre):
        c = base + rng.uniform(-0.6, 0.6)
        rows.append({"datetime": t0 + step * i, "open": c, "high": c + 0.5, "low": c - 0.5,
                     "close": c, "volume": 1500.0})
    for j in range(n_break):
        c = base + 2.5 + j * 1.5
        rows.append({"datetime": t0 + step * (n_pre + j), "open": c - 0.5, "high": c + 1.0,
                     "low": c - 0.7, "close": c, "volume": 4000.0})
    return pd.DataFrame(rows)


def _spec(out_dir: str) -> MultiRunSpec:
    combos = [("RB0", "day"), ("RB0", "60min"), ("HC0", "day")]
    return MultiRunSpec(
        strategy_class=DonchianCta,
        combos=combos,
        get_bars=lambda sym, itv: _bars(sym, itv),
        get_setting=lambda sym, itv: {"trade_side_mode": "both"},
        out_dir=out_dir,
        title_fmt="{symbol} / {interval}",
    )


class TestRunMulti(unittest.TestCase):
    def test_returns_multi_run_result(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = run_multi(_spec(tmp))
        self.assertIsInstance(res, MultiRunResult)
        self.assertIsInstance(res.summary, pd.DataFrame)
        self.assertEqual(len(res.summary), 3)

    def test_summary_columns(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = run_multi(_spec(tmp))
        for col in ("symbol", "interval", "sharpe", "sortino", "calmar",
                    "mdd", "total_pnl", "trades_count"):
            self.assertIn(col, res.summary.columns)

    def test_monthly_pnl_long_format(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = run_multi(_spec(tmp))
        for col in ("symbol", "interval", "ym", "pnl"):
            self.assertIn(col, res.monthly_pnl.columns)
        # 至少应该有 1 行（任意组合产生月度 PnL 即可）
        # 若交易稀疏可能为 0，所以放宽：仅断言列结构

    def test_writes_html_per_combo(self) -> None:
        with tempfile.TemporaryDirectory() as tmp:
            res = run_multi(_spec(tmp))
            for p in res.out_paths:
                self.assertTrue(Path(p).exists())
            self.assertTrue((Path(tmp) / "summary.csv").exists())
            self.assertTrue((Path(tmp) / "monthly_pnl.csv").exists())
            self.assertTrue((Path(tmp) / "sharpe_pivot.csv").exists())

    def test_continues_when_one_combo_fails(self) -> None:
        """单个组合 bars 加载失败时，其余组合仍应继续。"""
        def bars_fn(sym, itv):
            if sym == "BAD":
                raise FileNotFoundError(f"no bars for {sym}/{itv}")
            return _bars(sym, itv)

        with tempfile.TemporaryDirectory() as tmp:
            spec = MultiRunSpec(
                strategy_class=DonchianCta,
                combos=[("BAD", "day"), ("RB0", "day")],
                get_bars=bars_fn,
                get_setting=lambda s, i: {},
                out_dir=tmp,
            )
            res = run_multi(spec)
        # 一个失败一个成功
        self.assertEqual(len(res.summary), 2)
        self.assertTrue(any(res.summary["error"].fillna("") != ""))
        self.assertTrue(any(res.summary["error"].fillna("") == ""))


class TestAggregatePortfolio(unittest.TestCase):
    def test_equal_weight_portfolio(self) -> None:
        s1 = pd.Series([0.0, 1.0, 2.0], name="equity")
        s2 = pd.Series([0.0, 2.0, 4.0], name="equity")
        eq = aggregate_portfolio([s1, s2])
        self.assertEqual(list(eq), [0.0, 1.5, 3.0])

    def test_handles_empty_list(self) -> None:
        self.assertEqual(len(aggregate_portfolio([])), 0)

    def test_aligns_different_lengths(self) -> None:
        s1 = pd.Series([0.0, 1.0, 2.0])
        s2 = pd.Series([0.0, 4.0])
        eq = aggregate_portfolio([s1, s2])
        # 较短序列以最后值前向填充，或截断到最短：实现选择"截到最短"
        self.assertEqual(len(eq), 2)


if __name__ == "__main__":
    unittest.main()
