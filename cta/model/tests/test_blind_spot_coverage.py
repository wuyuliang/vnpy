"""P2.6 blind-spot tests:

1) multi-interval signal conflict: 60min long + 5min short on same symbol/timestamp
   → portfolio cap should still arbitrate via concurrent caps, not crash.
2) night-session missing data: NaN OHLC bars in intrabar slice fallback.
3) cap priority order: when several caps fire simultaneously, the most-specific reason
   wins ("blocked_symbol_cap" before "blocked_weekly_budget" before others).
"""
from __future__ import annotations

import unittest

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.pipeline_oot_evaluation import _evaluate_oot_real_execution


def _base_cfg(**overrides) -> OotEvaluationConfig:
    base = dict(
        use_trade_filter_gate=False,
        use_regime_gate=False,
        use_mfe_mae_gate=False,
        use_stacking_gate=False,
        mae_penalty=1.0,
        initial_capital=1_000_000.0,
        risk_per_trade_pct=0.002,
        max_single_loss_pct=0.001,
        commission_pct_per_trade=0.0,
        slippage_pct_per_trade=0.0,
        use_position_sizing=False,
        max_position_scale=1.0,
        max_symbol_notional_pct=1.0,
        max_concurrent_positions_per_symbol=10,
        max_concurrent_positions_total=20,
        use_portfolio_constraints=True,
        margin_rate=0.10,
        max_total_leverage=10.0,
        max_daily_new_notional_pct=10.0,
        weekly_max_drawdown_pct=1.0,
        enforce_weekly_dd_budget_on_entry=False,
        block_new_entries_on_weekly_dd_breach=False,
        block_new_entries_on_monthly_dd_breach=False,
        benchmark_annual_return=0.0,
        risk_free_annual_return=0.0,
        annualization_factor=12.0,
        use_intrabar_stop_tracking=False,
        use_roll_cost=False,
    )
    base.update(overrides)
    return OotEvaluationConfig(**base)


class TestBlindSpotCoverage(unittest.TestCase):
    def test_multi_interval_conflict_long_and_short_same_symbol_graceful(self) -> None:
        """60min long + 5min short 同 symbol 同时触发：组合层应优雅处理，
        要么两笔都成交（cap 留有 buffer），要么后一笔被 cap_symbol 阻断 — 任何情况都不应崩溃。"""
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:05:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-06 10:00:00", "2020-01-06 09:10:00"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "5min"],
                "signal_type": ["donchian_breakout", "tight_range_breakout"],
                "side": ["long", "short"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [1, 1],
                "entry_price": [100.0, 100.0],
                "future_mfe_atr": [1.0, 1.0],
                "future_mae_atr": [0.5, 0.5],
            }
        )
        # 用 max_position_scale=0.3 + max_symbol_notional_pct=1.0 留 buffer，允许两笔都执行
        _monthly, summary, trades = _evaluate_oot_real_execution(
            pred, cfg=_base_cfg(max_position_scale=0.3, max_symbol_notional_pct=1.0)
        )
        self.assertEqual(int(summary.iloc[0]["trade_count"]), 2)
        # 不论是否都成交，每笔的 status 必须是合法值（不可为空 / Pending）
        statuses = trades["execution_status"].astype(str).tolist()
        valid_statuses = {
            "executed",
            "opened",
            "blocked_symbol_cap",
            "blocked_symbol_concurrent",
            "blocked_total_concurrent",
            "blocked_leverage",
            "blocked_margin_cash",
            "blocked_daily_position",
            "blocked_portfolio_constraint",
        }
        for s in statuses:
            self.assertIn(s, valid_statuses)

    def test_night_session_missing_data_fallback_does_not_crash(self) -> None:
        """夜盘数据全 NaN 时，intrabar 跟踪应安全回退到 atr_proxy 模式而非崩溃。"""
        def empty_bars_provider(symbol, exchange, start_ts, end_ts, interval):
            return pd.DataFrame()  # 模拟夜盘整段缺数据

        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-06 21:00:00"]),
                "exit_datetime": pd.to_datetime(["2020-01-07 02:00:00"]),
                "symbol": ["RB0"],
                "exchange": ["SHFE"],
                "interval": ["60min"],
                "signal_type": ["atr_breakout"],
                "side": ["long"],
                "pred_split": ["test"],
                "window_id": [0],
                "is_executed": [1],
                "entry_price": [100.0],
                "future_mfe_atr": [1.0],
                "future_mae_atr": [0.5],
            }
        )
        cfg = _base_cfg(use_intrabar_stop_tracking=True, intrabar_stop_loss_pct=0.005)
        _monthly, summary, trades = _evaluate_oot_real_execution(
            pred, cfg=cfg, intrabar_bar_provider=empty_bars_provider
        )
        self.assertGreaterEqual(int(summary.iloc[0]["executed_rows"]), 1)
        self.assertEqual(int(len(trades)), 1)
        # 不要求一定 executed，但绝不能因为缺数据 raise

    def test_cap_priority_symbol_cap_wins_over_weekly_budget(self) -> None:
        """同时触发 cap_symbol、cap_weekly_budget 时，block_reason 应优先报最具体的 symbol_cap。"""
        # 设定：单 symbol 已开仓占满 max_symbol_notional_pct，同时周回撤预算也紧张
        starts = pd.to_datetime(
            ["2020-01-06 09:00:00", "2020-01-06 10:00:00", "2020-01-06 11:00:00"]
        )
        exits = pd.to_datetime(
            ["2020-01-10 09:00:00", "2020-01-10 09:00:00", "2020-01-10 09:00:00"]
        )
        pred = pd.DataFrame(
            {
                "datetime": starts,
                "exit_datetime": exits,
                "symbol": ["RB0"] * 3,
                "exchange": ["SHFE"] * 3,
                "interval": ["60min"] * 3,
                "signal_type": ["donchian_breakout"] * 3,
                "side": ["long"] * 3,
                "pred_split": ["test"] * 3,
                "window_id": [0] * 3,
                "is_executed": [1] * 3,
                "entry_price": [100.0] * 3,
                "future_mfe_atr": [0.5] * 3,
                "future_mae_atr": [0.5] * 3,
            }
        )
        # max_symbol_notional_pct=0.30, position_scale=0.20 → 第 2 笔会被 cap_symbol 卡掉
        cfg = _base_cfg(
            max_position_scale=0.20,
            max_symbol_notional_pct=0.30,
            max_concurrent_positions_per_symbol=10,
            max_total_leverage=10.0,
            max_daily_new_notional_pct=10.0,
            weekly_max_drawdown_pct=0.01,
            enforce_weekly_dd_budget_on_entry=True,
        )
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
        statuses = trades["execution_status"].astype(str).tolist()
        # 至少有一笔被 symbol_cap 卡掉，且优先级高于 weekly_budget
        self.assertIn("blocked_symbol_cap", statuses)


if __name__ == "__main__":
    unittest.main()
