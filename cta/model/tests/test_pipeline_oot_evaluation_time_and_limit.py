from __future__ import annotations

import logging

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.pipeline_oot_evaluation import _evaluate_oot_real_execution
from cta.model.tests.test_pipeline_oot_evaluation import _single_day_pred


def _plain_cfg() -> OotEvaluationConfig:
    return OotEvaluationConfig(
        use_stacking_gate=False,
        use_trade_filter_gate=False,
        use_regime_gate=False,
        use_mfe_mae_gate=False,
        use_test_split_only=False,
        require_executed_only=True,
        use_intrabar_stop_tracking=False,
        use_portfolio_constraints=False,
        use_position_sizing=False,
    )


def test_exit_datetime_fixup_logs_warning_and_exposes_counter(caplog) -> None:
    pred = _single_day_pred("long").copy()
    pred.loc[:, "entry_datetime"] = pd.to_datetime(["2020-01-06 09:00:00"])
    pred.loc[:, "exit_datetime"] = pd.to_datetime(["2020-01-06 09:00:00"])
    with caplog.at_level(logging.WARNING, logger="cta.model.oot.pipeline_oot_evaluation"):
        _m, summary, trades = _evaluate_oot_real_execution(pred, cfg=_plain_cfg())
    assert int(summary.iloc[0]["exit_datetime_fixup_rows"]) == 1
    assert int(trades["exit_datetime_fixup"].sum()) == 1
    assert any("exit_datetime fixup applied" in r.message for r in caplog.records)


def test_limit_move_gate_prefers_entry_bar_limit_features() -> None:
    pred = _single_day_pred("long").copy()
    pred["feature_is_limit_up_close"] = [0]
    pred["feature_is_limit_down_close"] = [0]
    pred["entry_feature_is_limit_up_close"] = [1]
    pred["entry_feature_is_limit_down_close"] = [0]
    _m, summary, trades = _evaluate_oot_real_execution(pred, cfg=_plain_cfg())
    assert int(summary.iloc[0]["blocked_limit_move_rows"]) == 1
    assert str(trades.iloc[0]["execution_status"]) == "blocked_limit_move"
