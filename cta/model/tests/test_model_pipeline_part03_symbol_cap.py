from __future__ import annotations

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.pipeline_oot_evaluation import _evaluate_oot_real_execution


def test_evaluate_oot_real_execution_symbol_notional_cap_blocks_second_entry() -> None:
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
        signal_type_size_multiplier={},
        signal_type_max_concurrent_positions={},
        signal_type_max_notional_pct={},
        trade_filter_percentile_threshold_delta_by_signal_type={},
        trade_filter_raw_threshold_delta_by_signal_type={},
        use_regime_gate=False,
        use_mfe_mae_gate=False,
        mae_penalty=1.0,
        initial_capital=1000.0,
        risk_per_trade_pct=0.01,
        max_single_loss_pct=0.002,
        commission_pct_per_trade=0.0,
        slippage_pct_per_trade=0.0,
        commission_pct_by_cluster_interval={},
        slippage_pct_by_cluster_interval={},
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
    assert int(summary.iloc[0]["blocked_symbol_cap_rows"]) >= 1
    assert "blocked_symbol_cap" in trades["execution_status"].astype(str).tolist()
