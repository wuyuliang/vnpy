from __future__ import annotations

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.oot_trade_filter_gate import apply_trade_filter_gate


def _base_df() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "symbol": ["IF0", "IF0"],
            "exchange": ["CFFEX", "CFFEX"],
            "interval": ["day", "day"],
            "side": ["long", "short"],
            "bull_mode": ["attack", "attack"],
            "trade_filter_prob": [0.55, 0.55],
            "trade_filter_prob_pctl": [75.0, 75.0],
        }
    )


def test_trade_filter_gate_supports_side_bull_mode_threshold_adjustment() -> None:
    df = _base_df()
    cfg = OotEvaluationConfig(
        use_trade_filter_gate=True,
        trade_filter_gate_mode="cluster_interval_percentile",
        trade_filter_percentile_threshold=70.0,
        trade_filter_percentile_threshold_attack_long_delta=-5.0,
        trade_filter_percentile_threshold_attack_short_delta=10.0,
    )
    gate = pd.Series([True, True], index=df.index, dtype=bool)
    block = pd.Series(["", ""], index=df.index, dtype=object)

    out, gate_out, block_out = apply_trade_filter_gate(
        df,
        cfg=cfg,
        gate_by_legacy=gate,
        model_block_reason=block,
    )

    assert bool(gate_out.iloc[0]) is True
    assert bool(gate_out.iloc[1]) is False
    assert float(out.iloc[0]["trade_filter_gate_threshold"]) == 65.0
    assert float(out.iloc[1]["trade_filter_gate_threshold"]) == 80.0
    assert str(block_out.iloc[1]) == "blocked_trade_filter"


def test_trade_filter_gate_supports_explicit_cluster_interval_side_bull_override() -> None:
    df = _base_df()
    cfg = OotEvaluationConfig(
        use_trade_filter_gate=True,
        trade_filter_gate_mode="cluster_interval_percentile",
        trade_filter_percentile_threshold=70.0,
        trade_filter_percentile_threshold_by_cluster_interval_side_bull_mode={
            "index|day|long|attack": 77.0,
            "index|day|short|attack": 72.0,
        },
    )
    gate = pd.Series([True, True], index=df.index, dtype=bool)
    block = pd.Series(["", ""], index=df.index, dtype=object)

    out, gate_out, _block_out = apply_trade_filter_gate(
        df,
        cfg=cfg,
        gate_by_legacy=gate,
        model_block_reason=block,
    )

    assert float(out.iloc[0]["trade_filter_gate_threshold"]) == 77.0
    assert float(out.iloc[1]["trade_filter_gate_threshold"]) == 72.0
    assert bool(gate_out.iloc[0]) is False
    assert bool(gate_out.iloc[1]) is True
