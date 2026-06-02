"""PredictionStaleGuard 测试。"""
from __future__ import annotations

import os
import tempfile
import time
import unittest
from pathlib import Path

import pandas as pd

from cta.live.risk import RiskContext
from cta.risk.guards.config import PredictionStaleGuardConfig
from cta.risk.guards.prediction_stale_guard import PredictionStaleGuard


def _ctx(now: pd.Timestamp | None = None) -> RiskContext:
    return RiskContext(
        pos={}, daily_pnl=0.0, capital=1_000_000.0,
        now=now if now is not None else pd.Timestamp.now(),
    )


def _order(offset: str = "open") -> dict:
    return {"symbol": "RB0", "direction": "long", "offset": offset, "price": 3000.0}


class TestPredictionStaleGuard(unittest.TestCase):

    def test_no_path_fails_open(self) -> None:
        # 默认 cfg.predictions_path="" → 直接放行
        guard = PredictionStaleGuard()
        decision = guard.check(_order(), _ctx())
        self.assertTrue(decision.allowed)

    def test_missing_predictions_file_fails_open(self) -> None:
        cfg = PredictionStaleGuardConfig(predictions_path="/nonexistent/predictions.csv")
        guard = PredictionStaleGuard(cfg)
        decision = guard.check(_order(), _ctx())
        self.assertTrue(decision.allowed)

    def test_fresh_predictions_passes(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "predictions.csv"
            p.write_text("symbol,prob\nRB0,0.65\n", encoding="utf-8")
            cfg = PredictionStaleGuardConfig(
                predictions_path=str(p), max_stale_hours=4.0, cache_seconds=0.0,
            )
            guard = PredictionStaleGuard(cfg)
            decision = guard.check(_order(), _ctx(pd.Timestamp.now()))
        self.assertTrue(decision.allowed)

    def test_stale_predictions_blocks_open(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "predictions.csv"
            p.write_text("symbol,prob\nRB0,0.65\n", encoding="utf-8")
            # 把 mtime 调到 10 小时前
            old_ts = time.time() - 10 * 3600
            os.utime(p, (old_ts, old_ts))
            cfg = PredictionStaleGuardConfig(
                predictions_path=str(p), max_stale_hours=4.0, cache_seconds=0.0,
            )
            guard = PredictionStaleGuard(cfg)
            decision = guard.check(_order(), _ctx(pd.Timestamp.now()))
        self.assertFalse(decision.allowed)
        self.assertIn("predictions_age", decision.reason)

    def test_stale_predictions_allows_close_by_default(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "predictions.csv"
            p.write_text("symbol\nRB0\n", encoding="utf-8")
            old_ts = time.time() - 10 * 3600
            os.utime(p, (old_ts, old_ts))
            cfg = PredictionStaleGuardConfig(
                predictions_path=str(p), max_stale_hours=4.0, cache_seconds=0.0,
            )
            guard = PredictionStaleGuard(cfg)
            decision = guard.check(_order(offset="close"), _ctx(pd.Timestamp.now()))
        self.assertTrue(decision.allowed)

    def test_stale_close_blocked_when_enabled(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "predictions.csv"
            p.write_text("symbol\nRB0\n", encoding="utf-8")
            old_ts = time.time() - 10 * 3600
            os.utime(p, (old_ts, old_ts))
            cfg = PredictionStaleGuardConfig(
                predictions_path=str(p), max_stale_hours=4.0,
                block_close_on_stale=True, cache_seconds=0.0,
            )
            guard = PredictionStaleGuard(cfg)
            decision = guard.check(_order(offset="close"), _ctx(pd.Timestamp.now()))
        self.assertFalse(decision.allowed)

    def test_block_open_disabled_passes_open(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "predictions.csv"
            p.write_text("symbol\nRB0\n", encoding="utf-8")
            old_ts = time.time() - 10 * 3600
            os.utime(p, (old_ts, old_ts))
            cfg = PredictionStaleGuardConfig(
                predictions_path=str(p), max_stale_hours=4.0,
                block_open_on_stale=False, cache_seconds=0.0,
            )
            guard = PredictionStaleGuard(cfg)
            decision = guard.check(_order(), _ctx(pd.Timestamp.now()))
        self.assertTrue(decision.allowed)

    def test_mtime_cache_works(self) -> None:
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "predictions.csv"
            p.write_text("symbol\nRB0\n", encoding="utf-8")
            old_ts = time.time() - 10 * 3600
            os.utime(p, (old_ts, old_ts))
            cfg = PredictionStaleGuardConfig(
                predictions_path=str(p), max_stale_hours=4.0, cache_seconds=60.0,
            )
            guard = PredictionStaleGuard(cfg)
            # 第一次 check：stat 文件，cache 结果
            d1 = guard.check(_order(), _ctx(pd.Timestamp.now()))
            self.assertFalse(d1.allowed)
            # 文件 mtime 设回当下（但 cache 未过期 → 还应拦）
            now_ts = time.time()
            os.utime(p, (now_ts, now_ts))
            d2 = guard.check(_order(), _ctx(pd.Timestamp.now()))
            self.assertFalse(d2.allowed)   # cache 命中，仍报 stale

    def test_model_age_warning_no_block(self) -> None:
        # model 老但 predictions 新 → 仍放行（model age 仅 warning）
        with tempfile.TemporaryDirectory() as d:
            p = Path(d) / "predictions.csv"
            p.write_text("ok", encoding="utf-8")
            mp = Path(d) / "model.joblib"
            mp.write_text("model", encoding="utf-8")
            old_ts = time.time() - 30 * 86400
            os.utime(mp, (old_ts, old_ts))
            cfg = PredictionStaleGuardConfig(
                predictions_path=str(p), model_path=str(mp),
                max_stale_hours=4.0, max_model_age_days=14.0, cache_seconds=0.0,
            )
            guard = PredictionStaleGuard(cfg)
            decision = guard.check(_order(), _ctx(pd.Timestamp.now()))
        self.assertTrue(decision.allowed)


class TestPredictionStaleGuardConfig(unittest.TestCase):

    def test_invalid_max_stale_raises(self) -> None:
        with self.assertRaises(ValueError):
            PredictionStaleGuardConfig(max_stale_hours=0.0)

    def test_invalid_model_age_raises(self) -> None:
        with self.assertRaises(ValueError):
            PredictionStaleGuardConfig(max_model_age_days=0.0)

    def test_negative_cache_seconds_raises(self) -> None:
        with self.assertRaises(ValueError):
            PredictionStaleGuardConfig(cache_seconds=-1.0)


if __name__ == "__main__":
    unittest.main()
