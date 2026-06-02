"""Risk wiring tests for train hook + OOT input enrichment."""
from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.pipeline_oot_evaluation_inputs import apply_risk_orchestrator_columns
from cta.model.orchestration.pipeline_run_predictions import (
    write_score_distribution_baseline_from_scored_rows,
    write_score_quantile_manifest_from_scored_rows,
)
from cta.risk.config import RiskSystemConfig
from cta.risk.guards.config import (
    ScoreDistributionDriftConfig,
)
from cta.risk.state.score_quantile_manifest import ScoreQuantileManifest


class TestRiskManifestTrainHook(unittest.TestCase):
    def test_write_manifest_uses_train_valid_only(self) -> None:
        scored_rows = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0", "RB0", "IF0"],
                "interval": ["day", "day", "day", "day"],
                "cluster_name": ["black", "black", "black", "index"],
                "split": ["train", "valid", "test", "train"],
                "trade_filter_prob": [0.40, 0.60, 0.95, 0.70],
            }
        )
        with tempfile.TemporaryDirectory() as td:
            manifests_dir = Path(td)
            out_path = write_score_quantile_manifest_from_scored_rows(
                scored_rows=scored_rows,
                manifests_dir=manifests_dir,
                run_tag="UNITTEST",
                meta={"git_sha": "abc123"},
                timestamp="20260529_120000",
            )
            self.assertTrue(out_path.exists())
            latest = manifests_dir / "score_quantile_manifest_latest.json"
            self.assertTrue(latest.exists())
            manifest = ScoreQuantileManifest.from_json(out_path)
            rb = manifest.lookup("black", "RB0", "day")
            self.assertIsNotNone(rb)
            assert rb is not None
            # 仅 train + valid，排除 test
            self.assertEqual(int(rb.sample_count), 2)
            self.assertAlmostEqual(float(rb.p50), 0.50, places=6)

    def test_write_score_distribution_baseline_uses_train_valid_only(self) -> None:
        scored_rows = pd.DataFrame(
            {
                "symbol": ["RB0", "RB0", "RB0", "IF0"],
                "interval": ["day", "day", "day", "day"],
                "cluster_name": ["black", "black", "black", "index"],
                "split": ["train", "valid", "test", "train"],
                "trade_filter_prob": [0.40, 0.60, 0.95, 0.70],
            }
        )
        with tempfile.TemporaryDirectory() as td:
            manifests_dir = Path(td)
            out_path = write_score_distribution_baseline_from_scored_rows(
                scored_rows=scored_rows,
                manifests_dir=manifests_dir,
                run_tag="UNITTEST",
                timestamp="20260529_120000",
                bins=10,
            )
            self.assertTrue(out_path.exists())
            latest = manifests_dir / "score_distribution_train_latest.json"
            self.assertTrue(latest.exists())
            payload = json.loads(out_path.read_text(encoding="utf-8"))
            self.assertEqual(int(payload["sample_count"]), 3)
            self.assertEqual(len(payload["scores"]), 3)
            self.assertNotIn(0.95, payload["scores"])  # test split 被排除


class TestOotRiskInputEnrichment(unittest.TestCase):
    def test_no_risk_config_keeps_defaults(self) -> None:
        cfg = OotEvaluationConfig()
        df = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "interval": ["day"],
                "datetime": ["2024-01-01"],
                "trade_filter_prob_pctl": [80.0],
                "lots": [2],
            }
        )
        out = apply_risk_orchestrator_columns(df, cfg)
        self.assertEqual(float(out.loc[0, "risk_lots_mult"]), 1.0)
        self.assertEqual(str(out.loc[0, "risk_block_reason"]), "")

    def test_risk_config_blocks_when_below_threshold(self) -> None:
        cfg = OotEvaluationConfig(
            risk_system=RiskSystemConfig(
                enable_quantile_threshold=False,
                enable_bucket_scaling=False,
                enable_linear_dd_scaler=False,
                enable_dynamic_bump=False,
            ),
            trade_filter_percentile_threshold=70.0,
            trade_filter_percentile_threshold_by_cluster_interval={},
        )
        df = pd.DataFrame(
            {
                "symbol": ["RB0"],
                "interval": ["day"],
                "datetime": ["2024-01-01"],
                "trade_filter_prob_pctl": [60.0],
                "lots": [3],
            }
        )
        out = apply_risk_orchestrator_columns(df, cfg)
        self.assertEqual(str(out.loc[0, "risk_block_reason"]), "below_threshold")
        self.assertEqual(float(out.loc[0, "risk_lots_mult"]), 0.0)
        self.assertGreater(float(out.loc[0, "risk_effective_threshold"]), 65.0)

    def test_oot_score_distribution_guard_blocks_on_critical_drift(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            ref_path = Path(td) / "score_distribution_train_latest.json"
            ref_payload = {
                "scores": [0.1] * 30,
                "reference_scores": [0.1] * 30,
            }
            ref_path.write_text(json.dumps(ref_payload), encoding="utf-8")
            cfg = OotEvaluationConfig(
                use_oot_guard_chain=True,
                use_oot_signal_concentration_guard=False,
                use_oot_consecutive_loss_guard=False,
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
            df = pd.DataFrame(
                {
                    "symbol": ["RB0", "RB0", "RB0", "RB0"],
                    "interval": ["day", "day", "day", "day"],
                    "datetime": [
                        "2024-01-01 09:00:00",
                        "2024-01-01 10:00:00",
                        "2024-01-01 11:00:00",
                        "2024-01-01 12:00:00",
                    ],
                    "exit_datetime": [
                        "2024-01-02 09:00:00",
                        "2024-01-02 10:00:00",
                        "2024-01-02 11:00:00",
                        "2024-01-02 12:00:00",
                    ],
                    "trade_filter_prob": [0.90, 0.92, 0.95, 0.93],
                    "trade_filter_prob_pctl": [90.0, 92.0, 95.0, 93.0],
                    "lots": [1, 1, 1, 1],
                    "side": ["long", "long", "long", "long"],
                }
            )
            out = apply_risk_orchestrator_columns(df, cfg)
            reasons = out["risk_block_reason"].astype(str).tolist()
            blocked = [r for r in reasons if r.startswith("score_distribution_drift:")]
            self.assertTrue(blocked, f"expected score drift block reason, got={reasons}")


if __name__ == "__main__":
    unittest.main()
