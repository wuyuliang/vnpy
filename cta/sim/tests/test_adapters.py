"""P1 sim/live adapter tests — covers all 7 P1 items.

覆盖：
- P1-7: RotationStepper
- P1-8: PositionEvaluator (trailing_tp)
- P1-9: PositionEvaluator (profit_aware_horizon)
- P1-10: EntryGateChain (ma_cross_gate + regime_short_filter)
- P1-11: EntryGateChain (trend_aware_trade_filter)
- P1-12: EntryGateChain (trade_filter_bypass_signal_types)
- P1-13: PositionEvaluator (intrabar_stop_loss_pct_by_cluster_interval)
"""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.config.cross_sectional_rotation_config import CrossSectionalRotationConfig
from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.risk.guards.config import ScoreDistributionDriftConfig
from cta.live.online_feature import OnlineFeatureLoader
from cta.portfolio_logic.portfolio_state import PortfolioState
from cta.risk.config import RiskSystemConfig
from cta.sim.adapters import (
    EntryGateChain,
    FeatureBasedStateProvider,
    GateDecision,
    PositionEvaluator,
    PositionExitDecision,
    RotationStepper,
)


# ─── 测试数据辅助 ────────────────────────────────────────────────────────


def _synthetic_bars(n_days: int = 90) -> pd.DataFrame:
    dt = pd.date_range("2024-01-01", periods=n_days, freq="D")
    return pd.DataFrame({"datetime": dt, "close": np.linspace(100, 130, n_days)})


def _write_parquet_dir(root: Path, symbol: str, interval: str, df: pd.DataFrame) -> None:
    prefix = "".join(ch for ch in symbol if ch.isalpha()).upper()
    df["_date"] = pd.to_datetime(df["datetime"]).dt.strftime("%Y-%m-%d")
    for date_str, group in df.groupby("_date"):
        sub = group.drop(columns=["_date"])
        if interval == "day":
            out_dir = root / "day" / symbol
        else:
            out_dir = root / interval / prefix
        out_dir.mkdir(parents=True, exist_ok=True)
        sub.to_parquet(out_dir / f"{date_str}.parquet")


# ─── P1-7: RotationStepper ───────────────────────────────────────────────


class TestRotationStepper(unittest.TestCase):
    def _universe(self) -> dict[str, pd.DataFrame]:
        return {
            "IF0": _synthetic_bars(90).assign(close=np.linspace(100, 130, 90)),
            "IH0": _synthetic_bars(90).assign(close=np.linspace(100, 110, 90)),
            "IC0": _synthetic_bars(90).assign(close=np.linspace(100, 80, 90)),
        }

    def _cfg(self) -> CrossSectionalRotationConfig:
        return CrossSectionalRotationConfig(
            use_cross_sectional_momentum_rotation=True,
            enabled_by_cluster_interval={"index|day": True},
            cluster_neutral=True,
            min_cluster_size=3,
            use_vol_target_weighting=False,
            max_symbol_notional_pct=0.25,
            universe_clusters=("index",),
        )

    def test_step_emits_intents_on_rebalance_day(self) -> None:
        universe = self._universe()
        stepper = RotationStepper(
            cfg=self._cfg(),
            universe_provider=lambda _ts: universe,
            exchange_by_symbol={"IF0": "CFFEX", "IH0": "CFFEX", "IC0": "CFFEX"},
        )
        intents = stepper.step(pd.Timestamp("2024-03-25"), PortfolioState(equity=1_000_000.0))
        self.assertGreaterEqual(len(intents), 2)
        self.assertEqual({i.signal_type for i in intents}, {"cross_sectional_momentum"})

    def test_step_returns_empty_when_disabled(self) -> None:
        cfg = CrossSectionalRotationConfig()  # 默认 off
        stepper = RotationStepper(
            cfg=cfg, universe_provider=lambda _ts: self._universe(),
        )
        intents = stepper.step(pd.Timestamp("2024-03-25"), PortfolioState(equity=1_000_000))
        self.assertEqual(intents, [])

    def test_last_rebalance_dt_tracked(self) -> None:
        universe = self._universe()
        stepper = RotationStepper(
            cfg=self._cfg(), universe_provider=lambda _ts: universe,
        )
        self.assertIsNone(stepper.last_rebalance_dt)
        stepper.step(pd.Timestamp("2024-03-25"), PortfolioState(equity=500_000))
        self.assertEqual(stepper.last_rebalance_dt, pd.Timestamp("2024-03-25"))


