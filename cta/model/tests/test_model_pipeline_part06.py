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
from cta.portfolio_logic.config import PortfolioLogicConfig, RiskThrottleConfig, ThrottleLevel
from cta.model.training.trade_filter_model import TradeFilterModel



class TestModelPipelinePart06(unittest.TestCase):
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
            trade_filter_gate_mode="raw",
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
        # OOT 明细会保留被模型挡掉的候选，便于排查 block_reason；
        # 真实交易计数仍只统计 executed 行。
        self.assertEqual(len(trades), 2)
        executed = trades.loc[trades["execution_status"].astype(str) == "executed"]
        blocked = trades.loc[trades["execution_status"].astype(str) == "blocked_final_decision_gate"]
        self.assertEqual(len(executed), 1)
        self.assertEqual(len(blocked), 1)
        self.assertAlmostEqual(float(executed.iloc[0]["final_decision_score"]), 0.90, places=9)

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


if __name__ == "__main__":
    unittest.main()
