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
from cta.portfolio_logic.config import (
    PortfolioLogicConfig,
    RiskThrottleConfig,
    ThrottleLevel,
    TrailingExitConfig,
)
from cta.model.training.trade_filter_model import TradeFilterModel



class TestModelPipelinePart02(unittest.TestCase):
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
            enforce_stop_loss_consistency=False,
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

    def test_evaluate_oot_real_execution_htf_external_reference_unblocks_single_interval(self) -> None:
        """单 interval=day 跑批时，Fix-A 应自动把 htf_intervals 窄化到 ("day",)，
        外部 60min 参考仍可正常工作。详见 cta/docs/block_reason.md §5。"""
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 11:00:00"]),
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["day"],
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
                "pred_regime_label": ["trend_up"],
                "pred_split": ["test"],
                "window_id": [0],
                "is_executed": [1],
                "entry_price": [100.0],
                "future_mfe_atr": [1.0],
                "future_mae_atr": [0.2],
            }
        )
        htf_ref = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 15:00:00", "2020-01-06 15:00:00"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["day", "60min"],
                "signal_type": ["htf_ref", "htf_ref"],
                "side": ["long", "long"],
                "pred_regime_label": ["trend_up", "trend_up"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [0, 0],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_test_split_only=True,
            require_executed_only=True,
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
        # Fix-A：仅 day 数据可用时，HTF gate 自动窄化到 ("day",)，方向匹配 → 交易执行
        _m0, s0, t0 = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(s0.iloc[0]["trade_count"]), 1)
        self.assertEqual(int(s0.iloc[0]["blocked_htf_rows"]), 0)
        self.assertEqual(str(t0.iloc[0]["execution_status"]), "executed")

        # 显式提供 day+60min 参考时仍按完整 consensus 走，行为一致
        _m1, s1, t1 = _evaluate_oot_real_execution(pred, cfg=cfg, htf_reference_df=htf_ref)
        self.assertEqual(int(s1.iloc[0]["trade_count"]), 1)
        self.assertEqual(int(s1.iloc[0]["blocked_htf_rows"]), 0)
        self.assertEqual(str(t1.iloc[0]["execution_status"]), "executed")

    def test_evaluate_oot_real_execution_htf_external_reference_aligns_target_window_id(self) -> None:
        """外部 HTF 参考含多 window 时，应与当前评估 window 对齐，不取参考表自己的 max window。"""
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 11:00:00"]),
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["day"],
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
                "pred_regime_label": ["trend_up"],
                "pred_split": ["test"],
                "window_id": [1],
                "is_executed": [1],
                "entry_price": [100.0],
                "future_mfe_atr": [1.0],
                "future_mae_atr": [0.2],
            }
        )
        htf_ref = pd.DataFrame(
            {
                "datetime": pd.to_datetime(
                    [
                        "2020-01-06 09:00:00",
                        "2020-01-06 09:00:00",
                        # 噪音 window：仅 day，无 60min（若错误按 max window 过滤会触发 htf_missing）
                        "2020-02-06 09:00:00",
                    ]
                ),
                "entry_datetime": pd.to_datetime(
                    ["2020-01-06 09:00:00", "2020-01-06 09:00:00", "2020-02-06 09:00:00"]
                ),
                "exit_datetime": pd.to_datetime(
                    ["2020-01-06 15:00:00", "2020-01-06 15:00:00", "2020-02-06 15:00:00"]
                ),
                "symbol": ["RB0", "RB0", "RB0"],
                "exchange": ["SHFE", "SHFE", "SHFE"],
                "interval": ["day", "60min", "day"],
                "signal_type": ["htf_ref", "htf_ref", "htf_ref"],
                "side": ["long", "long", "long"],
                "pred_regime_label": ["trend_up", "trend_up", "trend_up"],
                "pred_split": ["test", "test", "test"],
                "window_id": [1, 1, 2],
                "is_executed": [0, 0, 0],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_test_split_only=True,
            use_last_window_only=True,
            require_executed_only=True,
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
        _m1, s1, t1 = _evaluate_oot_real_execution(pred, cfg=cfg, htf_reference_df=htf_ref)
        self.assertEqual(int(s1.iloc[0]["trade_count"]), 1)
        self.assertEqual(int(s1.iloc[0]["blocked_htf_rows"]), 0)
        self.assertEqual(str(t1.iloc[0]["execution_status"]), "executed")

    def test_evaluate_oot_real_execution_htf_external_reference_mixed_datetime_formats(self) -> None:
        """HTF 参考含 day(YYYY-MM-DD) + 60min(YYYY-MM-DD HH:MM:SS) 时不应把 60min 解析成 NaT。"""
        pred = pd.DataFrame(
            {
                "datetime": ["2020-01-06"],
                "entry_datetime": ["2020-01-06"],
                "exit_datetime": ["2020-01-07"],
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["day"],
                "signal_type": ["donchian_breakout"],
                "side": ["long"],
                "pred_regime_label": ["trend_up"],
                "pred_split": ["test"],
                "window_id": [0],
                "is_executed": [1],
                "entry_price": [100.0],
                "future_mfe_atr": [1.0],
                "future_mae_atr": [0.2],
            }
        )
        htf_ref = pd.DataFrame(
            {
                "datetime": ["2020-01-06", "2020-01-05 23:00:00"],
                "entry_datetime": ["2020-01-06", "2020-01-05 23:00:00"],
                "exit_datetime": ["2020-01-07", "2020-01-06 00:00:00"],
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["day", "60min"],
                "signal_type": ["htf_ref", "htf_ref"],
                "side": ["long", "long"],
                "pred_regime_label": ["trend_up", "trend_up"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [0, 0],
            }
        )
        cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_test_split_only=True,
            require_executed_only=True,
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
        _m, s, t = _evaluate_oot_real_execution(pred, cfg=cfg, htf_reference_df=htf_ref)
        self.assertEqual(int(s.iloc[0]["trade_count"]), 1)
        self.assertEqual(int(s.iloc[0]["blocked_htf_rows"]), 0)
        self.assertEqual(str(t.iloc[0]["execution_status"]), "executed")

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


if __name__ == "__main__":
    unittest.main()