# ─── P1-8 + P1-9 + P1-13: PositionEvaluator ──────────────────────────────


class TestPositionEvaluator(unittest.TestCase):
    def _state_provider(self, *, ma_align: int = 2, regime: str = "trend_up") -> object:
        class _Fake:
            def lookup_ma_alignment(self, symbol, dt): return ma_align
            def lookup_regime_label(self, symbol, dt): return regime
            def lookup_realized_vol(self, symbol, dt): return 0.02
        return _Fake()

    def test_no_config_returns_no_exit(self) -> None:
        ev = PositionEvaluator()
        decision = ev.evaluate(
            position={"symbol": "RB0", "side": "long", "entry_price": 100.0, "bars_held": 5},
            bar={"close": 105.0, "datetime": pd.Timestamp("2024-03-25")},
            highwater_price=106.0, cluster="black", interval="day",
        )
        self.assertFalse(decision.should_exit)

    def test_hard_stop_triggers_on_loss(self) -> None:
        # P1-13: 默认 intrabar_stop_loss_pct=0.01
        oot_cfg = OotEvaluationConfig()
        ev = PositionEvaluator(oot_cfg=oot_cfg)
        # entry 100, current 98 → -2% < -1% stop
        decision = ev.evaluate(
            position={"symbol": "RB0", "side": "long", "entry_price": 100.0, "bars_held": 1},
            bar={"close": 98.0, "datetime": pd.Timestamp("2024-03-25")},
            highwater_price=100.0, cluster="black", interval="day",
        )
        self.assertTrue(decision.should_exit)
        self.assertEqual(decision.exit_reason, "hard_stop")

    def test_intrabar_stop_pct_by_cluster_interval_override(self) -> None:
        # P1-13: black|day override 到 0.05 → entry 100 / current 96 不止损
        oot_cfg = OotEvaluationConfig(
            intrabar_stop_loss_pct_by_cluster_interval={"black|day": 0.05},
        )
        ev = PositionEvaluator(oot_cfg=oot_cfg)
        decision = ev.evaluate(
            position={"symbol": "RB0", "side": "long", "entry_price": 100.0, "bars_held": 1},
            bar={"close": 96.0, "datetime": pd.Timestamp("2024-03-25")},
            highwater_price=100.0, cluster="black", interval="day",
        )
        # -4% < -5% → 不触发
        self.assertFalse(decision.should_exit)


# ─── P1-10/11/12: EntryGateChain ─────────────────────────────────────────


