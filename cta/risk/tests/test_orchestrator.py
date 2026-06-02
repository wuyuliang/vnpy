"""RiskOrchestrator 集成测试。

覆盖：
1. 全 disable → 行为退化（base_threshold + lots 不变）
2. quantile + dynamic_bump 都开 → 阈值组合
3. linear_dd_scaler 单跑 → lots 缩
4. bucket_scaling + linear_dd 串联 → 两层连乘
5. score < threshold → BLOCK(stage=threshold, reason=below_threshold)
6. lots → 0 → BLOCK(stage=sizing)
7. fail-open：缺 score
8. cfg 校验
"""
from __future__ import annotations

import unittest

import pandas as pd

from cta.risk.base import SignalContext
from cta.risk.config import RiskSystemConfig
from cta.risk.orchestrator import RiskOrchestrator
from cta.risk.state.bucket_pnl_tracker import BucketKey, BucketPnlTracker
from cta.risk.state.score_quantile_manifest import (
    ScoreQuantileEntry,
    ScoreQuantileManifest,
    WILDCARD_SYMBOL,
)


def _ctx(prob_pctl: float = 80.0, dd: float = 0.0,
         cluster="metal", symbol="CU0", interval="day") -> SignalContext:
    return SignalContext(
        candidate={
            "cluster": cluster, "symbol": symbol, "interval": interval,
            "trade_filter_prob": prob_pctl / 100.0,
            "trade_filter_prob_pctl": prob_pctl,
            "signal_type": "donchian_breakout",
            "side": "long",
        },
        portfolio={"equity": 1_000_000.0, "effective_dd_pct": dd},
        bar_dt=pd.Timestamp("2026-01-30"),
    )


