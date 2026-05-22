"""Smoke test for generic CTA model pipeline."""
from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np
import pandas as pd

from cta.model.model_pipeline import (
    MFE_MAE_KIND_SKIPPED_NO_EXEC,
    _apply_causality_manifest_filter,
    _auto_enrich_candidate_features_for_models,
    _build_candidate_table,
    _build_top_feature_concentration_alerts,
    _build_valid_test_gap_alerts,
    _build_symbol_cluster_sample_weight,
    _ensure_training_columns,
    _build_last_oot_decile_table,
    _build_walk_forward_windows,
    _evaluate_oot_real_execution,
    _ensure_binary_label_diversity,
    _load_top_n_symbols_from_ranking,
    _normalize_intervals,
    _parse_args,
    _resolve_run_exchange,
    _select_best_param_trial,
    _filter_model_leakage_features,
    _select_feature_columns,
    _train_mfe_mae_or_skip,
    _validate_stop_loss_pct_consistency,
    run_model_pipeline,
    run_model_pipeline_multi,
)
from cta.model.oot.pipeline_oot_evaluation import _build_position_lifetime_table
from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.portfolio_logic.config import CapsConfig, PortfolioLogicConfig, RiskThrottleConfig, ThrottleLevel
from cta.model.training.trade_filter_model import TradeFilterModel



class TestModelPipelinePart07(unittest.TestCase):
    def test_evaluate_oot_real_execution_cluster_notional_cap_emits_blocked_cluster_cap(self) -> None:
        """cluster cap 触发时应输出独立 reason，便于诊断是 cluster 维度约束。"""
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-07 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-10 15:00:00", "2020-01-10 16:00:00"]),
                "symbol": ["RB0", "HC0"],  # 同属 BLACK cluster
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "60min"],
                "signal_type": ["atr_breakout", "atr_breakout"],
                "side": ["long", "long"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [1, 1],
                "entry_price": [100.0, 100.0],
                "future_mfe_atr": [1.0, 1.0],
                "future_mae_atr": [0.0, 0.0],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            mae_penalty=1.0,
            initial_capital=1000.0,
            risk_per_trade_pct=0.01,
            max_single_loss_pct=0.002,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            use_position_sizing=False,
            max_position_scale=1.0,
            max_symbol_notional_pct=0.50,  # 与 cluster cap 一致，第二笔由 cluster 维度触顶
            max_concurrent_positions_per_symbol=10,
            max_concurrent_positions_total=20,
            use_portfolio_constraints=True,
            margin_rate=0.10,
            max_total_leverage=10.0,
            max_daily_new_notional_pct=10.0,
            weekly_max_drawdown_pct=1.0,
            enforce_weekly_dd_budget_on_entry=False,
            block_new_entries_on_weekly_dd_breach=False,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
            use_intrabar_stop_tracking=False,
            use_portfolio_logic_runtime=True,
            portfolio_logic=PortfolioLogicConfig(
                enable_htf_gate=False,
                enable_ranker=False,
                enable_risk_throttle=False,
                enable_pyramid=False,
                caps=CapsConfig(
                    max_symbol_notional_pct=0.50,
                    max_cluster_notional_pct=0.50,
                    max_total_notional_pct=1.50,
                ),
            ),
        )
        _monthly, _summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        reasons = trades["block_reason"].astype(str).tolist()
        self.assertIn("blocked_cluster_cap", reasons)

    def test_run_model_pipeline_writes_provenance_json(self) -> None:
        """P2.3: pipeline 输出目录必须有 provenance.json，便于实验追溯。"""
        with tempfile.TemporaryDirectory(prefix="cta_provenance_") as td:
            out = run_model_pipeline(
                symbol="PROV0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=180,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            provenance_path = out.output_dir / "provenance.json"
            self.assertTrue(provenance_path.exists())
            payload = pd.read_json(provenance_path, typ="series")
            for key in (
                "run_tag",
                "git_commit",
                "python_version",
                "candidate_path",
                "feature_table_path",
                "prediction_path",
                "metrics_path",
                "data_snapshot_hash",
                "feature_manifest_hash",
            ):
                self.assertIn(key, payload.index)

    def test_run_model_pipeline_outputs_final_decision_model_naming(self) -> None:
        """final decision 必须体现 dual-side 命名，并输出 side-aware 分数字段。"""
        with tempfile.TemporaryDirectory(prefix="cta_stack_model_") as td:
            out = run_model_pipeline(
                symbol="STACK0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=220,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            metrics = pd.read_csv(out.metrics_path)
            preds = pd.read_csv(out.prediction_path)
            self.assertIn("final_decision_stack", set(metrics["model"].astype(str)))
            self.assertIn("final_decision_score", set(preds.columns))
            self.assertIn("final_decision_score_long", set(preds.columns))
            self.assertIn("final_decision_score_short", set(preds.columns))
            self.assertTrue(
                ("generic_ma_alignment" in preds.columns) or ("ma_alignment" in preds.columns),
                "predictions must carry MA alignment column for ma_cross gate round-trip",
            )
            final_rows = metrics.loc[metrics["model"].astype(str) == "final_decision_stack"]
            self.assertTrue(
                final_rows["model_kind"].astype(str).str.contains("dual_side", regex=False).any()
            )
            self.assertTrue(
                preds["final_decision_model_kind"].astype(str).str.contains("long|short", regex=True).any()
            )


if __name__ == "__main__":
    unittest.main()
