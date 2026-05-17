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



class TestModelPipelinePart01(unittest.TestCase):
    def test_validate_stop_loss_pct_consistency_default_passes(self) -> None:
        _validate_stop_loss_pct_consistency()

    def test_validate_stop_loss_pct_consistency_raises_when_gap_too_large(self) -> None:
        with self.assertRaises(RuntimeError):
            _validate_stop_loss_pct_consistency(
                oot_stop_loss_pct=0.001,
                label_stop_loss_pct=0.02,
            )

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
        # 平仓后仓位应回到 0，供 group runtime 汇总目录做“每笔交易后仓位”展示。
        self.assertAlmostEqual(float(trades.iloc[0]["position_notional_after_trade"]), 0.0, places=6)
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


if __name__ == "__main__":
    unittest.main()
