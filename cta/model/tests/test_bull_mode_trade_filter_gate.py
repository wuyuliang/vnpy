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


def test_trade_filter_gate_blocks_cross_sectional_when_bypass_not_configured() -> None:
    """H4 回归：默认 trade_filter_bypass_signal_types=() → rotation 信号同样被 gate。"""
    df = _base_df().iloc[[0]].copy()
    df["signal_type"] = "cross_sectional_momentum"
    df["bull_mode"] = "normal"
    df["trade_filter_prob_pctl"] = 10.0
    cfg = OotEvaluationConfig(
        use_trade_filter_gate=True,
        trade_filter_gate_mode="cluster_interval_percentile",
        trade_filter_percentile_threshold=70.0,
    )
    # 默认 bypass 元组为空
    assert cfg.trade_filter_bypass_signal_types == ()
    gate = pd.Series([True], index=df.index, dtype=bool)
    block = pd.Series([""], index=df.index, dtype=object)

    _out, gate_out, _block_out = apply_trade_filter_gate(
        df, cfg=cfg, gate_by_legacy=gate, model_block_reason=block,
    )
    # 10 < 70 → 应被拦截
    assert bool(gate_out.iloc[0]) is False


def test_trade_filter_gate_bypasses_cross_sectional_rotation_signal() -> None:
    """显式 opt-in bypass 时，rotation 信号绕过 trade-filter gate。"""
    df = _base_df().iloc[[0]].copy()
    df["signal_type"] = "cross_sectional_momentum"
    df["bull_mode"] = "normal"
    df["trade_filter_prob_pctl"] = 10.0
    cfg = OotEvaluationConfig(
        use_trade_filter_gate=True,
        trade_filter_gate_mode="cluster_interval_percentile",
        trade_filter_percentile_threshold=70.0,
        trade_filter_bypass_signal_types=("cross_sectional_momentum",),  # 显式启用
    )
    gate = pd.Series([True], index=df.index, dtype=bool)
    block = pd.Series([""], index=df.index, dtype=object)

    out, gate_out, block_out = apply_trade_filter_gate(
        df, cfg=cfg, gate_by_legacy=gate, model_block_reason=block,
    )

    assert bool(gate_out.iloc[0]) is True
    assert str(block_out.iloc[0]) == ""
    assert float(out.iloc[0]["trade_filter_gate_threshold"]) == 70.0


def test_trade_filter_gate_bypasses_spread_arbitrage_signal() -> None:
    """spread_arbitrage 可显式加入 bypass 列表，避免重复被 trade-filter 拦截。"""
    df = _base_df().iloc[[0]].copy()
    df["signal_type"] = "spread_arbitrage"
    df["bull_mode"] = "normal"
    df["trade_filter_prob_pctl"] = 10.0
    cfg = OotEvaluationConfig(
        use_trade_filter_gate=True,
        trade_filter_gate_mode="cluster_interval_percentile",
        trade_filter_percentile_threshold=70.0,
        trade_filter_bypass_signal_types=("spread_arbitrage",),
    )
    gate = pd.Series([True], index=df.index, dtype=bool)
    block = pd.Series([""], index=df.index, dtype=object)

    out, gate_out, block_out = apply_trade_filter_gate(
        df, cfg=cfg, gate_by_legacy=gate, model_block_reason=block,
    )

    assert bool(gate_out.iloc[0]) is True
    assert str(block_out.iloc[0]) == ""
    assert float(out.iloc[0]["trade_filter_gate_threshold"]) == 70.0


def test_trade_filter_gate_supports_trend_aware_percentile_delta() -> None:
    df = _base_df().iloc[[0]].copy()
    df["bull_mode"] = "normal"
    df["ma_alignment"] = [2.0]
    df["regime_label"] = ["trend_up"]
    df["realized_vol_rank"] = [0.8]
    cfg = OotEvaluationConfig(
        use_trade_filter_gate=True,
        trade_filter_gate_mode="cluster_interval_percentile",
        trade_filter_percentile_threshold=70.0,
        use_trend_aware_trade_filter=True,
        trend_threshold_delta_pctl=-10.0,
        trend_aware_trade_filter_enabled_by_cluster_interval={"index|day": True},
    )
    gate = pd.Series([True], index=df.index, dtype=bool)
    block = pd.Series([""], index=df.index, dtype=object)
    out, gate_out, _ = apply_trade_filter_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=block)

    # 75 >= (70-10) => pass
    assert float(out.iloc[0]["trade_filter_gate_threshold"]) == 60.0
    assert bool(gate_out.iloc[0]) is True
    assert float(out.iloc[0]["trend_aware_threshold_delta"]) == -10.0


def test_trade_filter_gate_trend_aware_not_applied_when_disabled_key() -> None:
    df = _base_df().iloc[[0]].copy()
    df["bull_mode"] = "normal"
    df["ma_alignment"] = [2.0]
    df["regime_label"] = ["trend_up"]
    df["realized_vol_rank"] = [0.8]
    cfg = OotEvaluationConfig(
        use_trade_filter_gate=True,
        trade_filter_gate_mode="cluster_interval_percentile",
        trade_filter_percentile_threshold=70.0,
        use_trend_aware_trade_filter=True,
        trend_threshold_delta_pctl=-10.0,
        trend_aware_trade_filter_enabled_by_cluster_interval={"metal|day": True},
    )
    gate = pd.Series([True], index=df.index, dtype=bool)
    block = pd.Series([""], index=df.index, dtype=object)
    out, gate_out, _ = apply_trade_filter_gate(df, cfg=cfg, gate_by_legacy=gate, model_block_reason=block)
    assert float(out.iloc[0]["trade_filter_gate_threshold"]) == 70.0
    assert bool(gate_out.iloc[0]) is True
    assert float(out.iloc[0]["trend_aware_threshold_delta"]) == 0.0