class TestRiskOrchestrator(unittest.TestCase):

    def test_all_disabled_passes_through(self) -> None:
        cfg = RiskSystemConfig(
            enable_quantile_threshold=False,
            enable_bucket_scaling=False,
            enable_linear_dd_scaler=False,
            enable_dynamic_bump=False,
        )
        orc = RiskOrchestrator.from_config(cfg)
        decision = orc.evaluate(_ctx(prob_pctl=80.0), original_lots=10, base_threshold=70.0)
        self.assertTrue(decision.passed)
        self.assertEqual(decision.adjusted_lots, 10)
        self.assertEqual(decision.effective_threshold, 70.0)

    def test_below_base_threshold_blocked(self) -> None:
        cfg = RiskSystemConfig(
            enable_quantile_threshold=False,
            enable_bucket_scaling=False,
            enable_linear_dd_scaler=False,
            enable_dynamic_bump=False,
        )
        orc = RiskOrchestrator.from_config(cfg)
        decision = orc.evaluate(_ctx(prob_pctl=50.0), original_lots=10, base_threshold=70.0)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.block_stage, "threshold")
        self.assertEqual(decision.block_reason, "below_threshold")

    def test_quantile_manifest_lifts_threshold(self) -> None:
        manifest = ScoreQuantileManifest([
            ScoreQuantileEntry(
                cluster="metal", symbol="CU0", interval="day",
                p50=0.5, p60=0.55, p70=0.60, p80=0.66, p90=0.74, p95=0.81,
            ),
        ])
        cfg = RiskSystemConfig(
            enable_quantile_threshold=True,
            quantile_field="p80",
            enable_bucket_scaling=False,
            enable_linear_dd_scaler=False,
            enable_dynamic_bump=False,
        )
        orc = RiskOrchestrator.from_config(cfg, quantile_manifest=manifest)
        # candidate pctl=75 < 80 (p80) → BLOCK
        decision = orc.evaluate(_ctx(prob_pctl=75.0), original_lots=10, base_threshold=70.0)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.effective_threshold, 80.0)

    def test_quantile_does_not_override_stricter_static_threshold(self) -> None:
        manifest = ScoreQuantileManifest([
            ScoreQuantileEntry(
                cluster="bond", symbol="T0", interval="day",
                p50=0.45, p60=0.50, p70=0.55, p80=0.60, p90=0.66, p95=0.72,
            ),
        ])
        cfg = RiskSystemConfig(
            enable_quantile_threshold=True,
            quantile_field="p70",
            enable_bucket_scaling=False,
            enable_linear_dd_scaler=False,
            enable_dynamic_bump=False,
        )
        orc = RiskOrchestrator.from_config(
            cfg,
            quantile_manifest=manifest,
            static_overrides={"bond|day": 80.0},
            global_default_threshold=70.0,
        )
        decision = orc.evaluate(
            _ctx(prob_pctl=75.0, cluster="bond", symbol="T0", interval="day"),
            original_lots=10,
            base_threshold=70.0,
        )
        self.assertFalse(decision.passed)
        self.assertEqual(decision.block_stage, "threshold")
        self.assertEqual(decision.effective_threshold, 80.0)

    def test_dynamic_bump_raises_threshold_with_dd(self) -> None:
        cfg = RiskSystemConfig(
            enable_quantile_threshold=False,
            enable_bucket_scaling=False,
            enable_linear_dd_scaler=False,
            enable_dynamic_bump=True,
        )
        orc = RiskOrchestrator.from_config(cfg)
        # dd=2% → bump +5pp → threshold 70 → 75 → candidate 80 仍通过
        decision = orc.evaluate(_ctx(prob_pctl=80.0, dd=0.02), original_lots=10, base_threshold=70.0)
        self.assertTrue(decision.passed)
        self.assertEqual(decision.effective_threshold, 75.0)
        # dd=2% pctl=73 → 73 < 75 → BLOCK
        decision = orc.evaluate(_ctx(prob_pctl=73.0, dd=0.02), original_lots=10, base_threshold=70.0)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.block_stage, "threshold")

    def test_linear_dd_scaler_only(self) -> None:
        cfg = RiskSystemConfig(
            enable_quantile_threshold=False,
            enable_bucket_scaling=False,
            enable_linear_dd_scaler=True,
            enable_dynamic_bump=False,
        )
        orc = RiskOrchestrator.from_config(cfg)
        decision = orc.evaluate(_ctx(prob_pctl=80.0, dd=0.02), original_lots=10, base_threshold=70.0)
        self.assertTrue(decision.passed)
        self.assertEqual(decision.adjusted_lots, 9)

    def test_bucket_and_dd_scaler_compound(self) -> None:
        cfg = RiskSystemConfig(
            enable_quantile_threshold=False,
            enable_bucket_scaling=True,
            enable_linear_dd_scaler=True,
            enable_dynamic_bump=False,
            bucket_min_trades=5,
        )
        tracker = BucketPnlTracker(window_days=30)
        # 把 p70-80 桶塞 14 笔 -50pnl → -7 bp → bucket mult=0.7
        for _ in range(14):
            tracker.on_trade({
                "cluster": "metal", "interval": "day", "score_bucket": "p70-80",
                "dt": pd.Timestamp("2026-01-20"), "net_pnl": -50.0,
                "portfolio_equity": 1_000_000.0,
            })
        orc = RiskOrchestrator.from_config(cfg, bucket_tracker=tracker)
        # bucket: 10 → 7；linear_dd dd=2% mult=0.9 → 7*0.9=6.3 → 6
        decision = orc.evaluate(_ctx(prob_pctl=75.0, dd=0.02), original_lots=10, base_threshold=70.0)
        self.assertTrue(decision.passed)
        self.assertEqual(decision.adjusted_lots, 6)

    def test_missing_score_passes_through_when_cfg_allows(self) -> None:
        cfg = RiskSystemConfig(
            enable_quantile_threshold=False,
            enable_bucket_scaling=False,
            enable_linear_dd_scaler=False,
            enable_dynamic_bump=False,
            pass_through_when_score_missing=True,
        )
        ctx = SignalContext(
            candidate={"cluster": "metal", "symbol": "CU0", "interval": "day"},
            portfolio={"equity": 1_000_000.0},
            bar_dt=pd.Timestamp("2026-01-30"),
        )
        orc = RiskOrchestrator.from_config(cfg)
        decision = orc.evaluate(ctx, original_lots=10, base_threshold=70.0)
        self.assertTrue(decision.passed)
        self.assertEqual(decision.adjusted_lots, 10)

    def test_missing_score_blocks_when_strict(self) -> None:
        cfg = RiskSystemConfig(
            enable_quantile_threshold=False,
            enable_bucket_scaling=False,
            enable_linear_dd_scaler=False,
            enable_dynamic_bump=False,
            pass_through_when_score_missing=False,
        )
        ctx = SignalContext(
            candidate={"cluster": "metal", "symbol": "CU0", "interval": "day"},
            portfolio={"equity": 1_000_000.0},
            bar_dt=pd.Timestamp("2026-01-30"),
        )
        orc = RiskOrchestrator.from_config(cfg)
        decision = orc.evaluate(ctx, original_lots=10, base_threshold=70.0)
        self.assertFalse(decision.passed)
        self.assertEqual(decision.block_reason, "score_missing")

    def test_invalid_cfg_quantile_field_raises(self) -> None:
        with self.assertRaises(ValueError):
            RiskSystemConfig(quantile_field="p77")

    def test_invalid_cfg_bp_steps_order_raises(self) -> None:
        with self.assertRaises(ValueError):
            RiskSystemConfig(
                bucket_pnl_bp_steps=(0.0, -5.0),
                bucket_pnl_mults=(0.5, 0.7, 1.0),
            )

    def test_debug_dict_records_chain(self) -> None:
        cfg = RiskSystemConfig(
            enable_quantile_threshold=False,
            enable_bucket_scaling=False,
            enable_linear_dd_scaler=True,
            enable_dynamic_bump=True,
        )
        orc = RiskOrchestrator.from_config(cfg)
        decision = orc.evaluate(_ctx(prob_pctl=85.0, dd=0.03), original_lots=10, base_threshold=70.0)
        self.assertIn("base_threshold", decision.debug)
        self.assertIn("score_pctl", decision.debug)
        # 至少有一个 after_* 键
        keys = [k for k in decision.debug if k.startswith("after_")]
        self.assertGreaterEqual(len(keys), 1)

    def test_from_config_auto_wires_optional_w7_w10_sizers(self) -> None:
        cfg = RiskSystemConfig(
            enable_quantile_threshold=False,
            enable_bucket_scaling=False,
            enable_linear_dd_scaler=False,
            enable_dynamic_bump=False,
            enable_holiday_position_reducer=True,
            enable_volatility_regime_scaler=True,
            enable_night_session_carry=True,
            enable_daily_var_budget=True,
            enable_execution_quality=True,
            enable_profit_give_back_sizer=True,
        )
        orc = RiskOrchestrator.from_config(cfg)
        names = [type(x).__name__ for x in orc._scalers]
        self.assertIn("HolidayPositionReducer", names)
        self.assertIn("VolatilityRegimeScaler", names)
        self.assertIn("NightSessionCarryRule", names)
        self.assertIn("DailyVaRBudgetSizer", names)
        self.assertIn("ExecutionQualityScaler", names)
        self.assertIn("ProfitGiveBackSizer", names)


if __name__ == "__main__":
    unittest.main()
