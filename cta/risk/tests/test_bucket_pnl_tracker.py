"""BucketPnlTracker 测试。

覆盖：
1. classify_score_bucket：分桶边界 + 越界
2. on_trade：基础累计 + bp 计算
3. window 滚动：超窗自动 pop
4. trade_count / rolling_pnl_bp
5. save/load roundtrip
6. fail-open：缺 portfolio_equity / equity<=0 / 损坏 JSON
7. replay 批量
"""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.risk.state.bucket_pnl_tracker import (
    BucketKey,
    BucketPnlTracker,
    classify_score_bucket,
)


def _trade(cluster="metal", interval="day", bucket="p70-80",
           dt="2026-01-02 10:00:00", net_pnl=100.0,
           equity=1_000_000.0, symbol="CU0") -> dict:
    return {
        "cluster": cluster, "interval": interval, "score_bucket": bucket,
        "dt": pd.Timestamp(dt), "net_pnl": net_pnl,
        "portfolio_equity": equity, "symbol": symbol,
    }


class TestClassifyScoreBucket(unittest.TestCase):

    def test_below_lowest_edge_returns_none(self) -> None:
        self.assertIsNone(classify_score_bucket(55.0))
        self.assertIsNone(classify_score_bucket(0.0))

    def test_within_first_bucket(self) -> None:
        self.assertEqual(classify_score_bucket(60.0), "p60-70")
        self.assertEqual(classify_score_bucket(69.99), "p60-70")

    def test_at_boundary_uses_next_bucket(self) -> None:
        self.assertEqual(classify_score_bucket(70.0), "p70-80")
        self.assertEqual(classify_score_bucket(80.0), "p80-90")

    def test_above_highest_edge_uses_last_bucket(self) -> None:
        self.assertEqual(classify_score_bucket(100.0), "p90+")
        self.assertEqual(classify_score_bucket(95.0), "p90+")

    def test_none_input_returns_none(self) -> None:
        self.assertIsNone(classify_score_bucket(None))

    def test_non_numeric_returns_none(self) -> None:
        self.assertIsNone(classify_score_bucket("not_a_number"))


class TestBucketPnlTracker(unittest.TestCase):

    def test_on_trade_basic_pnl_bp(self) -> None:
        t = BucketPnlTracker(window_days=30)
        t.on_trade(_trade(net_pnl=100.0, equity=1_000_000.0))
        # 100 / 1e6 = 1e-4 → 1 bp
        key = BucketKey("metal", "day", "p70-80")
        self.assertAlmostEqual(t.rolling_pnl_bp(key, now=pd.Timestamp("2026-01-02 11:00:00")), 1.0, places=4)
        self.assertEqual(t.trade_count(key), 1)

    def test_window_eviction(self) -> None:
        t = BucketPnlTracker(window_days=5)
        t.on_trade(_trade(dt="2026-01-01", net_pnl=100.0))
        t.on_trade(_trade(dt="2026-01-10", net_pnl=200.0))
        key = BucketKey("metal", "day", "p70-80")
        pnl = t.rolling_pnl_bp(key, now=pd.Timestamp("2026-01-10"))
        # 2026-01-01 在 5 天窗口外（与 01-10 间隔 9 天）→ 应被踢
        self.assertAlmostEqual(pnl, 2.0, places=4)
        self.assertEqual(t.trade_count(key, now=pd.Timestamp("2026-01-10")), 1)

    def test_multiple_buckets_isolated(self) -> None:
        t = BucketPnlTracker(window_days=30)
        t.on_trade(_trade(bucket="p70-80", net_pnl=100.0))
        t.on_trade(_trade(bucket="p80-90", net_pnl=-200.0))
        key70 = BucketKey("metal", "day", "p70-80")
        key80 = BucketKey("metal", "day", "p80-90")
        self.assertAlmostEqual(t.rolling_pnl_bp(key70), 1.0, places=4)
        self.assertAlmostEqual(t.rolling_pnl_bp(key80), -2.0, places=4)

    def test_save_load_roundtrip(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            t = BucketPnlTracker(window_days=30, state_path=p)
            t.on_trade(_trade(net_pnl=300.0))
            t.save()
            t2 = BucketPnlTracker.from_path(p, window_days=30)
        key = BucketKey("metal", "day", "p70-80")
        self.assertEqual(t2.trade_count(key, now=pd.Timestamp("2026-01-02 12:00")), 1)
        self.assertAlmostEqual(t2.rolling_pnl_bp(key, now=pd.Timestamp("2026-01-02 12:00")), 3.0, places=4)

    def test_load_missing_file_returns_empty(self) -> None:
        t = BucketPnlTracker.from_path("/nonexistent/state.json", window_days=30)
        self.assertEqual(len(t.known_buckets()), 0)

    def test_load_corrupt_json_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "state.json"
            p.write_text("{not valid", encoding="utf-8")
            t = BucketPnlTracker.from_path(p, window_days=30)
        self.assertEqual(len(t.known_buckets()), 0)

    def test_zero_equity_skips_trade(self) -> None:
        t = BucketPnlTracker(window_days=30)
        t.on_trade(_trade(equity=0.0))
        self.assertEqual(t.trade_count(BucketKey("metal", "day", "p70-80")), 0)

    def test_replay_batch(self) -> None:
        t = BucketPnlTracker(window_days=30)
        trades = [
            _trade(dt=f"2026-01-{d:02d}", net_pnl=50.0 * d)
            for d in (1, 2, 3, 4, 5)
        ]
        n = t.replay(trades)
        self.assertEqual(n, 5)
        key = BucketKey("metal", "day", "p70-80")
        # 50 + 100 + 150 + 200 + 250 = 750 → 750 / 1e6 * 1e4 = 7.5 bp
        self.assertAlmostEqual(t.rolling_pnl_bp(key, now=pd.Timestamp("2026-01-05")), 7.5, places=4)

    def test_purge_expired_returns_count(self) -> None:
        t = BucketPnlTracker(window_days=2)
        t.on_trade(_trade(dt="2026-01-01"))
        t.on_trade(_trade(dt="2026-01-02"))
        t.on_trade(_trade(dt="2026-01-10"))
        n_purged = t.purge_expired(now=pd.Timestamp("2026-01-10"))
        # 01-01 / 01-02 都过期（距 01-10 超 2 天）
        self.assertEqual(n_purged, 2)

    def test_bucket_key_str_roundtrip(self) -> None:
        k = BucketKey("metal", "day", "p70-80")
        self.assertEqual(BucketKey.from_str(k.to_str()), k)


if __name__ == "__main__":
    unittest.main()
