"""daily_review.py tests."""
from __future__ import annotations

import tempfile
import unittest

import pandas as pd

from cta.skills.live_ops.daily_review import (
    ReviewConfig,
    build_daily_review,
    compare_live_vs_backtest,
)


class TestDailyReview(unittest.TestCase):
    def test_compare_live_vs_backtest(self) -> None:
        live = pd.DataFrame({"trade_id": [1, 2], "net_pnl": [10.0, -3.0]})
        bt = pd.DataFrame({"trade_id": [1, 2], "net_pnl": [8.0, -2.0]})
        diff = compare_live_vs_backtest(live, bt)
        self.assertIn("diff", diff.columns)
        self.assertEqual(len(diff), 2)

    def test_build_daily_review(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            path = build_daily_review(
                date=pd.Timestamp("2024-01-01"),
                trade_log_today=pd.DataFrame({"trade_id": [1], "net_pnl": [10.0]}),
                equity_today=pd.Series([1000.0, 1010.0]),
                alerts_today=pd.DataFrame({"level": ["warn"], "msg": ["x"]}),
                cfg=ReviewConfig(out_dir=tmpdir, include_charts=False),
            )
            self.assertTrue(path.endswith(".md"))


if __name__ == "__main__":
    unittest.main()