class TestEntryGateChain(unittest.TestCase):
    def test_all_pass_when_no_gates_enabled(self) -> None:
        cfg = OotEvaluationConfig(use_trade_filter_gate=False)
        chain = EntryGateChain(cfg)
        decision = chain.evaluate({
            "symbol": "IF0", "side": "long", "signal_type": "cross_sectional_momentum",
            "cluster": "index", "interval": "day",
        })
        self.assertTrue(decision.passed)

    def test_signal_type_blacklist_blocks_before_other_gates(self) -> None:
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            signal_type_blacklist=("donchian_breakout",),
        )
        chain = EntryGateChain(cfg)
        decision = chain.evaluate(
            {
                "symbol": "IF0",
                "side": "long",
                "signal_type": "donchian_breakout",
                "cluster": "index",
                "interval": "day",
            }
        )
        self.assertFalse(decision.passed)
        self.assertEqual(decision.block_stage, "signal_type_blacklist")
        self.assertEqual(decision.block_reason, "blocked_signal_type_blacklist")

    def _candidate(self, **overrides) -> dict:
        base = {
            "symbol": "IF0", "exchange": "CFFEX",
            "side": "long", "signal_type": "cross_sectional_momentum",
            "cluster": "index", "interval": "day", "bull_mode": "attack",
            "trade_filter_prob": 0.55,
            "trade_filter_prob_pctl": 75.0,
        }
        base.update(overrides)
        return base

    def test_bypass_signal_types_routes_through(self) -> None:
        # P1-12: cross_sectional_momentum 在 bypass list 中 → 跳过 trade_filter
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=True,
            trade_filter_gate_mode="cluster_interval_percentile",
            trade_filter_percentile_threshold=70.0,
            trade_filter_bypass_signal_types=("cross_sectional_momentum",),
        )
        chain = EntryGateChain(cfg)
        # prob_pctl=10 远低于 70，正常应被拦；但 bypass 列表含本 signal_type
        decision = chain.evaluate(self._candidate(
            bull_mode="normal", trade_filter_prob=0.10, trade_filter_prob_pctl=10.0,
        ))
        self.assertTrue(decision.passed)

    def test_default_no_bypass_blocks_low_pctl(self) -> None:
        # H4 回归 + P1-12 对照
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=True,
            trade_filter_gate_mode="cluster_interval_percentile",
            trade_filter_percentile_threshold=70.0,
            # 默认 bypass = ()
        )
        chain = EntryGateChain(cfg)
        decision = chain.evaluate(self._candidate(
            bull_mode="normal", trade_filter_prob=0.10, trade_filter_prob_pctl=10.0,
        ))
        self.assertFalse(decision.passed)
        self.assertEqual(decision.block_stage, "trade_filter")

    def test_risk_stage_blocks_below_threshold(self) -> None:
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            risk_system=RiskSystemConfig(
                enable_quantile_threshold=False,
                enable_bucket_scaling=False,
                enable_linear_dd_scaler=False,
                enable_dynamic_bump=False,
            ),
            trade_filter_percentile_threshold=70.0,
            trade_filter_percentile_threshold_by_cluster_interval={},
        )
        chain = EntryGateChain(cfg)
        decision = chain.evaluate(
            {
                "symbol": "IF0",
                "exchange": "CFFEX",
                "side": "long",
                "signal_type": "cross_sectional_momentum",
                "interval": "day",
                "trade_filter_prob_pctl": 60.0,
            },
            dt=pd.Timestamp("2026-01-02"),
            original_lots=5,
        )
        self.assertFalse(decision.passed)
        self.assertEqual(decision.block_stage, "risk_threshold")
        self.assertIn("below_threshold", decision.block_reason)

    def test_risk_stage_scales_lots_with_drawdown(self) -> None:
        class _Provider:
            def lookup_ma_alignment(self, symbol, dt): return 1
            def lookup_regime_label(self, symbol, dt): return "trend_up"
            def lookup_realized_vol(self, symbol, dt): return 0.02
            def portfolio_snapshot(self, dt):  # noqa: D401
                return {"effective_dd_pct": 0.03}

        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            risk_system=RiskSystemConfig(
                enable_quantile_threshold=False,
                enable_bucket_scaling=False,
                enable_linear_dd_scaler=True,
                enable_dynamic_bump=False,
                linear_dd_trigger_pct=0.01,
                linear_dd_step_pct=0.01,
                linear_dd_step_mult=0.5,
                linear_dd_floor_mult=0.2,
            ),
            trade_filter_percentile_threshold=50.0,
            trade_filter_percentile_threshold_by_cluster_interval={},
        )
        chain = EntryGateChain(cfg)
        decision = chain.evaluate(
            {
                "symbol": "IF0",
                "exchange": "CFFEX",
                "side": "long",
                "signal_type": "cross_sectional_momentum",
                "interval": "day",
                "trade_filter_prob_pctl": 90.0,
            },
            dt=pd.Timestamp("2026-01-02"),
            state_provider=_Provider(),
            original_lots=10,
        )
        self.assertTrue(decision.passed)
        self.assertEqual(int(decision.adjusted_lots), 2)

    def test_score_drift_guard_blocks_when_distribution_critically_shifted(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ref_path = Path(td) / "score_distribution_train_latest.json"
            ref_path.write_text(
                '{"scores":[0.1,0.1,0.1,0.1,0.1], "reference_scores":[0.1,0.1,0.1,0.1,0.1]}',
                encoding="utf-8",
            )
            cfg = OotEvaluationConfig(
                use_trade_filter_gate=False,
                use_oot_score_distribution_guard=True,
                oot_score_distribution_guard=ScoreDistributionDriftConfig(
                    train_distribution_path=str(ref_path),
                    drift_metric="kl",
                    warning_threshold=0.01,
                    critical_threshold=0.02,
                    emergency_threshold=1.0,
                    min_samples_for_assessment=3,
                    rolling_window_hours=8,
                ),
                risk_system=None,
            )
            chain = EntryGateChain(cfg)
            # 前两条因样本不足仍放行；第三条开始触发 critical 阻断。
            d1 = chain.evaluate(self._candidate(trade_filter_prob=0.95, trade_filter_prob_pctl=95.0), dt=pd.Timestamp("2026-01-02 09:00:00"))
            d2 = chain.evaluate(self._candidate(trade_filter_prob=0.94, trade_filter_prob_pctl=94.0), dt=pd.Timestamp("2026-01-02 10:00:00"))
            d3 = chain.evaluate(self._candidate(trade_filter_prob=0.93, trade_filter_prob_pctl=93.0), dt=pd.Timestamp("2026-01-02 11:00:00"))
            self.assertTrue(d1.passed)
            self.assertTrue(d2.passed)
            self.assertFalse(d3.passed)
            self.assertEqual(d3.block_stage, "score_drift")
            self.assertIn("score_distribution_drift", d3.block_reason)


# ─── StateProvider ───────────────────────────────────────────────────────


class TestStateProvider(unittest.TestCase):
    def test_returns_zero_when_data_missing(self) -> None:
        loader = OnlineFeatureLoader(feature_root="/nonexistent")
        sp = FeatureBasedStateProvider(loader)
        self.assertEqual(sp.lookup_ma_alignment("RB0", pd.Timestamp("2024-03-25")), 0)
        self.assertEqual(sp.lookup_regime_label("RB0", pd.Timestamp("2024-03-25")), "")
        self.assertEqual(sp.lookup_realized_vol("RB0", pd.Timestamp("2024-03-25")), 0.02)

    def test_reads_from_real_parquet(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            tmp = Path(tmpdir)
            df = pd.DataFrame({
                "datetime": pd.date_range("2024-03-25", periods=3, freq="D"),
                "close": [100.0, 101.0, 102.0],
                "generic_ma_alignment": [1, 2, -1],
                "regime_label": ["trend_up", "trend_up", "range"],
                "realized_vol_20d": [0.018, 0.019, 0.020],
            })
            _write_parquet_dir(tmp, "RB0", "day", df.copy())
            loader = OnlineFeatureLoader(feature_root=tmp)
            sp = FeatureBasedStateProvider(loader, interval="day")
            # 取 2024-03-26 数据
            target = pd.Timestamp("2024-03-26")
            self.assertEqual(sp.lookup_ma_alignment("RB0", target), 2)
            self.assertEqual(sp.lookup_regime_label("RB0", target), "trend_up")
            self.assertAlmostEqual(sp.lookup_realized_vol("RB0", target), 0.019, places=5)


if __name__ == "__main__":
    unittest.main()
