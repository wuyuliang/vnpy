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



class TestModelPipelinePart03(unittest.TestCase):
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
            max_position_scale=1.0,
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
            intrabar_stop_loss_pct_by_cluster_interval={},
            enforce_stop_loss_consistency=False,
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

    def test_evaluate_oot_real_execution_blocked_entry_does_not_reserve_margin(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:00:00"]),
                "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 10:00:00", "2020-01-06 10:00:00"]),
                "symbol": ["RB0", "HC0"],
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
                # 第一笔涨停不可买，第二笔正常。
                "feature_is_limit_up_close": [1, 0],
                "feature_is_limit_down_close": [0, 0],
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
            use_portfolio_constraints=True,
            margin_rate=1.0,
            max_total_leverage=1.0,
            max_daily_new_notional_pct=10.0,
            weekly_max_drawdown_pct=1.0,
            enforce_weekly_dd_budget_on_entry=False,
            block_new_entries_on_weekly_dd_breach=False,
            use_intrabar_stop_tracking=False,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        self.assertEqual(int(summary.iloc[0]["blocked_limit_move_rows"]), 1)
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 1)
        rb = trades.loc[trades["symbol"] == "RB0"].iloc[0]
        hc = trades.loc[trades["symbol"] == "HC0"].iloc[0]
        self.assertEqual(str(rb["execution_status"]), "blocked_limit_move")
        self.assertEqual(str(hc["execution_status"]), "executed")

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


if __name__ == "__main__":
    unittest.main()
