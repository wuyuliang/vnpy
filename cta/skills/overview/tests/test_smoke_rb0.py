"""端到端烟雾测试：
- 用真实 RB0 的 day OHLCV 合成一个 'shift(-1) close' 信号（明显 lookahead），
  verifying detect_lookahead 能检出
- 构造一个假的 summary.csv，跑 assess_against_gates，写到 cta/skills/output/

不依赖 feature 落盘；只用 cta/data/origin/day/RB0.csv。
"""
from __future__ import annotations

import unittest
from pathlib import Path

import pandas as pd

from cta.skills import OUTPUT_DIR
from cta.skills.overview.acceptance import assess_against_gates
from cta.skills.overview.backtest_principles import detect_lookahead

DAY_CSV = (
    Path(__file__).resolve().parent.parent.parent.parent
    / "data" / "origin" / "day" / "RB0.csv"
)


class TestSmokeRB0(unittest.TestCase):
    def setUp(self) -> None:
        if not DAY_CSV.exists():
            self.skipTest(f"no RB0 day csv at {DAY_CSV}")
        self.df = pd.read_csv(DAY_CSV, encoding="utf-8-sig",
                              parse_dates=["datetime"])

    def test_detects_lookahead_on_real_rb0(self) -> None:
        df = self.df.copy()
        df["signal_peek"] = (df["close"].shift(-3) > df["close"]).astype(int)
        vios = detect_lookahead(df, "signal_peek", corr_threshold=0.15)
        self.assertTrue(vios, "RB0 下 shift(-3) 应检出 lookahead")

    def test_gate_assessment_writes_file(self) -> None:
        # 合成一个跨频率的 summary
        summary = pd.DataFrame([
            # day 走 gate A
            {"symbol": "RB0", "interval": "day",
             "annual_return": 0.25, "max_drawdown": 0.10,
             "sharpe": 1.5, "calmar": 2.5, "trade_count": 120,
             "win_rate": 0.45, "profit_factor": 1.4,
             "total_return": 1.3, "avg_holding_bars": 30, "turnover": 20},
            # minute5 走 gate B
            {"symbol": "RB0", "interval": "minute5",
             "annual_return": 0.05, "max_drawdown": 0.20,
             "sharpe": 0.8, "calmar": 0.25, "trade_count": 500,
             "win_rate": 0.40, "profit_factor": 1.1,
             "total_return": 0.1, "avg_holding_bars": 10, "turnover": 100},
        ])
        res = assess_against_gates(summary)
        # 第一行应过，第二行应挂
        self.assertEqual(int(res.loc[0, "passed"]), 1)
        self.assertEqual(int(res.loc[1, "passed"]), 0)

        # 产物落盘
        out = OUTPUT_DIR / "smoke_rb0_gate_assess.csv"
        OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
        res.to_csv(out, index=False)
        self.assertTrue(out.exists())
        self.addCleanup(lambda: out.unlink(missing_ok=True))


if __name__ == "__main__":
    unittest.main()
