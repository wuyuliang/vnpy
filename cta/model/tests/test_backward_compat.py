"""Backward-compat checks for portfolio-logic runtime switches."""
from __future__ import annotations

import unittest

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.pipeline_oot_evaluation import _evaluate_oot_real_execution
from cta.portfolio_logic.config import PortfolioLogicConfig


class TestBackwardCompat(unittest.TestCase):
    def test_disable_all_portfolio_logic_matches_legacy_execution(self) -> None:
        pred = pd.DataFrame(
            {
                "datetime": pd.to_datetime(["2020-01-10", "2020-01-20"]),
                "entry_datetime": pd.to_datetime(["2020-01-10", "2020-01-20"]),
                "exit_datetime": pd.to_datetime(["2020-01-11", "2020-01-21"]),
                "symbol": ["RB0", "RB0"],
                "exchange": ["SHFE", "SHFE"],
                "interval": ["60min", "60min"],
                "signal_type": ["tight_range_breakout", "tight_range_breakout"],
                "side": ["long", "short"],
                "pred_split": ["test", "test"],
                "window_id": [0, 0],
                "is_executed": [1, 1],
                "entry_price": [100.0, 100.0],
                "future_mfe_atr": [2.0, 0.0],
                "future_mae_atr": [1.0, 1.0],
            }
        )
        base_cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_intrabar_stop_tracking=False,
            use_portfolio_constraints=False,
        )
        pl_cfg = OotEvaluationConfig(
            use_trade_filter_gate=False,
            use_regime_gate=False,
            use_mfe_mae_gate=False,
            use_intrabar_stop_tracking=False,
            use_portfolio_constraints=False,
            use_portfolio_logic_runtime=True,
            portfolio_logic=PortfolioLogicConfig(
                enable_htf_gate=False,
                enable_ranker=False,
                enable_trailing=False,
                enable_pyramid=False,
                enable_horizon_extend=False,
                enable_score_calibration=False,
                enable_risk_throttle=False,
            ),
        )
        _m0, s0, t0 = _evaluate_oot_real_execution(pred, cfg=base_cfg)
        _m1, s1, t1 = _evaluate_oot_real_execution(pred, cfg=pl_cfg)
        self.assertEqual(int(s0.iloc[0]["trade_count"]), int(s1.iloc[0]["trade_count"]))
        self.assertAlmostEqual(float(s0.iloc[0]["net_pnl"]), float(s1.iloc[0]["net_pnl"]), places=8)
        self.assertListEqual(
            t0["execution_status"].astype(str).tolist(),
            t1["execution_status"].astype(str).tolist(),
        )


if __name__ == "__main__":
    unittest.main()
