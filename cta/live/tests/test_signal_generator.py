"""Tests for cta.live.signal_generator."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.live.signal_generator import generate_today_candidates
from cta.risk.state.score_quantile_manifest import ScoreQuantileEntry, ScoreQuantileManifest


class _FakeModelRegistry:
    def __init__(self) -> None:
        self.kinds: list[str] = []

    def predict(self, df: pd.DataFrame, *, model_kind: str) -> pd.DataFrame:
        self.kinds.append(model_kind)
        out = df.copy()
        if model_kind == "trade_filter":
            out["trade_filter_prob"] = pd.to_numeric(out.get("seed_prob", 0.82), errors="coerce").fillna(0.82)
        elif model_kind == "regime_classifier":
            out["pred_regime_label"] = "trend_up"
        elif model_kind == "mfe_mae":
            out["pred_mfe_atr"] = 1.2
            out["pred_mae_atr"] = 0.7
        elif model_kind == "final_decision_stack":
            out["final_decision_score"] = 0.66
        return out


class _FakeFeatureLoader:
    def __call__(self, symbol: str, interval: str, dt: pd.Timestamp) -> pd.Series:  # noqa: D401
        return pd.Series(
            {
                "generic_ma_alignment": 1,
                "generic_vol_rank_20": 0.55,
                "generic_ret_5": 0.01,
                "feature_model_trade_filter_hint": 0.8,
            }
        )


class TestSignalGenerator(unittest.TestCase):
    def test_generate_expands_side_and_runs_all_model_stages(self) -> None:
        model_registry = _FakeModelRegistry()
        feature_loader = _FakeFeatureLoader()
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=True,
            trade_filter_gate_mode="cluster_interval_percentile",
            trade_filter_percentile_threshold=70.0,
            use_regime_gate=True,
            use_mfe_mae_gate=True,
            use_stacking_gate=True,
            stacking_score_threshold=0.55,
            trade_filter_percentile_threshold_by_cluster_interval={},
            signal_type_blacklist=(),
        )
        manifest = ScoreQuantileManifest(
            [
                ScoreQuantileEntry(
                    cluster="black",
                    symbol="RB0",
                    interval="day",
                    p50=0.50,
                    p60=0.60,
                    p70=0.70,
                    p80=0.80,
                    p90=0.90,
                    p95=0.95,
                    sample_count=100,
                )
            ]
        )
        bars = {
            ("RB0", "day"): pd.DataFrame(
                [
                    {
                        "datetime": "2026-05-29 15:00:00",
                        "signal_type": "donchian_breakout",
                        "seed_prob": 0.92,
                    }
                ]
            )
        }
        out = generate_today_candidates(
            bars_by_symbol_interval=bars,
            cfg=cfg,
            as_of=pd.Timestamp("2026-05-29 15:00:00"),
            model_registry=model_registry,
            feature_loader=feature_loader,
            score_manifest=manifest,
        )
        self.assertEqual(sorted(out["side"].unique().tolist()), ["long", "short"])
        self.assertIn("trade_filter_prob", out.columns)
        self.assertIn("trade_filter_prob_pctl", out.columns)
        self.assertIn("_model_pass", out.columns)
        self.assertTrue(bool(out["_model_pass"].all()))
        self.assertEqual(
            model_registry.kinds,
            ["trade_filter", "regime_classifier", "mfe_mae", "final_decision_stack"],
        )

    def test_generate_blocks_when_trade_filter_percentile_below_threshold(self) -> None:
        model_registry = _FakeModelRegistry()
        feature_loader = _FakeFeatureLoader()
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=True,
            trade_filter_gate_mode="cluster_interval_percentile",
            trade_filter_percentile_threshold=75.0,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_stacking_gate=False,
            trade_filter_percentile_threshold_by_cluster_interval={},
            signal_type_blacklist=(),
        )
        manifest = ScoreQuantileManifest(
            [
                ScoreQuantileEntry(
                    cluster="black",
                    symbol="RB0",
                    interval="day",
                    p50=0.50,
                    p60=0.60,
                    p70=0.70,
                    p80=0.80,
                    p90=0.90,
                    p95=0.95,
                    sample_count=100,
                )
            ]
        )
        bars = {
            ("RB0", "day"): pd.DataFrame(
                [
                    {
                        "datetime": "2026-05-29 15:00:00",
                        "signal_type": "donchian_breakout",
                        "side": "long",
                        "seed_prob": 0.65,
                    }
                ]
            )
        }
        out = generate_today_candidates(
            bars_by_symbol_interval=bars,
            cfg=cfg,
            as_of=pd.Timestamp("2026-05-29 15:00:00"),
            model_registry=model_registry,
            feature_loader=feature_loader,
            score_manifest=manifest,
        )
        self.assertEqual(len(out), 1)
        self.assertFalse(bool(out.iloc[0]["_model_pass"]))
        self.assertTrue(str(out.iloc[0]["_model_block_reason"]).startswith("blocked_trade_filter"))

    def test_generate_filters_signal_type_blacklist(self) -> None:
        model_registry = _FakeModelRegistry()
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_stacking_gate=False,
            signal_type_blacklist=("donchian_breakout",),
        )
        bars = {
            ("RB0", "day"): pd.DataFrame(
                [
                    {
                        "datetime": "2026-05-29 15:00:00",
                        "signal_type": "donchian_breakout",
                        "side": "long",
                    }
                ]
            )
        }
        out = generate_today_candidates(
            bars_by_symbol_interval=bars,
            cfg=cfg,
            as_of=pd.Timestamp("2026-05-29 15:00:00"),
            model_registry=model_registry,
            feature_loader=None,
            score_manifest=None,
        )
        self.assertTrue(out.empty)


if __name__ == "__main__":
    unittest.main()
