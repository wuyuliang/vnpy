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
from cta.model.pipeline_oot_evaluation import _build_position_lifetime_table
from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.portfolio_logic.config import PortfolioLogicConfig, RiskThrottleConfig, ThrottleLevel
from cta.model.trade_filter_model import TradeFilterModel



class TestModelPipelinePart04(unittest.TestCase):
    def test_evaluate_oot_real_execution_symbol_concurrent_cap_blocks_fourth_entry(self) -> None:
        """同 symbol 同时在仓笔数 cap：3 笔后第 4 笔阻断。"""
        starts = pd.to_datetime([
            "2020-01-06 09:00:00",
            "2020-01-06 10:00:00",
            "2020-01-06 11:00:00",
            "2020-01-06 12:00:00",
        ])
        exits = pd.to_datetime([
            "2020-01-10 09:00:00",
            "2020-01-10 09:00:00",
            "2020-01-10 09:00:00",
            "2020-01-10 09:00:00",
        ])
        pred = pd.DataFrame(
            {
                "datetime": starts,
                "exit_datetime": exits,
                "symbol": ["RB0"] * 4,
                "exchange": ["SHFE"] * 4,
                "interval": ["60min"] * 4,
                "signal_type": ["atr_breakout"] * 4,
                "side": ["long"] * 4,
                "pred_split": ["test"] * 4,
                "window_id": [0] * 4,
                "is_executed": [1] * 4,
                "entry_price": [100.0] * 4,
                "future_mfe_atr": [1.0] * 4,
                "future_mae_atr": [0.0] * 4,
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            mae_penalty=1.0,
            initial_capital=1_000_000.0,
            risk_per_trade_pct=0.002,
            max_single_loss_pct=0.001,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            use_position_sizing=False,
            max_position_scale=0.10,
            max_symbol_notional_pct=1.0,
            max_concurrent_positions_per_symbol=3,
            max_concurrent_positions_total=10,
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
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["blocked_symbol_concurrent_rows"]), 1)
        statuses = trades["execution_status"].astype(str).tolist()
        self.assertEqual(statuses.count("blocked_symbol_concurrent"), 1)

    def test_evaluate_oot_real_execution_applies_roll_cost(self) -> None:
        """P0.3: OOT 评估应扣减换月/展期成本。"""
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-02 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-02 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-02-01 09:00:00"]),
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["day"],
                "signal_type": ["tight_range_breakout"],
                "side": ["long"],
                "pred_split": ["test"],
                "window_id": [0],
                "is_executed": [1],
                "entry_price": [100.0],
                "future_mfe_atr": [2.0],
                "future_mae_atr": [0.0],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_intrabar_stop_tracking=False,
            use_portfolio_constraints=False,
            use_roll_cost=True,
            default_roll_cost_pct_per_year=0.12,
            initial_capital=1000.0,
            risk_per_trade_pct=0.01,
            max_single_loss_pct=1.0,
            use_position_sizing=False,
            max_position_scale=1.0,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 1)
        self.assertIn("roll_cost", trades.columns)
        self.assertIn("roll_cost_total", summary.columns)
        self.assertAlmostEqual(float(trades.iloc[0]["gross_pnl"]), 20.0, places=6)
        self.assertGreater(float(trades.iloc[0]["roll_cost"]), 0.0)
        self.assertLess(float(trades.iloc[0]["net_pnl"]), 20.0)
        self.assertAlmostEqual(
            float(summary.iloc[0]["roll_cost_total"]),
            float(trades.iloc[0]["roll_cost"]),
            places=6,
        )

    def test_build_valid_test_gap_alerts_flags_only_over_threshold(self) -> None:
        metrics = pd.DataFrame(
            [
                {"signal_type": "A", "window_id": 0, "model": "m", "split": "valid", "auc": 0.80},
                {"signal_type": "A", "window_id": 0, "model": "m", "split": "test", "auc": 0.55},
                {"signal_type": "B", "window_id": 0, "model": "m", "split": "valid", "auc": 0.62},
                {"signal_type": "B", "window_id": 0, "model": "m", "split": "test", "auc": 0.60},
            ]
        )
        out = _build_valid_test_gap_alerts(metrics, max_gap=0.10)
        self.assertEqual(len(out), 1)
        self.assertEqual(str(out.iloc[0]["signal_type"]), "A")
        self.assertAlmostEqual(float(out.iloc[0]["gap"]), 0.25, places=6)

    def test_build_top_feature_concentration_alerts_flags_top1_above_threshold(self) -> None:
        imp = pd.DataFrame(
            [
                {"signal_type": "A", "window_id": 0, "model": "m", "feature": "x", "importance": 0.9},
                {"signal_type": "A", "window_id": 0, "model": "m", "feature": "y", "importance": 0.1},
                {"signal_type": "B", "window_id": 0, "model": "m", "feature": "u", "importance": 0.4},
                {"signal_type": "B", "window_id": 0, "model": "m", "feature": "v", "importance": 0.3},
                {"signal_type": "B", "window_id": 0, "model": "m", "feature": "w", "importance": 0.3},
            ]
        )
        out = _build_top_feature_concentration_alerts(imp, top1_thresh=0.5)
        self.assertEqual(len(out), 1)
        self.assertEqual(str(out.iloc[0]["signal_type"]), "A")
        self.assertEqual(str(out.iloc[0]["top_feature"]), "x")
        self.assertAlmostEqual(float(out.iloc[0]["top1_pct_in_top10"]), 0.9, places=6)
        self.assertIn("top1_pct_in_top10", out.columns)

    def test_filter_model_leakage_features_blocks_centered_and_lookahead_naming(self) -> None:
        cols = [
            "generic_centered_ma_20",
            "generic_rollingmax_5",
            "feature_lookahead_score",
            "generic_aft_high",
            "generic_shiftneg_close",
            "feature_breakout_score",
        ]
        out = _filter_model_leakage_features(cols, model_name="trade_filter")
        self.assertNotIn("generic_centered_ma_20", out)
        self.assertNotIn("generic_rollingmax_5", out)
        self.assertNotIn("feature_lookahead_score", out)
        self.assertNotIn("generic_aft_high", out)
        self.assertNotIn("generic_shiftneg_close", out)
        self.assertIn("feature_breakout_score", out)

    def test_filter_model_leakage_features_for_regime_classifier(self) -> None:
        cols = [
            "feature_trend_score",
            "feature_trend_dir",
            "generic_auto_trend",
            "generic_model_regime_state",
            "feature_breakout_score",
            "generic_auto_vol_ratio",
        ]
        out = _filter_model_leakage_features(cols, model_name="regime_classifier")
        self.assertNotIn("feature_trend_score", out)
        self.assertNotIn("feature_trend_dir", out)
        self.assertNotIn("generic_auto_trend", out)
        self.assertNotIn("generic_model_regime_state", out)
        self.assertIn("feature_breakout_score", out)
        self.assertIn("generic_auto_vol_ratio", out)

    def test_filter_model_leakage_features_blocks_target_like_prefixed_columns(self) -> None:
        cols = [
            "generic_atr_based_target_long",
            "generic_future_edge",
            "feature_next_bar_gap",
            "generic_forward_score",
            "feature_breakout_score",
        ]
        out = _filter_model_leakage_features(cols, model_name="trade_filter")
        self.assertNotIn("generic_atr_based_target_long", out)
        self.assertNotIn("generic_future_edge", out)
        self.assertNotIn("feature_next_bar_gap", out)
        self.assertNotIn("generic_forward_score", out)
        self.assertIn("feature_breakout_score", out)

    def test_apply_causality_manifest_filter_blocks_noncausal_features(self) -> None:
        """P1.5: manifest 标记为 non-causal 的特征必须被过滤。"""
        with tempfile.TemporaryDirectory(prefix="cta_causal_manifest_") as td:
            p = Path(td) / "causality_manifest.csv"
            pd.DataFrame(
                [
                    {"feature": "feature_ok", "causal": 1},
                    {"feature": "generic_centered_swing", "causal": 0},
                ]
            ).to_csv(p, index=False, encoding="utf-8-sig")
            out = _apply_causality_manifest_filter(
                ["feature_ok", "generic_centered_swing", "feature_unknown"],
                manifest_path=p,
            )
        self.assertIn("feature_ok", out)
        self.assertIn("feature_unknown", out)
        self.assertNotIn("generic_centered_swing", out)

    def test_build_symbol_cluster_sample_weight_compensates_dense_cluster(self) -> None:
        """P1.2 双层加权后：cluster 内 samples 少的 symbol 权重 ≥ samples 多的同伴，
        而单 symbol cluster 的总权重应 > 拥挤 cluster 的 symbol 权重。"""
        df = pd.DataFrame(
            {
                # RB / HC 同 black cluster，但 RB 样本 2 条、HC 仅 1 条
                # AU 独占 precious cluster（cluster=1 symbol）
                "symbol": ["RB0", "HC0", "AU0", "RB0", "AU0"],
                "feature_x": [1, 2, 3, 4, 5],
            }
        )
        w = _build_symbol_cluster_sample_weight(df)
        self.assertEqual(len(w), len(df))
        rb_w = float(w.iloc[0])
        hc_w = float(w.iloc[1])
        au_w = float(w.iloc[2])
        # 同 cluster 内 sample 少的 HC 应 ≥ sample 多的 RB
        self.assertGreaterEqual(hc_w, rb_w)
        # 单 symbol cluster (AU) 总权重应高于拥挤 cluster (RB)
        self.assertGreater(au_w, rb_w)

    def test_build_walk_forward_windows_purges_cross_boundary_exit_rows(self) -> None:
        df = pd.DataFrame(
            {
                "row_id": ["drop_train", "keep_train", "drop_valid", "keep_valid", "keep_test"],
                "datetime": pd.to_datetime(
                    [
                        "2020-01-01",
                        "2020-01-03",
                        "2020-01-05",
                        "2020-01-07",
                        "2020-01-09",
                    ]
                ),
                "exit_datetime": pd.to_datetime(
                    [
                        "2020-01-06",  # cross train_end -> purge from train
                        "2020-01-03",
                        "2020-01-09",  # cross valid_end -> purge from valid
                        "2020-01-07",
                        "2020-01-10",
                    ]
                ),
                "feature_x": [1.0, 2.0, 3.0, 4.0, 5.0],
                "label_class": [1, 0, 1, 0, 1],
                "future_mfe_atr": [1.0, 1.0, 1.0, 1.0, 1.0],
                "future_mae_atr": [0.5, 0.5, 0.5, 0.5, 0.5],
            }
        )
        wins = _build_walk_forward_windows(
            df,
            train_end="2020-01-04",
            valid_end="2020-01-08",
            max_windows=1,
            window_mode="expanding",
        )
        self.assertEqual(len(wins), 1)
        win = wins[0]
        self.assertListEqual(win.train["row_id"].tolist(), ["keep_train"])
        self.assertListEqual(win.valid["row_id"].tolist(), ["keep_valid"])
        self.assertListEqual(win.test["row_id"].tolist(), ["keep_test"])

    def test_run_pipeline_smoke(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_model_pipeline_") as td:
            out = run_model_pipeline(
                symbol="RB0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
            )
            self.assertTrue(out.report_path.exists())
            self.assertTrue(out.prediction_path.exists())
            self.assertTrue(out.metrics_path.exists())
            self.assertTrue(out.top_feature_importance_path.exists())
            self.assertTrue(out.oot_monthly_path.exists())
            self.assertTrue(out.oot_summary_path.exists())
            self.assertTrue(out.oot_trades_path.exists())
            self.assertTrue(out.html_report_path.exists())
            pred = pd.read_csv(out.prediction_path)
            self.assertIn("pred_split", pred.columns)
            metrics = pd.read_csv(out.metrics_path)
            for col in (
                "split_start",
                "split_end",
                "split_sample_count",
                "split_executed_count",
                "split_non_executed_count",
                "feature_count",
                "feature_null_ratio_mean",
                "feature_null_ratio_max",
                "feature_null_feature_count",
                "feature_all_null_count",
                "label_ic_abs_mean",
                "regime_ic_abs_mean",
                "return_ic_abs_mean",
            ):
                self.assertIn(col, metrics.columns)
            imp = pd.read_csv(out.top_feature_importance_path)
            self.assertIn("model", imp.columns)
            self.assertIn("feature", imp.columns)
            self.assertIn("importance", imp.columns)
            self.assertIn("feature_meaning", imp.columns)
            oot_monthly = pd.read_csv(out.oot_monthly_path)
            for col in ("month", "trade_count", "win_count", "loss_count", "monthly_return_pct", "cum_return_pct"):
                self.assertIn(col, oot_monthly.columns)
            oot_summary = pd.read_csv(out.oot_summary_path)
            for col in ("trade_count", "gross_pnl", "monthly_sharpe", "max_drawdown_pct", "monthly_excess_return_pct"):
                self.assertIn(col, oot_summary.columns)
            self.assertIn("blocked_htf_rows", oot_summary.columns)
            self.assertIn("blocked_ranker_rows", oot_summary.columns)
            self.assertIn("blocked_throttle_rows", oot_summary.columns)
            self.assertIn("blocked_pyramid_rows", oot_summary.columns)
            self.assertIn("trailing_stop_exit_rows", oot_summary.columns)
            oot_trades = pd.read_csv(out.oot_trades_path)
            for col in (
                "datetime",
                "symbol",
                "side",
                "trade_return_pct",
                "equity_before",
                "equity_after",
                "entry_price",
                "exit_datetime",
                "exit_price_ref",
                "position_notional",
                "position_qty",
                "max_loss_amount",
                "entry_amount",
                "exit_amount",
                "pnl_amount",
                "pos_id",
                "layer_id",
                "throttle_level_at_entry",
                "ranker_score",
            ):
                self.assertIn(col, oot_trades.columns)
            throttle_logs = list(out.output_dir.glob("*_throttle_log.csv"))
            position_lifetimes = list(out.output_dir.glob("*_oot_position_lifetime.csv"))
            self.assertTrue(throttle_logs, "throttle_log.csv should be generated")
            self.assertTrue(position_lifetimes, "oot_position_lifetime.csv should be generated")
            throttle_df = pd.read_csv(throttle_logs[0])
            for col in ("timestamp", "equity", "drawdown_pct", "level", "score_threshold"):
                self.assertIn(col, throttle_df.columns)
            pos_life_df = pd.read_csv(position_lifetimes[0])
            for col in ("pos_id", "layer_count", "peak_notional"):
                self.assertIn(col, pos_life_df.columns)
            report_text = out.report_path.read_text(encoding="utf-8")
            self.assertIn("## Process Steps", report_text)
            self.assertIn("## Split Diagnostics", report_text)
            self.assertIn("## OOT Real Execution Evaluation", report_text)
            self.assertIn("html_report", report_text)
            self.assertIn("oot_throttle_log_csv", report_text)
            self.assertIn("oot_position_lifetime_csv", report_text)

    def test_run_pipeline_support_all_intervals(self) -> None:
        intervals = ("day", "60min", "30min", "15min", "5min", "min")
        with tempfile.TemporaryDirectory(prefix="cta_all_interval_pipeline_") as td:
            for interval in intervals:
                out = run_model_pipeline(
                    symbol="NOPE0",
                    exchange="SHFE",
                    interval=interval,
                    start_date="2018-01-01",
                    end_date="2018-12-31",
                    output_root=Path(td),
                    train_end="2018-06-30",
                    valid_end="2018-09-30",
                    synthetic_periods=120,
                    by_signal_type=False,
                    max_walk_forward_windows=1,
                )
                self.assertTrue(out.report_path.exists())
                self.assertTrue(out.prediction_path.exists())
                self.assertTrue(out.metrics_path.exists())

    def test_pipeline_auto_enriches_features_when_generic_dir_missing(self) -> None:
        """当 cta/data/feature/<interval>/<symbol> 缺失时，pipeline 应自动补充
        通用 fallback 特征和模型专用特征，不应中断训练。
        """
        with tempfile.TemporaryDirectory(prefix="cta_no_generic_dir_") as td:
            out = run_model_pipeline(
                symbol="NOPE0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2018-12-31",
                output_root=Path(td),
                train_end="2018-06-30",
                valid_end="2018-09-30",
                synthetic_periods=160,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            ft = pd.read_csv(out.feature_table_path)
            self.assertIn("generic_auto_close", ft.columns)
            self.assertIn("generic_model_trade_setup", ft.columns)
            self.assertIn("generic_model_regime_state", ft.columns)
            self.assertIn("generic_model_mfe_edge", ft.columns)


if __name__ == "__main__":
    unittest.main()
