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


class TestModelPipeline(unittest.TestCase):
    def test_validate_stop_loss_pct_consistency_default_passes(self) -> None:
        _validate_stop_loss_pct_consistency()

    def test_validate_stop_loss_pct_consistency_raises_when_gap_too_large(self) -> None:
        with self.assertRaises(RuntimeError):
            _validate_stop_loss_pct_consistency(
                oot_stop_loss_pct=0.001,
                label_stop_loss_pct=0.02,
            )

    # ------------------ HPO: select by valid AUC + gap<=2%, OOT excluded -----
    def test_select_best_param_trial_prefers_gap_constrained_candidate(self) -> None:
        trials = [
            {"name": "overfit_high_valid", "train_auc": 0.97, "valid_auc": 0.90, "oot_auc": 0.10},
            {"name": "stable", "train_auc": 0.87, "valid_auc": 0.85, "oot_auc": 0.08},
        ]
        picked = _select_best_param_trial(trials, max_auc_gap=0.02)
        self.assertEqual(str(picked["name"]), "stable")

    def test_select_best_param_trial_fallbacks_to_min_gap_when_no_candidate_under_threshold(self) -> None:
        trials = [
            {"name": "gap_8pct", "train_auc": 0.90, "valid_auc": 0.82, "oot_auc": 0.50},
            {"name": "gap_3pct", "train_auc": 0.86, "valid_auc": 0.83, "oot_auc": 0.10},
            {"name": "gap_5pct", "train_auc": 0.92, "valid_auc": 0.87, "oot_auc": 0.90},
        ]
        picked = _select_best_param_trial(trials, max_auc_gap=0.02)
        self.assertEqual(str(picked["name"]), "gap_3pct")

    def test_select_best_param_trial_does_not_use_oot_for_selection(self) -> None:
        trials = [
            {"name": "best_valid_low_oot", "train_auc": 0.84, "valid_auc": 0.82, "oot_auc": 0.10},
            {"name": "worse_valid_high_oot", "train_auc": 0.83, "valid_auc": 0.81, "oot_auc": 0.99},
        ]
        picked = _select_best_param_trial(trials, max_auc_gap=0.02)
        self.assertEqual(str(picked["name"]), "best_valid_low_oot")

    def test_build_candidate_table_allows_synthetic_fallback_for_missing_symbol(self) -> None:
        df, ex = _build_candidate_table(
            symbol="NOPE0",
            exchange="SHFE",
            interval="60min",
            start_date="2018-01-01",
            end_date="2019-12-31",
            trade_side_mode="both",
            synthetic_periods=120,
            allow_synthetic_fallback=True,
        )
        self.assertFalse(df.empty)
        self.assertIn("symbol", df.columns)
        self.assertEqual(str(ex).upper(), "SHFE")

    def test_build_candidate_table_disables_synthetic_fallback_when_required(self) -> None:
        with self.assertRaises(Exception):
            _build_candidate_table(
                symbol="NOPE0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                trade_side_mode="both",
                synthetic_periods=120,
                allow_synthetic_fallback=False,
            )

    def test_pool_mode_raises_when_no_real_data_symbol_available(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_pool_no_real_") as td:
            with self.assertRaises(ValueError):
                run_model_pipeline(
                    symbol="POOL",
                    exchange="SHFE",
                    interval="60min",
                    start_date="2018-01-01",
                    end_date="2019-12-31",
                    output_root=Path(td),
                    train_end="2018-12-31",
                    valid_end="2019-06-30",
                    synthetic_periods=120,
                    by_signal_type=True,
                    max_walk_forward_windows=2,
                    pool_symbols=[("NOPE0", "SHFE"), ("NONE0", "DCE")],
                )

    def test_pool_mode_survives_when_generic_feature_dir_missing(self) -> None:
        """缺失 cta/data/feature/<interval>/<symbol> 时，POOL 仍可退化用 candidate-only 继续。"""
        with tempfile.TemporaryDirectory(prefix="cta_pool_missing_feat_") as td:
            missing_feature_root = Path(td) / "feature_missing"
            out = run_model_pipeline(
                symbol="POOL",
                exchange=None,
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                feature_root=missing_feature_root,
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=120,
                by_signal_type=False,
                max_walk_forward_windows=1,
                pool_symbols=[("RB0", "SHFE")],
                min_used_symbols=1,
            )
            self.assertTrue(out.report_path.exists())
            self.assertTrue(out.feature_table_path.exists())
            self.assertTrue(out.metrics_path.exists())

    def test_pool_mode_guard_raises_when_used_symbols_below_minimum(self) -> None:
        """P1/PL1: 防止 POOL 只有单品种却误当池化模型。"""
        with tempfile.TemporaryDirectory(prefix="cta_pool_guard_") as td:
            with self.assertRaises(ValueError):
                run_model_pipeline(
                    symbol="POOL",
                    exchange=None,
                    interval="60min",
                    start_date="2018-01-01",
                    end_date="2019-12-31",
                    output_root=Path(td),
                    train_end="2018-12-31",
                    valid_end="2019-06-30",
                    synthetic_periods=120,
                    by_signal_type=False,
                    max_walk_forward_windows=1,
                    pool_symbols=[("RB0", "SHFE"), ("NOPE0", "DCE")],
                    min_used_symbols=2,
                )

    def test_pipeline_writes_calibration_joblib_files(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_calibration_") as td:
            out = run_model_pipeline(
                symbol="RB0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=120,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            cal_files = sorted(out.output_dir.glob("models/*/window_*/**/*_calibration.joblib"))
            self.assertTrue(cal_files, "expected at least one *_calibration.joblib file")
            names = {p.name for p in cal_files}
            self.assertIn("trade_filter_calibration.joblib", names)
            self.assertIn("final_decision_stack_calibration.joblib", names)

    def test_build_last_oot_decile_table_uses_last_window_test_only(self) -> None:
        df = pd.DataFrame(
            {
                "window_id": [0, 0, 1, 1, 1, 1],
                "pred_split": ["test", "valid", "test", "test", "test", "train"],
                "trade_filter_prob": [0.10, 0.90, 0.20, 0.40, 0.80, 0.95],
                "future_mfe_atr": [0.1, 0.2, 0.6, 1.0, 1.8, 3.0],
                "future_mae_atr": [0.2, 0.1, 0.2, 0.4, 0.3, 0.2],
                "is_executed": [1, 1, 1, 0, 1, 1],
            }
        )
        out = _build_last_oot_decile_table(df, bins=10)
        self.assertFalse(out.empty)
        # only window_id=1 & pred_split=test & is_executed==1 rows should be used: 2 rows
        self.assertEqual(int(out["sample_count"].sum()), 2)
        self.assertTrue((out["window_id"] == 1).all())
        self.assertTrue((out["pred_split"] == "test").all())
        self.assertTrue((out["executed_rate"] == 1.0).all())

    def test_training_columns_keep_non_executed_samples(self) -> None:
        """训练阶段应保留未成交样本（is_executed=0）用于 trade_filter/regime 学习。"""
        df = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-01", "2020-01-02", "2020-01-03"]),
                "candidate_status": ["filled", "not_triggered", "filtered"],
                "is_executed": [1, 0, 0],
                "future_mfe_atr": [1.0, 0.0, 0.2],
                "future_mae_atr": [0.3, 0.0, 0.1],
                "label_class": [1, 0, 0],
                "signal_type": ["donchian_breakout", "donchian_breakout", "donchian_breakout"],
                "side": ["long", "long", "short"],
                "feature_x": [1.0, 0.5, -0.2],
            }
        )
        out = _ensure_training_columns(df)
        self.assertEqual(len(out), 3)
        self.assertEqual(int((out["is_executed"] == 0).sum()), 2)

    def test_build_last_oot_decile_table_builds_10_bins_when_enough_samples(self) -> None:
        n = 100
        score = np.linspace(0.01, 0.99, n)
        df = pd.DataFrame(
            {
                "window_id": [3] * n,
                "pred_split": ["test"] * n,
                "trade_filter_prob": score,
                "future_mfe_atr": score * 2.0,
                "future_mae_atr": np.zeros(n),
                "is_executed": np.ones(n, dtype=int),
            }
        )
        out = _build_last_oot_decile_table(df, bins=10)
        self.assertEqual(len(out), 10)
        self.assertEqual(int(out["sample_count"].sum()), n)
        self.assertEqual(out["decile"].min(), 1)
        self.assertEqual(out["decile"].max(), 10)
        self.assertIn("avg_return_atr", out.columns)
        self.assertIn("total_return_atr", out.columns)

    def test_evaluate_oot_real_execution_numeric_correctness(self) -> None:
        """P1: OOT 真实成交评估应按资金曲线口径得到可复核结果。"""
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-10", "2020-01-20", "2020-02-10", "2020-03-10"]),
                "symbol": ["RB0"] * 4,
                "exchange": ["SHFE"] * 4,
                "interval": ["60min"] * 4,
                "signal_type": ["tight_range_breakout"] * 4,
                "side": ["long", "short", "long", "short"],
                "window_id": [1, 1, 1, 1],
                "pred_split": ["test", "test", "test", "test"],
                "is_executed": [1, 1, 1, 1],
                "future_mfe_atr": [2.0, 0.0, 4.0, 0.0],
                "future_mae_atr": [1.0, 1.0, 1.0, 2.0],
            }
        )
        cfg = OotEvaluationConfig(
            use_test_split_only=True,
            use_last_window_only=False,
            require_executed_only=True,
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            mae_penalty=1.0,
            initial_capital=1000.0,
            risk_per_trade_pct=0.1,
            max_single_loss_pct=1.0,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
            use_portfolio_constraints=False,
            use_intrabar_stop_tracking=False,
            max_position_scale=1.0,
            max_symbol_notional_pct=1.0,
            max_concurrent_positions_per_symbol=10,
            max_concurrent_positions_total=20,
            # P0 fix：trade_return floor 改用 intrabar_stop_loss_pct（实际止损率），
            # 不再误用 max_single_loss_pct（权益占比）。设到合法上限 0.10 模拟"基本无 cap"。
            intrabar_stop_loss_pct=0.10,
        )
        monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(len(trades), 4)
        self.assertEqual(len(monthly), 3)
        # equity path (floor=-0.10): 1000 -> 1100 -> 990 (第二笔 -0.1 未触底) -> 1287 -> 1158.3 (第四笔 -0.2 被 floor 到 -0.10)
        self.assertAlmostEqual(float(trades["equity_before"].iloc[0]), 1000.0, places=6)
        self.assertAlmostEqual(float(trades["equity_after"].iloc[-1]), 1158.3, places=6)
        jan = monthly.loc[monthly["month"] == pd.Timestamp("2020-01-01")]
        self.assertEqual(len(jan), 1)
        self.assertEqual(int(jan["trade_count"].iloc[0]), 2)
        self.assertEqual(int(jan["win_count"].iloc[0]), 1)
        self.assertEqual(int(jan["loss_count"].iloc[0]), 1)
        self.assertAlmostEqual(float(jan["monthly_return_pct"].iloc[0]), -0.01, places=6)
        s0 = summary.iloc[0]
        self.assertEqual(int(s0["trade_count"]), 4)
        # gross_pnl 用未截断的 gross_ret_pct：100 - 110 + 297 - 257.4 = 29.6
        self.assertAlmostEqual(float(s0["gross_pnl"]), 29.6, places=6)
        # net_pnl 用截断后的 net_ret_pct (floor=-0.10)：100 - 110 + 297 - 128.7 = 158.3
        self.assertAlmostEqual(float(s0["net_pnl"]), 158.3, places=6)
        self.assertAlmostEqual(float(s0["total_return_pct"]), 0.1583, places=6)
        # max_dd = 1158.3 / 1287 - 1 ≈ -0.10
        self.assertAlmostEqual(float(s0["max_drawdown_pct"]), -0.10, places=4)

    def test_evaluate_oot_real_execution_adds_position_sizing_fields(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-10", "2020-01-20"]),
                "signal_datetime": pd.to_datetime(["2020-01-09", "2020-01-19"]),
                "exit_datetime": pd.to_datetime(["2020-01-11", "2020-01-21"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "60min"],
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "short"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [1, 1],
                "entry_price": [100.0, 200.0],
                "exit_price_ref": [101.0, 198.0],
                "future_mfe_atr": [1.0, 0.0],
                "future_mae_atr": [0.0, 1.0],
                "pred_mae_atr": [4.0, 0.5],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            mae_penalty=1.0,
            initial_capital=1000.0,
            risk_per_trade_pct=0.002,
            max_single_loss_pct=0.002,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
            use_position_sizing=True,
            min_pred_mae_atr_for_sizing=0.5,
            max_position_scale=1.0,
            max_symbol_notional_pct=1.0,
            max_concurrent_positions_per_symbol=10,
            max_concurrent_positions_total=20,
            use_portfolio_constraints=False,
            use_intrabar_stop_tracking=False,
            # P0 fix：该测试本意是验证 sizing 在"超紧止损"下能打满 max_position_scale。
            # 显式设 0.001 保留旧测试意图（与新默认 0.01 的"合理止损"区分）。
            intrabar_stop_loss_pct=0.001,
        )
        _monthly, _summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertAlmostEqual(float(trades.iloc[0]["position_scale"]), 1.0, places=6)
        self.assertAlmostEqual(float(trades.iloc[0]["max_loss_amount"]), 2.0, places=6)
        self.assertAlmostEqual(float(trades.iloc[0]["position_notional"]), 1000.0, places=6)
        self.assertAlmostEqual(float(trades.iloc[0]["position_qty"]), 10.0, places=6)
        self.assertAlmostEqual(float(trades.iloc[0]["entry_amount"]), 1000.0, places=6)
        # 买入时总持仓资金（含新开这笔）应等于当笔 notional（该用例下无并发持仓）。
        self.assertAlmostEqual(float(trades.iloc[0]["open_notional_at_entry"]), 1000.0, places=6)
        self.assertAlmostEqual(
            float(trades.iloc[0]["pnl_amount"]),
            float(trades.iloc[0]["net_pnl"]),
            places=9,
        )

    def test_evaluate_oot_real_execution_intrabar_stop_tracking(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 12:00:00"]),
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["day"],
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
                "pred_split": ["test"],
                "window_id": [0],
                "is_executed": [1],
                "entry_price": [100.0],
                "future_mfe_atr": [5.0],
                "future_mae_atr": [0.0],
            }
        )

        def provider(
            symbol: str,
            exchange: str,
            start_ts: pd.Timestamp,
            end_ts: pd.Timestamp,
            interval: str,
        ) -> pd.DataFrame:
            self.assertEqual(symbol, "RB0")
            self.assertEqual(exchange, "SHFE")
            self.assertEqual(interval, "60min")
            return pd.DataFrame(
                {
                    "datetime": pd.to_datetime(
                        [
                            "2020-01-06 09:00:00",
                            "2020-01-06 10:00:00",
                            "2020-01-06 11:00:00",
                        ]
                    ),
                    "open": [100.0, 99.95, 99.70],
                    "high": [100.10, 100.00, 99.90],
                    "low": [99.97, 99.60, 99.50],
                    "close": [100.00, 99.80, 99.60],
                }
            )

        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            mae_penalty=1.0,
            initial_capital=1000.0,
            risk_per_trade_pct=0.002,
            max_single_loss_pct=0.001,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            use_position_sizing=False,
            use_portfolio_constraints=False,
            use_intrabar_stop_tracking=True,
            intrabar_tracking_interval="60min",
            intrabar_stop_loss_pct=0.001,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg, intrabar_bar_provider=provider)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 1)
        self.assertEqual(int(summary.iloc[0]["stop_loss_exit_rows"]), 1)
        self.assertEqual(str(trades.iloc[0]["exit_reason"]), "stop_loss")
        self.assertEqual(int(trades.iloc[0]["stop_triggered"]), 1)
        self.assertEqual(pd.Timestamp(trades.iloc[0]["final_exit_datetime"]), pd.Timestamp("2020-01-06 10:00:00"))
        self.assertAlmostEqual(float(trades.iloc[0]["trade_return_pct"]), -0.001, places=6)

    def test_evaluate_oot_real_execution_intrabar_trailing_stop_tracking(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 12:00:00"]),
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["30min"],
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
                "pred_regime_label": ["trend_up"],
                "pred_split": ["test"],
                "window_id": [0],
                "is_executed": [1],
                "entry_price": [100.0],
                "atr_pct_at_entry": [0.01],
                "future_mfe_atr": [5.0],
                "future_mae_atr": [0.0],
            }
        )

        def provider(
            symbol: str,
            exchange: str,
            start_ts: pd.Timestamp,
            end_ts: pd.Timestamp,
            interval: str,
        ) -> pd.DataFrame:
            self.assertEqual(symbol, "RB0")
            self.assertEqual(exchange, "SHFE")
            self.assertEqual(interval, "60min")
            return pd.DataFrame(
                {
                    "datetime": pd.to_datetime(
                        [
                            "2020-01-06 09:00:00",
                            "2020-01-06 10:00:00",
                            "2020-01-06 11:00:00",
                        ]
                    ),
                    "open": [100.0, 103.0, 101.5],
                    "high": [100.0, 104.0, 102.0],
                    "low": [100.0, 103.0, 100.5],
                    "close": [100.0, 103.5, 101.0],
                }
            )

        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            mae_penalty=1.0,
            initial_capital=1000.0,
            risk_per_trade_pct=0.002,
            max_single_loss_pct=0.001,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            use_position_sizing=False,
            use_portfolio_constraints=False,
            use_intrabar_stop_tracking=True,
            intrabar_tracking_interval="60min",
            intrabar_stop_loss_pct=0.02,
            use_portfolio_logic_runtime=True,
            portfolio_logic=PortfolioLogicConfig(
                enable_htf_gate=False,
                enable_ranker=False,
                enable_risk_throttle=False,
                enable_pyramid=False,
            ),
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg, intrabar_bar_provider=provider)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 1)
        self.assertEqual(int(summary.iloc[0]["trailing_stop_exit_rows"]), 1)
        self.assertEqual(str(trades.iloc[0]["exit_reason"]), "trailing_stop")
        self.assertEqual(int(trades.iloc[0]["trailing_activated"]), 1)
        self.assertEqual(pd.Timestamp(trades.iloc[0]["final_exit_datetime"]), pd.Timestamp("2020-01-06 11:00:00"))
        self.assertAlmostEqual(float(trades.iloc[0]["final_exit_price"]), 101.0, places=6)

    def test_evaluate_oot_real_execution_portfolio_logic_htf_blocks_conflict(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:00:00", "2020-01-06 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:00:00", "2020-01-06 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 15:00:00", "2020-01-06 15:00:00", "2020-01-06 11:00:00"]),
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["day", "60min", "30min"],
                "signal_type": ["htf_ref", "htf_ref", "donchian_breakout"],
                "side": ["long", "long", "short"],
                "pred_regime_label": ["trend_up", "trend_up", "trend_up"],
                "pred_split": ["test", "test", "test"],
                "window_id": [0, 0, 0],
                "is_executed": [0, 0, 1],
                "entry_price": [100.0, 100.0, 100.0],
                "future_mfe_atr": [0.0, 0.0, 1.0],
                "future_mae_atr": [0.0, 0.0, 0.2],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            require_executed_only=True,
            use_test_split_only=True,
            use_intrabar_stop_tracking=False,
            use_portfolio_constraints=False,
            use_position_sizing=False,
            use_portfolio_logic_runtime=True,
            portfolio_logic=PortfolioLogicConfig(
                enable_htf_gate=True,
                enable_ranker=False,
                enable_risk_throttle=False,
                enable_pyramid=False,
            ),
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 0)
        self.assertEqual(int(summary.iloc[0]["blocked_htf_rows"]), 1)
        self.assertEqual(int((trades["execution_status"] == "blocked_htf_gate").sum()), 1)

    def test_evaluate_oot_real_execution_portfolio_logic_htf_accepts_minute_aliases(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:00:00", "2020-01-06 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:00:00", "2020-01-06 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 15:00:00", "2020-01-06 15:00:00", "2020-01-06 11:00:00"]),
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["day", "minute60", "minute30"],
                "signal_type": ["htf_ref", "htf_ref", "donchian_breakout"],
                "side": ["long", "long", "long"],
                "pred_regime_label": ["trend_up", "trend_up", "trend_up"],
                "pred_split": ["test", "test", "test"],
                "window_id": [0, 0, 0],
                "is_executed": [0, 0, 1],
                "entry_price": [100.0, 100.0, 100.0],
                "future_mfe_atr": [0.0, 0.0, 1.0],
                "future_mae_atr": [0.0, 0.0, 0.2],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            require_executed_only=True,
            use_test_split_only=True,
            use_intrabar_stop_tracking=False,
            use_portfolio_constraints=False,
            use_position_sizing=False,
            use_portfolio_logic_runtime=True,
            portfolio_logic=PortfolioLogicConfig(
                enable_htf_gate=True,
                enable_ranker=False,
                enable_risk_throttle=False,
                enable_pyramid=False,
            ),
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 1)
        self.assertEqual(int(summary.iloc[0]["blocked_htf_rows"]), 0)
        self.assertEqual(str(trades.iloc[0]["execution_status"]), "executed")

    def test_evaluate_oot_real_execution_portfolio_logic_pyramid_layers(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 10:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 10:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 15:00:00", "2020-01-06 15:00:00"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "30min"],
                "signal_type": ["donchian_breakout", "atr_breakout"],
                "side": ["long", "long"],
                "pred_regime_label": ["trend_up", "trend_up"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [1, 1],
                "entry_price": [100.0, 101.0],
                "atr_pct_at_entry": [0.01, 0.01],
                "future_mfe_atr": [1.0, 1.0],
                "future_mae_atr": [0.2, 0.2],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_intrabar_stop_tracking=False,
            use_portfolio_constraints=False,
            use_position_sizing=False,
            use_portfolio_logic_runtime=True,
            portfolio_logic=PortfolioLogicConfig(
                enable_htf_gate=False,
                enable_ranker=False,
                enable_risk_throttle=False,
                enable_pyramid=True,
            ),
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 2)
        self.assertIn("pos_id", trades.columns)
        self.assertIn("layer_id", trades.columns)
        exec_trades = trades.loc[trades["execution_status"] == "executed"].copy()
        self.assertEqual(len(exec_trades), 2)
        self.assertEqual(int(exec_trades["layer_id"].min()), 0)
        self.assertEqual(int(exec_trades["layer_id"].max()), 1)
        self.assertEqual(exec_trades["pos_id"].nunique(), 1)

    def test_evaluate_oot_real_execution_portfolio_logic_risk_throttle_halts(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 10:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 10:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 09:30:00", "2020-01-06 10:30:00"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "60min"],
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "long"],
                "pred_regime_label": ["trend_up", "trend_up"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [1, 1],
                "entry_price": [100.0, 100.0],
                "future_mfe_atr": [0.0, 0.0],
                "future_mae_atr": [80.0, 1.0],  # first trade deep loss to trigger halt
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_intrabar_stop_tracking=False,
            use_portfolio_constraints=False,
            use_position_sizing=False,
            max_position_scale=1.0,
            risk_per_trade_pct=0.01,
            initial_capital=1000.0,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            use_portfolio_logic_runtime=True,
            portfolio_logic=PortfolioLogicConfig(
                enable_htf_gate=False,
                enable_ranker=False,
                enable_risk_throttle=True,
                enable_pyramid=False,
                risk_throttle=RiskThrottleConfig(
                    levels=(
                        ThrottleLevel("normal", 0.0, 0.005, 1.0, 1.0, 60.0, True, 2),
                        ThrottleLevel("halt", 0.005, 1.0, 0.0, 0.0, 100.0, False, 0),
                    )
                ),
            ),
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["blocked_throttle_rows"]), 1)
        self.assertEqual(int((trades["execution_status"] == "blocked_throttle_halt").sum()), 1)

    def test_evaluate_oot_real_execution_portfolio_logic_emits_extra_outputs(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 10:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 10:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 15:00:00", "2020-01-06 15:00:00"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "30min"],
                "signal_type": ["donchian_breakout", "atr_breakout"],
                "side": ["long", "long"],
                "pred_regime_label": ["trend_up", "trend_up"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [1, 1],
                "entry_price": [100.0, 101.0],
                "atr_pct_at_entry": [0.01, 0.01],
                "future_mfe_atr": [1.0, 1.0],
                "future_mae_atr": [0.2, 0.2],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_intrabar_stop_tracking=False,
            use_portfolio_constraints=False,
            use_position_sizing=False,
            use_portfolio_logic_runtime=True,
            portfolio_logic=PortfolioLogicConfig(
                enable_htf_gate=False,
                enable_ranker=False,
                enable_risk_throttle=True,
                enable_pyramid=True,
            ),
        )
        extra: dict[str, pd.DataFrame] = {}
        _monthly, _summary, _trades = _evaluate_oot_real_execution(pred, cfg=cfg, extra_outputs=extra)
        self.assertIn("throttle_log", extra)
        self.assertIn("position_lifetime", extra)
        self.assertFalse(extra["position_lifetime"].empty)
        self.assertEqual(int(extra["position_lifetime"].iloc[0]["layer_count"]), 2)

    def test_build_position_lifetime_table_computes_active_layers_and_peak_notional(self) -> None:
        trade_df = pd.DataFrame(
            {
                "pos_id": ["p1", "p1", "p1"],
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "side": ["long", "long", "long"],
                "execution_status": ["executed", "executed", "executed"],
                "entry_datetime": pd.to_datetime(
                    [
                        "2024-01-02 09:00:00",
                        "2024-01-02 10:00:00",
                        "2024-01-02 11:30:00",
                    ]
                ),
                "exit_datetime": pd.to_datetime(
                    [
                        "2024-01-02 11:00:00",
                        "2024-01-02 12:00:00",
                        "2024-01-02 13:00:00",
                    ]
                ),
                "layer_id": [0, 1, 2],
                "position_notional": [100_000.0, 50_000.0, 25_000.0],
            }
        )
        out = _build_position_lifetime_table(trade_df)
        self.assertEqual(len(out), 1)
        self.assertEqual(int(out.iloc[0]["layer_count"]), 3)
        self.assertEqual(int(out.iloc[0]["max_active_layers"]), 2)
        self.assertAlmostEqual(float(out.iloc[0]["peak_notional"]), 150_000.0, places=6)

    def test_evaluate_oot_real_execution_same_bar_stop_does_not_lock_leverage(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-07 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-07 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 12:00:00", "2020-01-07 12:00:00"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["day", "day"],
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "long"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [1, 1],
                "entry_price": [100.0, 100.0],
                "future_mfe_atr": [5.0, 5.0],
                "future_mae_atr": [0.0, 0.0],
            }
        )

        def provider(
            symbol: str,
            exchange: str,
            start_ts: pd.Timestamp,
            end_ts: pd.Timestamp,
            interval: str,
        ) -> pd.DataFrame:
            self.assertEqual(symbol, "RB0")
            self.assertEqual(exchange, "SHFE")
            self.assertEqual(interval, "60min")
            return pd.DataFrame(
                {
                    "datetime": [pd.Timestamp(start_ts)],
                    "open": [100.0],
                    "high": [100.1],
                    "low": [99.8],  # long stop 99.9 hit on entry bar
                    "close": [99.85],
                }
            )

        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            mae_penalty=1.0,
            initial_capital=1000.0,
            risk_per_trade_pct=0.002,
            max_single_loss_pct=0.001,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            use_position_sizing=False,
            use_portfolio_constraints=True,
            margin_rate=1.0,
            max_total_leverage=1.0,
            max_daily_new_notional_pct=10.0,
            weekly_max_drawdown_pct=1.0,
            block_new_entries_on_weekly_dd_breach=False,
            enforce_weekly_dd_budget_on_entry=False,
            use_intrabar_stop_tracking=True,
            intrabar_tracking_interval="60min",
            intrabar_stop_loss_pct=0.001,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg, intrabar_bar_provider=provider)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 2)
        self.assertEqual(int(summary.iloc[0]["blocked_leverage_rows"]), 0)
        self.assertTrue((trades["execution_status"].astype(str) == "executed").all())

    def test_evaluate_oot_real_execution_daily_position_cap_blocks_extra_entries(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 14:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 11:00:00", "2020-01-06 15:00:00"]),
                "symbol": ["RB0", "RB0"],
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
            max_symbol_notional_pct=1.0,
            max_concurrent_positions_per_symbol=10,
            max_concurrent_positions_total=20,
            use_portfolio_constraints=True,
            margin_rate=0.01,
            max_total_leverage=10.0,
            max_daily_new_notional_pct=1.0,
            weekly_max_drawdown_pct=1.0,
            enforce_weekly_dd_budget_on_entry=False,
            block_new_entries_on_weekly_dd_breach=False,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
            use_intrabar_stop_tracking=False,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 1)
        self.assertEqual(int(summary.iloc[0]["selected_rows"]), 2)
        self.assertEqual(int(summary.iloc[0]["blocked_rows"]), 1)
        self.assertEqual(int(summary.iloc[0]["blocked_daily_position_rows"]), 1)
        status = trades["execution_status"].astype(str).tolist()
        self.assertIn("executed", status)
        self.assertIn("blocked_daily_position", status)

    def test_evaluate_oot_real_execution_blocks_limit_move_entries(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-07 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-07 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 11:00:00", "2020-01-07 11:00:00"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["day", "day"],
                "signal_type": ["atr_breakout", "atr_breakout"],
                "side": ["long", "short"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [1, 1],
                "entry_price": [100.0, 100.0],
                "future_mfe_atr": [1.0, 1.0],
                "future_mae_atr": [0.0, 0.0],
                "feature_is_limit_up_close": [1, 0],
                "feature_is_limit_down_close": [0, 1],
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
            use_portfolio_constraints=False,
            use_intrabar_stop_tracking=False,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 0)
        self.assertEqual(int(summary.iloc[0]["blocked_limit_move_rows"]), 2)
        self.assertTrue((trades["execution_status"].astype(str) == "blocked_limit_move").all())

    def test_evaluate_oot_real_execution_weekly_drawdown_budget_caps_trades(self) -> None:
        n = 12
        dt = pd.date_range("2020-01-06 09:00:00", periods=n, freq="8h")
        pred = pd.DataFrame(
            {
                "datetime": dt,
                "exit_datetime": dt + pd.Timedelta(hours=1),
                "symbol": ["RB0"] * n,
                "exchange": ["SHFE"] * n,
                "interval": ["60min"] * n,
                "signal_type": ["donchian_breakout"] * n,
                "side": ["long"] * n,
                "pred_split": ["test"] * n,
                "window_id": [0] * n,
                "is_executed": [1] * n,
                "entry_price": [100.0] * n,
                # every trade will be clipped to -0.2%
                "future_mfe_atr": [0.0] * n,
                "future_mae_atr": [1.0] * n,
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
            max_symbol_notional_pct=1.0,
            max_concurrent_positions_per_symbol=10,
            max_concurrent_positions_total=20,
            use_portfolio_constraints=True,
            margin_rate=0.01,
            max_total_leverage=10.0,
            max_daily_new_notional_pct=100.0,
            weekly_max_drawdown_pct=0.02,
            enforce_weekly_dd_budget_on_entry=True,
            block_new_entries_on_weekly_dd_breach=True,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
            use_intrabar_stop_tracking=False,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        # 周回撤预算应触发裁剪/阻断，最终回撤不应突破 -2%。
        self.assertLess(int(summary.iloc[0]["trade_count"]), n)
        weekly_blocked = int(summary.iloc[0]["blocked_weekly_budget_rows"]) + int(
            summary.iloc[0]["blocked_weekly_drawdown_rows"]
        )
        self.assertGreaterEqual(weekly_blocked, 1)
        self.assertGreaterEqual(float(summary.iloc[0]["max_drawdown_pct"]), -0.0200001)

    def test_evaluate_oot_real_execution_symbol_notional_cap_blocks_second_entry(self) -> None:
        """单 symbol 名义金额 cap 在同 symbol 连续触发信号时把后续笔卡掉。"""
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-07 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-10 15:00:00", "2020-01-10 16:00:00"]),
                "symbol": ["RB0", "RB0"],
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
            max_symbol_notional_pct=0.30,
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
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        # 第一笔吃满单 symbol cap (=300)；第二笔触发时 sym_notional 已占满 → blocked_symbol_cap
        self.assertGreaterEqual(int(summary.iloc[0]["blocked_symbol_cap_rows"]), 1)
        statuses = trades["execution_status"].astype(str).tolist()
        self.assertIn("blocked_symbol_cap", statuses)

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

    def test_auto_enrich_candidate_features_numeric_correctness(self) -> None:
        """P1: _auto_enrich_candidate_features_for_models 应产出可校验的数值。"""
        df = pd.DataFrame(
            {
                "feature_open": [10.0, 5.0],
                "feature_high": [16.0, 5.0],
                "feature_low": [10.0, 5.0],
                "feature_close": [14.0, 5.0],
                "feature_volume": [100.0, 50.0],
                "feature_atr14": [2.0, 0.0],
                "feature_trend_score": [1.5, -1.0],
                "feature_breakout_score": [2.0, 0.5],
                "feature_setup_quality": [1.0, 0.2],
                "feature_tr_range_atr": [0.5, 1.0],
                "side": ["long", "short"],
                "signal_type": ["tight_range_breakout", "tight_range_breakout"],
            }
        )
        out = _auto_enrich_candidate_features_for_models(df, force_generic_fallback=True)
        # row0: range=6, body=4 -> body_ratio=2/3; vol_ratio=6/2=3
        self.assertAlmostEqual(float(out.loc[0, "generic_auto_body_ratio"]), 2.0 / 3.0, places=6)
        self.assertAlmostEqual(float(out.loc[0, "generic_auto_vol_ratio"]), 3.0, places=6)
        # row1: atr14=0 / range=0 时 ratio 应该被安全回填为 0
        self.assertAlmostEqual(float(out.loc[1, "generic_auto_body_ratio"]), 0.0, places=9)
        self.assertAlmostEqual(float(out.loc[1, "generic_auto_vol_ratio"]), 0.0, places=9)
        # row0: regime_state = trend + 0.15*side = 1.5 + 0.15*1
        self.assertAlmostEqual(float(out.loc[0, "generic_model_regime_state"]), 1.65, places=6)
        # row1: mfe_edge = (0.5 + 0.4*-1 + 0.3*0.2) - 0.4*1 = -0.24
        self.assertAlmostEqual(float(out.loc[1, "generic_model_mfe_edge"]), -0.24, places=6)
        # row1 short: side_interaction = -0.24 * (-1) = 0.24
        self.assertAlmostEqual(float(out.loc[1, "generic_model_mfe_side_interaction"]), 0.24, places=6)

    def test_run_pipeline_by_signal_type_walk_forward(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_signal_wf_pipeline_") as td:
            out = run_model_pipeline(
                symbol="MOCK0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2020-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=360,
                by_signal_type=True,
                max_walk_forward_windows=2,
            )
            metrics = pd.read_csv(out.metrics_path)
            self.assertIn("signal_type", metrics.columns)
            self.assertIn("window_id", metrics.columns)
            self.assertIn("model_kind", metrics.columns)
            self.assertGreaterEqual(metrics["signal_type"].nunique(), 2)
            self.assertGreaterEqual(metrics["window_id"].nunique(), 2)

    # ------------------ T-A: label-diversity must respect is_executed ----------

    def test_ensure_binary_label_diversity_does_not_relabel_not_triggered(self) -> None:
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=4, freq="D"),
                "label_class": [0, 0, 0, 0],
                "future_mfe_atr": [3.0, 3.0, 3.0, 3.0],
                "future_mae_atr": [0.0, 0.0, 0.0, 0.0],
                "is_executed": [1, 0, 0, 0],
                "atr_warmed": [1, 1, 1, 1],
            }
        )
        out = _ensure_binary_label_diversity(df)
        # Only the executed row should flip to 1; the other three must stay 0.
        self.assertEqual(int(out.loc[0, "label_class"]), 1)
        self.assertEqual(int(out.loc[1, "label_class"]), 0)
        self.assertEqual(int(out.loc[2, "label_class"]), 0)
        self.assertEqual(int(out.loc[3, "label_class"]), 0)

    def test_ensure_binary_label_diversity_skips_atr_warmup(self) -> None:
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=2, freq="D"),
                "label_class": [0, 0],
                "future_mfe_atr": [4.0, 4.0],
                "future_mae_atr": [0.0, 0.0],
                "is_executed": [1, 1],
                "atr_warmed": [0, 1],  # row 0 in warmup -> must remain 0
            }
        )
        out = _ensure_binary_label_diversity(df)
        self.assertEqual(int(out.loc[0, "label_class"]), 0)
        self.assertEqual(int(out.loc[1, "label_class"]), 1)

    # ------------------ T-C: walk-forward window monotonicity -----------------

    def test_walk_forward_windows_monotonic_and_no_overlap(self) -> None:
        df = pd.DataFrame(
            {"datetime": pd.date_range("2018-01-01", "2021-12-31", freq="D")}
        )
        windows = _build_walk_forward_windows(
            df,
            train_end="2019-06-30",
            valid_end="2019-12-31",
            max_windows=4,
            window_mode="expanding",
        )
        self.assertGreaterEqual(len(windows), 2)
        # train_end strictly increasing
        ends = [w.train_end for w in windows]
        self.assertEqual(ends, sorted(ends))
        # test of window i and train of window i+1 should not overlap (no leakage)
        for w in windows:
            self.assertLess(w.train_end, w.valid_end)
            self.assertLess(w.valid_end, w.test_end)

    def test_walk_forward_windows_sliding_train_starts_advance(self) -> None:
        df = pd.DataFrame(
            {"datetime": pd.date_range("2018-01-01", "2021-12-31", freq="D")}
        )
        windows = _build_walk_forward_windows(
            df,
            train_end="2019-06-30",
            valid_end="2019-12-31",
            max_windows=3,
            window_mode="sliding",
        )
        self.assertGreaterEqual(len(windows), 2)
        # In sliding mode, each later window's train_start should advance vs the previous window
        train_starts: list[pd.Timestamp] = []
        for w in windows:
            train_dt = pd.to_datetime(w.train["datetime"], errors="coerce")
            train_starts.append(train_dt.min())
        for i in range(1, len(train_starts)):
            self.assertGreater(train_starts[i], train_starts[i - 1])

    def test_walk_forward_windows_rolling_mode_uses_fixed_span(self) -> None:
        """P1.1: rolling 模式支持固定 train/valid/test 年份窗口。"""
        n = 16 * 12
        dt = pd.date_range("2010-01-01", periods=n, freq="MS")
        df = pd.DataFrame(
            {
                "datetime": dt,
                "exit_datetime": dt,
                "label_class": [0, 1] * (n // 2),
                "future_mfe_atr": np.ones(n),
                "future_mae_atr": np.zeros(n),
            }
        )
        windows = _build_walk_forward_windows(
            df,
            train_end="2014-12-31",
            valid_end="2015-12-31",
            max_windows=4,
            window_mode="rolling",
            rolling_train_years=3,
            rolling_valid_years=1,
            rolling_test_years=1,
            rolling_step_years=1,
        )
        self.assertGreaterEqual(len(windows), 2)
        for w in windows:
            self.assertGreater(len(w.train), 0)
            self.assertGreater(len(w.valid), 0)
            self.assertGreater(len(w.test), 0)
            train_span_days = (
                pd.to_datetime(w.train["datetime"], errors="coerce").max()
                - pd.to_datetime(w.train["datetime"], errors="coerce").min()
            ).days
            # 3年窗口约 1095 天，考虑月频边界留足容忍区间
            self.assertGreaterEqual(train_span_days, 900)
            self.assertLessEqual(train_span_days, 1300)

    def test_walk_forward_windows_invalid_mode_raises(self) -> None:
        df = pd.DataFrame({"datetime": pd.date_range("2020-01-01", periods=400, freq="D")})
        with self.assertRaises(ValueError):
            _build_walk_forward_windows(
                df,
                train_end="2020-06-30",
                valid_end="2020-09-30",
                max_windows=2,
                window_mode="bogus",  # type: ignore[arg-type]
            )

    # ------------------ T-H: by_signal_type should drop constant code ---------

    def test_select_feature_columns_skips_string_columns(self) -> None:
        df = pd.DataFrame(
            {
                "feature_close": [1.0, 2.0, 3.0],
                "feature_label": ["good", "bad", "good"],  # string column should be skipped
                "feature_flag": [True, False, True],  # bool should be kept
            }
        )
        out_df, cols = _select_feature_columns(df)
        self.assertIn("feature_close", cols)
        self.assertIn("feature_flag", cols)
        self.assertNotIn("feature_label", cols)

    # ------------------ B2: 兜底分支缺列时不应崩 -----------------------------
    def test_select_feature_columns_fallback_handles_missing_side_and_signal_type(self) -> None:
        """B2: 没有 feature_*/generic_* 列且 side/signal_type/entry_price 也缺失时，
        兜底分支不应抛 AttributeError。
        """
        df = pd.DataFrame({"datetime": pd.date_range("2020-01-01", periods=3, freq="D")})
        out_df, cols = _select_feature_columns(df)
        self.assertGreater(len(cols), 0)
        for c in cols:
            self.assertTrue(c.startswith("feature_"))

    # ------------------ B1: train_exec 为空时跳过 MFE/MAE 训练 ----------------
    def test_train_mfe_mae_or_skip_returns_none_when_no_executed(self) -> None:
        df = pd.DataFrame(
            {
                "is_executed": [0, 0, 0, 0],
                "future_mfe_atr": [0.0, 0.0, 0.0, 0.0],
                "future_mae_atr": [0.0, 0.0, 0.0, 0.0],
                "feature_x": [1.0, 2.0, 3.0, 4.0],
            }
        )
        model, kind = _train_mfe_mae_or_skip(df, feature_columns=["feature_x"])
        self.assertIsNone(model)
        self.assertEqual(kind, MFE_MAE_KIND_SKIPPED_NO_EXEC)

    def test_train_mfe_mae_or_skip_returns_model_when_executed_present(self) -> None:
        rng = np.random.default_rng(0)
        df = pd.DataFrame(
            {
                "is_executed": [1] * 30,
                "future_mfe_atr": rng.uniform(0, 2, size=30),
                "future_mae_atr": rng.uniform(0, 2, size=30),
                "feature_x": rng.normal(size=30),
            }
        )
        model, kind = _train_mfe_mae_or_skip(df, feature_columns=["feature_x"])
        self.assertIsNotNone(model)
        self.assertIn(kind, {"random_forest", "dummy"})

    # ------------------ B6: pred_split 在 walk-forward 路径下永远是 test ------
    def test_run_pipeline_pred_split_is_test_only(self) -> None:
        """B6: walk-forward 已经过滤 test 空的窗口，prediction_df 的 pred_split
        理应只有 'test'，不会出现 'valid'/'train' 的 fallback 写入。
        """
        with tempfile.TemporaryDirectory(prefix="cta_pred_split_") as td:
            out = run_model_pipeline(
                symbol="SPLIT0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=240,
                by_signal_type=False,
                max_walk_forward_windows=2,
            )
            pred = pd.read_csv(out.prediction_path)
            if not pred.empty:
                self.assertEqual(set(pred["pred_split"].astype(str).unique()), {"test"})

    # ------------------ B7: load 旧 joblib 缺 model_kind 时显示 legacy_no_kind -
    def test_trade_filter_load_legacy_no_kind(self) -> None:
        """B7: 老 joblib 没有 model_kind 字段时，load 后 model_kind 应为 legacy_no_kind。"""
        rng = np.random.default_rng(1)
        df = pd.DataFrame(
            {
                "feature_x": rng.normal(size=80),
                "label_class": (rng.uniform(size=80) > 0.5).astype(int),
            }
        )
        m = TradeFilterModel(random_state=3).fit(df, feature_columns=["feature_x"], label_column="label_class")
        with tempfile.TemporaryDirectory(prefix="cta_legacy_kind_") as td:
            p = Path(td) / "tf.joblib"
            m.save(p)
            # 模拟旧 joblib 的内容：人工去掉 model_kind 字段后重新 dump。
            import joblib  # local import to avoid polluting module top
            obj = joblib.load(p)
            obj.pop("model_kind", None)
            joblib.dump(obj, p)
            reloaded = TradeFilterModel.load(p)
            self.assertEqual(reloaded.model_kind, "legacy_no_kind")

    # ------------------ M1: --interval 支持数组 ------------------------------
    def test_normalize_intervals_accepts_space_separated_values(self) -> None:
        """M1: argparse nargs='+' 拿到的就是 list[str]，应原样保留。"""
        out = _normalize_intervals(["day", "60min", "30min", "15min"])
        self.assertEqual(out, ("day", "60min", "30min", "15min"))

    def test_normalize_intervals_splits_comma_separated_values(self) -> None:
        """M1: 也允许逗号分隔（例如 --interval day,60min,30min）。"""
        out = _normalize_intervals(["day,60min", "30min"])
        self.assertEqual(out, ("day", "60min", "30min"))

    def test_normalize_intervals_dedupes_preserving_first_seen_order(self) -> None:
        """M1: 重复传入的 interval 应去重，按首次出现顺序保留。"""
        out = _normalize_intervals(["60min", "day", "60min", "30min", "day"])
        self.assertEqual(out, ("60min", "day", "30min"))

    def test_normalize_intervals_strips_whitespace_and_skips_empty(self) -> None:
        out = _normalize_intervals([" day ", "", "  ", "60min,, 30min "])
        self.assertEqual(out, ("day", "60min", "30min"))

    def test_normalize_intervals_raises_when_all_empty(self) -> None:
        with self.assertRaises(ValueError):
            _normalize_intervals(["", "  ", ","])

    def test_parse_args_interval_supports_multiple_values(self) -> None:
        """M1: CLI 必须能接收多个 interval。"""
        argv = [
            "--symbol", "RB0",
            "--interval", "day", "60min", "30min", "15min",
        ]
        ns = _parse_args(argv)
        # argparse 拿到的是 list[str]
        self.assertEqual(list(ns.interval), ["day", "60min", "30min", "15min"])

    def test_parse_args_interval_default_is_single_60min(self) -> None:
        """M1: 不传 --interval 时仍兼容旧默认 60min。"""
        ns = _parse_args(["--symbol", "RB0"])
        self.assertEqual(list(ns.interval), ["60min"])
        self.assertAlmostEqual(float(ns.max_auc_gap), 0.03, places=9)
        self.assertEqual(int(ns.min_used_symbols), 2)

    def test_parse_args_supports_top_n_symbols(self) -> None:
        ns = _parse_args(
            [
                "--symbol", "RB0",
                "--top-n-symbols", "5",
                "--symbols-ranking-path", "cta/feature/symbols_research_ranking.csv",
                "--min-used-symbols", "3",
            ]
        )
        self.assertEqual(int(ns.top_n_symbols), 5)
        self.assertEqual(int(ns.min_used_symbols), 3)
        self.assertEqual(str(ns.symbols_ranking_path), "cta/feature/symbols_research_ranking.csv")

    def test_load_top_n_symbols_from_ranking_orders_by_rank(self) -> None:
        with tempfile.TemporaryDirectory(prefix="cta_topn_rank_") as td:
            p = Path(td) / "ranking.csv"
            pd.DataFrame(
                {
                    "symbol": ["B0", "A0", "C0"],
                    "exchange": ["DCE", "SHFE", "CZCE"],
                    "research_rank": [2, 1, 3],
                }
            ).to_csv(p, index=False, encoding="utf-8-sig")
            out = _load_top_n_symbols_from_ranking(p, top_n=2)
            self.assertEqual(out, [("A0", "SHFE"), ("B0", "DCE")])

    def test_run_model_pipeline_multi_returns_one_result_per_interval(self) -> None:
        """M1: run_model_pipeline_multi 应当每个 interval 返回一个结果，
        每个结果的 prediction_path 必须真实存在，且包含 interval 信息以便区分。
        """
        intervals = ("day", "60min", "30min")
        with tempfile.TemporaryDirectory(prefix="cta_multi_interval_") as td:
            results = run_model_pipeline_multi(
                symbol="MULTI0",
                exchange="SHFE",
                intervals=intervals,
                start_date="2018-01-01",
                end_date="2018-12-31",
                output_root=Path(td),
                train_end="2018-06-30",
                valid_end="2018-09-30",
                synthetic_periods=120,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            self.assertEqual(len(results), len(intervals))
            seen_dirs: set[Path] = set()
            for interval, res in zip(intervals, results):
                self.assertTrue(res.prediction_path.exists())
                self.assertTrue(res.metrics_path.exists())
                self.assertTrue(res.report_path.exists())
                # 不同 interval 必须落到不同目录，避免互相覆盖
                self.assertNotIn(res.prediction_path.parent, seen_dirs)
                seen_dirs.add(res.prediction_path.parent)


    # ---------------- D4: FEATURES.md 改动后 _feature_meaning 必须随时刷新 -----
    def test_feature_meaning_refreshes_when_features_doc_mtime_changes(self) -> None:
        """D4: 旧实现 ``@lru_cache(maxsize=8)`` 直接以 path 字符串为键，
        长跑进程里 FEATURES.md 改动后取到的依旧是老映射。修复后缓存键带上
        mtime —— 文件改动后映射立刻失效。
        """
        import os
        import time as _time

        from cta.model.model_pipeline import _feature_meaning

        with tempfile.TemporaryDirectory(prefix="cta_features_doc_") as td:
            doc = Path(td) / "FEATURES.md"
            doc.write_text("| `feature_xyz` | 第一版含义 |\n", encoding="utf-8")
            v1 = _feature_meaning("feature_xyz", features_doc_path=doc)
            self.assertEqual(v1, "第一版含义")

            # 制造一个明显比上次大的 mtime（避免 1s 粒度文件系统看不到差异）。
            doc.write_text("| `feature_xyz` | 第二版含义 |\n", encoding="utf-8")
            future_ts = _time.time() + 5
            os.utime(doc, (future_ts, future_ts))
            v2 = _feature_meaning("feature_xyz", features_doc_path=doc)
            self.assertEqual(v2, "第二版含义")

    # ---------------- G1: pipeline 默认 auto 模式拼上磁盘所有 generic 特征 ----
    def test_pipeline_includes_extra_generic_features_under_auto_mode(self) -> None:
        """G1: 给一个临时 feature_root，里面 generic parquet 含 30 个数值列。
        pipeline 默认 generic_mode='auto' 后，feature_table 里出现的 generic_*
        列数应等于 30（旧实现仅拿 18 列白名单的子集，会漏 12+ 个）。
        """
        with tempfile.TemporaryDirectory(prefix="cta_generic_auto_") as td:
            feat_root = Path(td) / "feature"
            (feat_root / "minute60" / "GAUTO0").mkdir(parents=True, exist_ok=True)

            # 构造 generic parquet：5 OHLCV + 30 数值特征 + 2 字符串元数据
            for date_str in ("2018-01-02", "2018-06-01", "2019-01-02", "2019-06-01", "2019-12-30"):
                rng = np.random.default_rng(int(date_str.replace("-", "")) % 10_000)
                row = {"datetime": pd.to_datetime([f"{date_str} 09:00:00"])}
                for col in ("open", "high", "low", "close", "volume"):
                    row[col] = [3500.0 + rng.normal()]
                for i in range(30):
                    row[f"feat_extra_{i}"] = [float(rng.normal())]
                row["regime_label_text"] = ["trend_up"]
                pd.DataFrame(row).to_parquet(
                    feat_root / "minute60" / "GAUTO0" / f"{date_str}.parquet",
                    index=False,
                )

            with tempfile.TemporaryDirectory(prefix="cta_generic_auto_out_") as td2:
                out = run_model_pipeline(
                    symbol="GAUTO0",
                    exchange="SHFE",
                    interval="60min",
                    start_date="2018-01-01",
                    end_date="2019-12-31",
                    output_root=Path(td2),
                    train_end="2018-12-31",
                    valid_end="2019-06-30",
                    feature_root=feat_root,
                    synthetic_periods=240,
                    by_signal_type=False,
                    max_walk_forward_windows=1,
                    generic_mode="auto",
                )
                feat_table = pd.read_csv(out.feature_table_path)
                generic_cols = [c for c in feat_table.columns if c.startswith("generic_")]
                # 30 个数值特征都应该被拼进来
                self.assertGreaterEqual(len(generic_cols), 30)
                # OHLCV / 字符串列必须排除
                for blocked in ("generic_open", "generic_high", "generic_low",
                                "generic_close", "generic_volume",
                                "generic_regime_label_text"):
                    self.assertNotIn(blocked, feat_table.columns)

    def test_pipeline_whitelist_mode_caps_generic_at_18_columns(self) -> None:
        """G1: 显式传 generic_mode='whitelist' 时仍走 18 列窄白名单（向后兼容）。"""
        with tempfile.TemporaryDirectory(prefix="cta_generic_whitelist_") as td:
            feat_root = Path(td) / "feature"
            (feat_root / "minute60" / "GWHITE0").mkdir(parents=True, exist_ok=True)
            for date_str in ("2018-01-02", "2018-06-01", "2019-01-02", "2019-06-01", "2019-12-30"):
                row = {"datetime": pd.to_datetime([f"{date_str} 09:00:00"])}
                for col in ("open", "high", "low", "close", "volume"):
                    row[col] = [3500.0]
                # 写入 18 个白名单列 + 30 个额外列
                for w in (
                    "sma_20", "ema_20", "macd_dif", "macd_dea", "rsi_14",
                    "atr_14", "bb_width", "stoch_k", "stoch_d", "mfi_14",
                    "trend_score", "compression_score", "breakout_mode_score",
                    "setup_quality_score", "breakout_quality_score",
                    "context_score", "regime_label", "regime_conf",
                ):
                    row[w] = [1.0]
                for i in range(30):
                    row[f"feat_extra_{i}"] = [1.0]
                pd.DataFrame(row).to_parquet(
                    feat_root / "minute60" / "GWHITE0" / f"{date_str}.parquet",
                    index=False,
                )

            with tempfile.TemporaryDirectory(prefix="cta_generic_whitelist_out_") as td2:
                out = run_model_pipeline(
                    symbol="GWHITE0",
                    exchange="SHFE",
                    interval="60min",
                    start_date="2018-01-01",
                    end_date="2019-12-31",
                    output_root=Path(td2),
                    train_end="2018-12-31",
                    valid_end="2019-06-30",
                    feature_root=feat_root,
                    synthetic_periods=240,
                    by_signal_type=False,
                    max_walk_forward_windows=1,
                    generic_mode="whitelist",
                )
                feat_table = pd.read_csv(out.feature_table_path)
                generic_cols = [c for c in feat_table.columns if c.startswith("generic_")]
                # whitelist 模式下应严格不超过 18 列
                self.assertLessEqual(len(generic_cols), 18)
                # extra_* 列绝不应在
                for c in feat_table.columns:
                    self.assertFalse(c.startswith("generic_feat_extra_"))

    # ---------------- P1-B: _select_feature_columns fallback 不再写 entry_price
    def test_select_feature_fallback_does_not_emit_entry_price(self) -> None:
        """P1-B: 旧 fallback 在没有 feature_*/generic_* 列时会写
        ``feature_entry_price = entry_price``。entry_price 是 entry bar 内
        实际成交价，决策时刻 (signal bar 收盘) 不可见 → 是潜在 lookahead leak。
        修复后 fallback 改用 ``feature_trigger = trigger``（决策时刻可见的突破触发价），
        不再泄漏 entry_price。
        """
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=3, freq="D"),
                "side": ["long", "short", "long"],
                "signal_type": ["donchian_breakout"] * 3,
                "trigger": [101.0, 102.0, 103.0],
                "entry_price": [101.5, 101.5, 103.5],
            }
        )
        out_df, feature_cols = _select_feature_columns(df)
        self.assertNotIn("feature_entry_price", out_df.columns)
        self.assertNotIn("feature_entry_price", feature_cols)

    def test_select_feature_fallback_uses_trigger_when_present(self) -> None:
        """P1-B: candidate 表有 trigger 列时，fallback 应写 feature_trigger
        而不是 feature_entry_price，让模型只看决策时刻可见的信息。
        """
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=3, freq="D"),
                "side": ["long", "short", "long"],
                "signal_type": ["donchian_breakout"] * 3,
                "trigger": [101.0, 102.0, 103.0],
                "entry_price": [101.5, 101.5, 103.5],  # 不应被使用
            }
        )
        out_df, feature_cols = _select_feature_columns(df)
        self.assertIn("feature_trigger", feature_cols)
        # 用 trigger 不是 entry_price 的取值
        for i, expected in enumerate([101.0, 102.0, 103.0]):
            self.assertAlmostEqual(float(out_df["feature_trigger"].iloc[i]), expected)

    # ---------------- F1: 每个模型 joblib 旁边生成全特征清单 -------------------
    def test_pipeline_writes_feature_manifest_per_saved_model(self) -> None:
        """F1: 模型部署时下游需要严格按"训练用过的特征清单"做 schema 校验。
        在每个 ``models/<signal_type>/window_xx/<model>.joblib`` 旁边生成一份
        ``<model>_features.csv``，列：rank / feature / importance / feature_meaning。
        清单按 importance 降序、所有特征都列出（不只 top10）。
        """
        with tempfile.TemporaryDirectory(prefix="cta_feat_manifest_") as td:
            out = run_model_pipeline(
                symbol="MANIFEST0",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=240,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            models_dir = out.report_path.parent / "models"
            self.assertTrue(models_dir.exists())

            joblib_files = list(models_dir.rglob("*.joblib"))
            self.assertGreater(len(joblib_files), 0)

            for joblib_path in joblib_files:
                manifest_path = joblib_path.with_name(joblib_path.stem + "_features.csv")
                self.assertTrue(
                    manifest_path.exists(),
                    f"missing feature manifest beside {joblib_path}: {manifest_path}",
                )
                df = pd.read_csv(manifest_path)
                for col in ("rank", "feature", "importance", "feature_meaning"):
                    self.assertIn(col, df.columns)
                self.assertGreater(len(df), 0)
                self.assertEqual(int(df["rank"].iloc[0]), 1)
                self.assertEqual(int(df["rank"].iloc[-1]), len(df))
                imp = df["importance"].astype(float).to_numpy()
                self.assertTrue(
                    all(imp[i] >= imp[i + 1] for i in range(len(imp) - 1)),
                    f"importance not non-increasing in {manifest_path}: {imp}",
                )
                self.assertEqual(df["feature"].nunique(), len(df))

    def test_feature_manifest_lists_all_training_features_not_just_top10(self) -> None:
        """F1: 清单必须列**所有**用于训练的特征（部署 schema 校验需要全集）。
        断言：清单行数 == top10_feature_importance 文件里同 (signal,window,model)
        条件下出现过的所有特征 ⊆ 清单 feature 列；并且清单不止 top10 行。
        """
        with tempfile.TemporaryDirectory(prefix="cta_feat_manifest_full_") as td:
            out = run_model_pipeline(
                symbol="MANIFEST1",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=240,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            models_dir = out.report_path.parent / "models"
            joblib_files = list(models_dir.rglob("trade_filter.joblib"))
            self.assertGreater(len(joblib_files), 0)
            df = pd.read_csv(joblib_files[0].with_name("trade_filter_features.csv"))

            # 1) top10 文件里 trade_filter 的所有特征必须 ⊆ 全量清单
            top10 = pd.read_csv(out.top_feature_importance_path)
            top10_trade = set(top10.loc[top10["model"] == "trade_filter", "feature"].astype(str))
            self.assertTrue(top10_trade.issubset(set(df["feature"].astype(str))))
            # 2) 全量清单行数 >= top10 行数（在合成数据下 feature 数可能少于 10）
            self.assertGreaterEqual(len(df), len(top10_trade))

    def test_feature_manifest_no_orphan_csv_without_joblib(self) -> None:
        """F1: 不能存在没有对应 joblib 的孤儿 manifest（mfe_mae 被 skip 时的边缘场景）。"""
        with tempfile.TemporaryDirectory(prefix="cta_feat_manifest_skip_") as td:
            out = run_model_pipeline(
                symbol="MANIFEST2",
                exchange="SHFE",
                interval="60min",
                start_date="2018-01-01",
                end_date="2019-12-31",
                output_root=Path(td),
                train_end="2018-12-31",
                valid_end="2019-06-30",
                synthetic_periods=240,
                by_signal_type=False,
                max_walk_forward_windows=1,
            )
            models_dir = out.report_path.parent / "models"
            for csv_path in models_dir.rglob("*_features.csv"):
                stem = csv_path.stem.replace("_features", "")
                self.assertTrue(
                    csv_path.with_name(stem + ".joblib").exists(),
                    f"orphan feature manifest without joblib: {csv_path}",
                )

    # ---------------- D3: warmup 行的 future_mfe/mae 必须保留 NaN -------------
    def test_ensure_training_columns_keeps_nan_for_atr_warmup_rows(self) -> None:
        """D3: 旧实现对所有行 ``fillna(0.0)``，会把 atr_warmup 期的"未知"
        悄悄写成"0 / 0"，吞掉了候选样本特意保留的 NaN 信号。修复后 warmup 行
        ``future_mfe_atr / future_mae_atr`` 必须保持 NaN，warm 行才走 fillna。
        """
        df = pd.DataFrame(
            {
                "datetime": pd.date_range("2020-01-01", periods=2, freq="D"),
                "is_executed": [1, 1],
                "atr_warmed": [0, 1],  # row0 在 warmup，row1 已热身
                "future_mfe_atr": [np.nan, 1.0],
                "future_mae_atr": [np.nan, 0.5],
                "label_class": [0, 1],
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "long"],
                "feature_x": [0.1, 0.2],
            }
        )
        out = _ensure_training_columns(df)
        warm_row = out.loc[out["atr_warmed"] == 1].iloc[0]
        warmup_row = out.loc[out["atr_warmed"] == 0].iloc[0]
        # warmup 行必须保留 NaN
        self.assertTrue(pd.isna(warmup_row["future_mfe_atr"]))
        self.assertTrue(pd.isna(warmup_row["future_mae_atr"]))
        # warm 行的值不动（已经是 finite）
        self.assertAlmostEqual(float(warm_row["future_mfe_atr"]), 1.0)
        self.assertAlmostEqual(float(warm_row["future_mae_atr"]), 0.5)

    # ---------------- D1: --exchange 操作符优先级回归 -------------------------
    def test_resolve_run_exchange_prefers_ranking_when_cli_exchange_blank(self) -> None:
        """D1: 之前的 ``a or b if c else None`` 解析为 ``(a or b) if c else None``，
        在 ``c`` 为假值时即使 ``a`` 已经从 ranking 拿到 SHFE，也会被丢成 None。
        """
        # ranking 提供 SHFE，CLI 没传 --exchange → 必须保留 SHFE。
        self.assertEqual(_resolve_run_exchange("SHFE", None), "SHFE")
        self.assertEqual(_resolve_run_exchange("SHFE", ""), "SHFE")
        # ranking 没提供 → 退到 CLI 值。
        self.assertEqual(_resolve_run_exchange(None, "CZCE"), "CZCE")
        # 两者皆空 → None。
        self.assertEqual(_resolve_run_exchange(None, None), None)
        self.assertEqual(_resolve_run_exchange(None, ""), None)
        # ranking 优先于 CLI（即使 CLI 也非空）。
        self.assertEqual(_resolve_run_exchange("DCE", "CZCE"), "DCE")
        # 大小写归一：CLI 小写时也要 upper。
        self.assertEqual(_resolve_run_exchange(None, "czce"), "CZCE")

    def test_evaluate_oot_real_execution_supports_stacking_gate_override(self) -> None:
        """P1.3: 启用 stacking gate 后，应支持覆盖旧的三段式 AND 过滤。"""
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-10", "2020-01-11"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "60min"],
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "long"],
                "window_id": [1, 1],
                "pred_split": ["test", "test"],
                "is_executed": [1, 1],
                "future_mfe_atr": [1.0, 1.0],
                "future_mae_atr": [0.2, 0.2],
                # 旧三段 gate 全会挡住第一行
                "trade_filter_prob": [0.05, 0.99],
                "pred_regime_label": ["trend_down", "trend_up"],
                "pred_mfe_atr": [0.0, 2.0],
                "pred_mae_atr": [1.0, 0.1],
                # stacking 分数只放行第一行
                "final_decision_score": [0.90, 0.10],
            }
        )
        cfg = OotEvaluationConfig(
            use_test_split_only=True,
            use_last_window_only=False,
            require_executed_only=True,
            use_trade_filter_gate=True,
            trade_filter_threshold=0.55,
            use_regime_gate=True,
            allow_range_in_regime_gate=False,
            use_mfe_mae_gate=True,
            min_pred_edge_atr=0.5,
            # 新逻辑：stacking gate 覆盖三段式 gate
            use_stacking_gate=True,
            stacking_score_threshold=0.6,
            stacking_score_column="final_decision_score",
            stacking_gate_overrides_individual_gates=True,
            mae_penalty=1.0,
            initial_capital=1000.0,
            risk_per_trade_pct=0.01,
            max_single_loss_pct=0.02,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
            use_position_sizing=False,
            use_portfolio_constraints=False,
            use_intrabar_stop_tracking=False,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["selected_rows"]), 1)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 1)
        self.assertEqual(len(trades), 1)
        self.assertAlmostEqual(float(trades.iloc[0]["final_decision_score"]), 0.90, places=9)

    def test_evaluate_oot_real_execution_scales_down_after_weekly_dd_breach(self) -> None:
        """P2.5: 周回撤触发后不一定停手，可按配置缩仓。"""
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-07 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-07 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 15:00:00", "2020-01-07 15:00:00"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["day", "day"],
                "signal_type": ["donchian_breakout", "donchian_breakout"],
                "side": ["long", "long"],
                "window_id": [0, 0],
                "pred_split": ["test", "test"],
                "is_executed": [1, 1],
                "future_mfe_atr": [0.0, 1.0],   # 第一笔亏损，第二笔盈利
                "future_mae_atr": [1.0, 0.0],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            mae_penalty=1.0,
            initial_capital=1000.0,
            risk_per_trade_pct=0.04,
            max_single_loss_pct=0.04,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
            use_position_sizing=False,
            max_position_scale=1.0,
            max_symbol_notional_pct=10.0,
            use_portfolio_constraints=True,
            margin_rate=0.01,
            max_total_leverage=10.0,
            max_daily_new_notional_pct=100.0,
            weekly_max_drawdown_pct=0.03,
            block_new_entries_on_weekly_dd_breach=False,
            weekly_dd_position_scale_after_breach=0.5,
            enforce_weekly_dd_budget_on_entry=False,
            use_intrabar_stop_tracking=False,
            # P0 fix：让 trade_return floor 与本测试场景的 max_single_loss_pct 对齐，
            # 保留旧测试意图（第一笔 -4% 触发 3% 周回撤 → 第二笔缩仓 0.5x）。
            intrabar_stop_loss_pct=0.04,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 2)
        first_notional = float(trades.iloc[0]["position_notional"])
        second_notional = float(trades.iloc[1]["position_notional"])
        self.assertGreater(first_notional, 0.0)
        second_equity_before = float(trades.iloc[1]["equity_before"])
        self.assertAlmostEqual(second_notional, second_equity_before * 0.5, places=6)

    def test_evaluate_oot_real_execution_monthly_dd_hard_stop(self) -> None:
        """P2.5: 月回撤触发后应阻断当月后续新开仓。"""
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    ["2020-01-06 09:00:00", "2020-01-10 09:00:00", "2020-01-15 09:00:00"]
                ),
                "entry_datetime": pd.to_datetime(
                    ["2020-01-06 09:00:00", "2020-01-10 09:00:00", "2020-01-15 09:00:00"]
                ),
                "exit_datetime": pd.to_datetime(
                    ["2020-01-06 15:00:00", "2020-01-10 15:00:00", "2020-01-15 15:00:00"]
                ),
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["day", "day", "day"],
                "signal_type": ["donchian_breakout", "donchian_breakout", "donchian_breakout"],
                "side": ["long", "long", "long"],
                "window_id": [0, 0, 0],
                "pred_split": ["test", "test", "test"],
                "is_executed": [1, 1, 1],
                # 前两笔各亏 3%，第三笔本应可交易
                "future_mfe_atr": [0.0, 0.0, 1.0],
                "future_mae_atr": [1.0, 1.0, 0.0],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            mae_penalty=1.0,
            initial_capital=1000.0,
            risk_per_trade_pct=0.03,
            max_single_loss_pct=0.03,
            commission_pct_per_trade=0.0,
            slippage_pct_per_trade=0.0,
            benchmark_annual_return=0.0,
            risk_free_annual_return=0.0,
            annualization_factor=12.0,
            use_position_sizing=False,
            max_position_scale=1.0,
            max_symbol_notional_pct=1.0,
            use_portfolio_constraints=True,
            margin_rate=0.01,
            max_total_leverage=10.0,
            max_daily_new_notional_pct=10.0,
            weekly_max_drawdown_pct=1.0,
            block_new_entries_on_weekly_dd_breach=False,
            enforce_weekly_dd_budget_on_entry=False,
            monthly_max_drawdown_pct=0.05,
            block_new_entries_on_monthly_dd_breach=True,
            use_intrabar_stop_tracking=False,
            # P0 fix：让 trade_return floor 与本测试 max_single_loss_pct 对齐。
            # 前两笔各亏 3% → 累计 -6% > 5% 月回撤阈值 → 第三笔被月熔断。
            intrabar_stop_loss_pct=0.03,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["selected_rows"]), 3)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 2)
        statuses = trades["execution_status"].astype(str).tolist()
        self.assertIn("blocked_monthly_drawdown", statuses)

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
        """P1.3: 训练后 metrics/predictions 中应有可辨识的最终决策模型命名。"""
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


if __name__ == "__main__":
    unittest.main()
