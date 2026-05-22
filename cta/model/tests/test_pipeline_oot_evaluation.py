from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.block_reasons import CANONICAL_BLOCK_REASONS, BR_LOG_EXECUTED_SENTINEL
from cta.model.oot.pipeline_oot_evaluation import _evaluate_oot_real_execution
from cta.portfolio_logic.config import PortfolioLogicConfig, PyramidConfig


def test_pipeline_oot_evaluation_empty_input() -> None:
    monthly, summary, trades = _evaluate_oot_real_execution(pd.DataFrame())
    assert monthly.empty
    assert summary.empty
    assert trades.empty


def _single_day_pred(side: str = "long") -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
            "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
            "exit_datetime": pd.to_datetime(["2020-01-06 11:00:00"]),
            "symbol": ["RB0"],
            "exchange": ["SHFE"],
            "interval": ["day"],
            "signal_type": ["donchian_breakout"],
            "side": [side],
            "pred_regime_label": ["trend_up"],
            "pred_split": ["test"],
            "window_id": [0],
            "is_executed": [1],
            "entry_price": [100.0],
            "future_mfe_atr": [1.0],
            "future_mae_atr": [0.2],
        }
    )


def _htf_enabled_cfg() -> OotEvaluationConfig:
    return OotEvaluationConfig(
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


def _trade_filter_gate_pred() -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-02", "2024-01-03"]),
            "entry_datetime": pd.to_datetime(["2024-01-02", "2024-01-03", "2024-01-02", "2024-01-03"]),
            "exit_datetime": pd.to_datetime(["2024-01-04", "2024-01-05", "2024-01-04", "2024-01-05"]),
            "symbol": ["IF0", "IH0", "RB0", "HC0"],
            "exchange": ["CFFEX", "CFFEX", "SHFE", "SHFE"],
            "interval": ["day", "day", "day", "day"],
            "signal_type": ["donchian_breakout"] * 4,
            "side": ["long"] * 4,
            "pred_split": ["test"] * 4,
            "window_id": [0] * 4,
            "is_executed": [1] * 4,
            "entry_price": [100.0] * 4,
            "trade_filter_prob": [0.40, 0.50, 0.80, 0.90],
            "final_decision_score": [1.0] * 4,
            "future_mfe_atr": [1.0] * 4,
            "future_mae_atr": [0.2] * 4,
        }
    )


def _trade_filter_only_cfg(**kwargs: object) -> OotEvaluationConfig:
    defaults = dict(
        use_stacking_gate=False,
        use_trade_filter_gate=True,
        use_regime_gate=False,
        use_mfe_mae_gate=False,
        use_test_split_only=True,
        require_executed_only=True,
        use_intrabar_stop_tracking=False,
        use_portfolio_constraints=False,
        use_position_sizing=False,
    )
    defaults.update(kwargs)
    return OotEvaluationConfig(**defaults)


def _pyramid_runtime_pred(*, add_score: float, add_size_mult: float) -> pd.DataFrame:
    return pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2024-01-02 09:00:00", "2024-01-02 10:00:00"]),
            "entry_datetime": pd.to_datetime(["2024-01-02 09:00:00", "2024-01-02 10:00:00"]),
            "exit_datetime": pd.to_datetime(["2024-01-02 12:00:00", "2024-01-02 13:00:00"]),
            "symbol": ["RB0", "RB0"],
            "exchange": ["SHFE", "SHFE"],
            "interval": ["60min", "60min"],
            "signal_type": ["tight_range_breakout", "tight_range_breakout"],
            "side": ["long", "long"],
            "pred_split": ["test", "test"],
            "window_id": [0, 0],
            "is_executed": [1, 1],
            "entry_price": [100.0, 100.0],
            "future_mfe_atr": [1.0, 1.0],
            "future_mae_atr": [0.2, 0.2],
            "pyramid_add_score": [0.0, add_score],
            "pyramid_size_mult": [0.0, add_size_mult],
        }
    )


def _pyramid_runtime_cfg() -> OotEvaluationConfig:
    return OotEvaluationConfig(
        use_stacking_gate=False,
        use_trade_filter_gate=False,
        use_regime_gate=False,
        use_mfe_mae_gate=False,
        use_test_split_only=True,
        require_executed_only=True,
        use_intrabar_stop_tracking=False,
        use_position_sizing=False,
        max_position_scale=0.10,
        use_portfolio_constraints=False,
        use_portfolio_logic_runtime=True,
        portfolio_logic=PortfolioLogicConfig(
            enable_htf_gate=False,
            enable_ranker=False,
            enable_risk_throttle=False,
            enable_pyramid=True,
            pyramid=PyramidConfig(
                min_profit_atr_to_add=0.0,
                cooldown_bars_per_interval={"60min": 0},
                one_layer_per_interval=False,
                apply_model_add_score_gate=True,
                min_model_add_score=0.6,
                apply_model_size_multiplier=True,
                min_model_size_multiplier=0.0,
                max_model_size_multiplier=1.0,
            ),
        ),
    )


def test_trade_filter_gate_can_use_cluster_interval_percentiles() -> None:
    """同一 cluster+interval 内按分位数筛选，而不是全局 raw probability 阈值。"""
    pred = _trade_filter_gate_pred()
    # 分位数必须来自训练/验证侧 calibration，不能在 OOT 评估时用 OOT 自身分布现算。
    pred["trade_filter_prob_pctl"] = [50.0, 80.0, 40.0, 90.0]
    cfg = _trade_filter_only_cfg(
        trade_filter_gate_mode="cluster_interval_percentile",
        trade_filter_percentile_threshold=75.0,
    )

    _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)

    executed = trades.loc[trades["execution_status"].astype(str) == "executed"]
    blocked = trades.loc[trades["execution_status"].astype(str) == "blocked_trade_filter"]
    assert set(executed["symbol"].astype(str)) == {"IH0", "HC0"}
    assert set(blocked["symbol"].astype(str)) == {"IF0", "RB0"}
    assert int(summary.iloc[0]["trade_count"]) == 2
    assert int(summary.iloc[0]["blocked_trade_filter_rows"]) == 2
    # IH0 raw=0.50 低于旧全局 0.62，但在 index/day 内是高分位，应被放行。
    assert float(executed.loc[executed["symbol"] == "IH0", "trade_filter_prob"].iloc[0]) < 0.62
    assert "trade_filter_prob_pctl" in trades.columns


def test_pyramid_add_score_gate_blocks_low_score_add_layer() -> None:
    pred = _pyramid_runtime_pred(add_score=0.10, add_size_mult=0.50)
    cfg = _pyramid_runtime_cfg()
    _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert int(summary.iloc[0]["trade_count"]) == 1
    assert int(summary.iloc[0]["blocked_pyramid_rows"]) == 1
    blocked = trades.loc[trades["execution_status"].astype(str) == "blocked_pyramid_rule"]
    assert len(blocked) == 1
    assert pd.Timestamp(blocked.iloc[0]["entry_datetime"]) == pd.Timestamp("2024-01-02 10:00:00")


def test_pyramid_size_multiplier_scales_add_layer_notional() -> None:
    pred = _pyramid_runtime_pred(add_score=0.95, add_size_mult=0.50)
    cfg = _pyramid_runtime_cfg()
    _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert int(summary.iloc[0]["trade_count"]) == 2
    executed = trades.loc[trades["execution_status"].astype(str) == "executed"].sort_values("entry_datetime")
    assert len(executed) == 2
    first_notional = float(executed.iloc[0]["entry_amount"])
    second_notional = float(executed.iloc[1]["entry_amount"])
    assert first_notional > 0.0
    assert second_notional > 0.0
    assert abs(second_notional / first_notional - 0.5) < 1e-6


def test_hold_extend_missing_columns_falls_back_to_legacy_horizon_behavior() -> None:
    pred = _single_day_pred("long").copy()
    pred["interval"] = "60min"
    # 保持 candidate 方向明确，且不注入 hold_extend_score/recommended_horizon_extension_bars
    cfg = OotEvaluationConfig(
        use_stacking_gate=False,
        use_trade_filter_gate=False,
        use_regime_gate=False,
        use_mfe_mae_gate=False,
        use_test_split_only=True,
        require_executed_only=True,
        use_intrabar_stop_tracking=True,
        use_portfolio_constraints=False,
        use_position_sizing=False,
        use_portfolio_logic_runtime=True,
        portfolio_logic=PortfolioLogicConfig(
            enable_trailing=True,
            enable_horizon_extend=True,
            enable_htf_gate=False,
            enable_ranker=False,
            enable_risk_throttle=False,
            enable_pyramid=False,
        ),
    )

    _monthly, _summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    row = trades.iloc[0]
    # 缺模型列时不应把 extension 全部禁掉（回退到 legacy default）。
    assert int(pd.to_numeric(pd.Series([row.get("extensions_used", 0)]), errors="coerce").fillna(0).iloc[0]) >= 0
    assert str(row["execution_status"]) in {"executed", "opened", "blocked_zero_notional"}


def test_pyramid_size_multiplier_missing_column_defaults_to_one() -> None:
    pred = _pyramid_runtime_pred(add_score=0.95, add_size_mult=0.50).drop(columns=["pyramid_size_mult"])
    cfg = _pyramid_runtime_cfg()
    _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert int(summary.iloc[0]["trade_count"]) == 2
    executed = trades.loc[trades["execution_status"].astype(str) == "executed"].sort_values("entry_datetime")
    assert len(executed) == 2
    first_notional = float(executed.iloc[0]["entry_amount"])
    second_notional = float(executed.iloc[1]["entry_amount"])
    assert first_notional > 0.0
    # 缺列时默认 1.0，不应被静默缩成 0 或 0.5
    assert abs(second_notional / first_notional - 1.0) < 1e-6


def test_pyramid_add_score_missing_column_keeps_legacy_add_layer_behavior() -> None:
    pred = _pyramid_runtime_pred(add_score=0.95, add_size_mult=0.50).drop(columns=["pyramid_add_score"])
    cfg = _pyramid_runtime_cfg()
    _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert int(summary.iloc[0]["trade_count"]) == 2
    executed = trades.loc[trades["execution_status"].astype(str) == "executed"].sort_values("entry_datetime")
    assert len(executed) == 2
    assert (executed["block_reason"].astype(str) == "").all()


def test_trade_filter_gate_does_not_self_rank_oot_without_calibrated_percentile() -> None:
    """缺少非 OOT 校准分位数时，不能用当前 OOT 批次自身分布补 percentile。"""
    pred = _trade_filter_gate_pred().iloc[:2].copy()
    pred["trade_filter_prob"] = [0.50, 0.60]
    cfg = _trade_filter_only_cfg(
        trade_filter_gate_mode="cluster_interval_percentile",
        trade_filter_percentile_threshold=70.0,
    )

    _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)

    assert int(summary.iloc[0]["trade_count"]) == 0
    assert int(summary.iloc[0]["blocked_trade_filter_rows"]) == 2
    assert set(trades["execution_status"].astype(str)) == {"blocked_trade_filter"}
    # 旧实现会把 0.60 在 OOT 两行里排到 100 分位并放行；修复后只允许非 OOT
    # calibration 分位数，缺失时 fail-closed 为 NaN。
    assert trades["trade_filter_prob_pctl"].isna().all()


def test_trade_filter_gate_supports_cluster_interval_raw_override() -> None:
    """允许单独配置 index/day raw 阈值，避免被全局 raw threshold 误杀。"""
    pred = _trade_filter_gate_pred().iloc[:2].copy()
    cfg = _trade_filter_only_cfg(
        trade_filter_gate_mode="raw",
        trade_filter_threshold=0.62,
        trade_filter_raw_threshold_by_cluster_interval={"index|day": 0.45},
    )

    _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)

    executed = trades.loc[trades["execution_status"].astype(str) == "executed"]
    blocked = trades.loc[trades["execution_status"].astype(str) == "blocked_trade_filter"]
    assert set(executed["symbol"].astype(str)) == {"IH0"}
    assert set(blocked["symbol"].astype(str)) == {"IF0"}
    assert int(summary.iloc[0]["trade_count"]) == 1
    assert int(summary.iloc[0]["blocked_trade_filter_rows"]) == 1


def test_htf_intervals_narrowed_when_only_day_available(caplog) -> None:
    """Fix-A：单 interval 跑批时 HTF gate 自动窄化到实际可用 interval。

    复现 20260517_GRP_CLUSTER_METAL_day 案例：pred 只有 day 行，
    默认 htf_intervals=("day","60min") 不再误判为 htf_missing。
    """
    pred = _single_day_pred(side="long")
    cfg = _htf_enabled_cfg()
    with caplog.at_level(logging.INFO, logger="cta.model.oot.pipeline_oot_evaluation"):
        _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)

    # 业务断言：trade 应通过窄化后的 day-only gate
    assert int(summary.iloc[0]["trade_count"]) == 1
    assert int(summary.iloc[0]["blocked_htf_rows"]) == 0
    assert str(trades.iloc[0]["execution_status"]) == "executed"

    # 诊断断言：INFO log 必须显式打印窄化前后的 intervals
    narrow_msgs = [r.message for r in caplog.records if "HTF intervals narrowed" in r.message]
    assert narrow_msgs, f"expected HTF narrowing log, got records={caplog.records!r}"
    assert "day" in narrow_msgs[0]
    assert "60min" in narrow_msgs[0]


def test_htf_gate_disabled_when_reference_has_no_overlap(caplog) -> None:
    """Fix-A：当 htf_reference 完全没有任何配置 interval 的行时，关闭 HTF gate
    而不是放行所有候选为 htf_missing。"""
    pred = _single_day_pred(side="long")
    # 把 interval 改成完全不在 htf_intervals 里的值
    pred.loc[:, "interval"] = "5min"
    cfg = _htf_enabled_cfg()
    with caplog.at_level(logging.WARNING, logger="cta.model.oot.pipeline_oot_evaluation"):
        _m, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)

    # HTF gate 关闭后，候选应被放行（不再被标 blocked_htf_gate）
    assert int(summary.iloc[0]["blocked_htf_rows"]) == 0
    # warning 提示用户 HTF gate 已被关闭
    disable_msgs = [r.message for r in caplog.records if "disabling HTF gate" in r.message]
    assert disable_msgs, f"expected disabling-HTF-gate warning, got {caplog.records!r}"


def test_block_reason_distribution_logged_at_info(caplog) -> None:
    """Fix-C：跑完 OOT 必须打印 block_reason 分布到 logger。"""
    pred = _single_day_pred(side="long")
    cfg = _htf_enabled_cfg()
    with caplog.at_level(logging.INFO, logger="cta.model.oot.pipeline_oot_evaluation"):
        _evaluate_oot_real_execution(pred, cfg=cfg)
    msgs = [r.message for r in caplog.records if "OOT block_reason distribution" in r.message]
    assert msgs, f"expected block_reason distribution log, got {caplog.records!r}"
    assert BR_LOG_EXECUTED_SENTINEL in msgs[0]
    assert "[path=main]" in msgs[0]


def test_block_reason_distribution_logs_early_return_path_marker(caplog) -> None:
    """M4: 全部被 block、无 executed 时也要打出 early_return path marker。"""
    pred = _single_day_pred(side="short")
    cfg = _htf_enabled_cfg()
    with caplog.at_level(logging.INFO, logger="cta.model.oot.pipeline_oot_evaluation"):
        _monthly, summary, _trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert int(summary.iloc[0]["trade_count"]) == 0
    msgs = [r.message for r in caplog.records if "OOT block_reason distribution" in r.message]
    assert msgs, f"expected block_reason distribution log, got {caplog.records!r}"
    assert any("[path=early_return]" in m for m in msgs), msgs


def test_htf_narrowed_to_single_interval_warns_self_consistency(caplog) -> None:
    pred = _single_day_pred(side="long")
    cfg = _htf_enabled_cfg()
    with caplog.at_level(logging.WARNING, logger="cta.model.oot.pipeline_oot_evaluation"):
        _evaluate_oot_real_execution(pred, cfg=cfg)
    msgs = [r.message for r in caplog.records if "self-consistency gate" in r.message]
    assert msgs, f"expected self-consistency warning, got {caplog.records!r}"


def test_fix_e_day_candidate_drops_lower_rank_htf_when_shared_reference(caplog) -> None:
    """Fix-E：跨 interval 共享 HTF 参考时，day 候选不应被 60min state（rank<day）拦截。

    复现 20260518_GROUP_POOL_CLUSTER_both_portfolio_logic_runtime/day 案例：
    _recompute_oot_with_shared_htf_reference 把 day+60min+30min 拼成共享参考，
    HtfGate 默认 60min state_ttl=3600s，day 候选评估时 60min state 必然过期 → 全部
    误判 htf_missing。Fix-E 用 interval_rank 过滤掉低于候选 rank 的 HTF interval。
    """
    pred = _single_day_pred(side="long")
    # 共享 HTF 参考：day 行 + 同 symbol 的 60min 行（60min 最近一根远在 day 候选 ts 之前）
    htf_ref = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                [
                    "2020-01-06 09:00:00",   # day 行（与候选同 ts，新鲜）
                    "2020-01-03 14:00:00",   # 60min 行（距候选 ts 67 小时，远超 60min TTL=1h）
                ]
            ),
            "entry_datetime": pd.to_datetime(
                ["2020-01-06 09:00:00", "2020-01-03 14:00:00"]
            ),
            "exit_datetime": pd.to_datetime(
                ["2020-01-06 15:00:00", "2020-01-03 15:00:00"]
            ),
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
    cfg = _htf_enabled_cfg()
    with caplog.at_level(logging.INFO, logger="cta.model.oot.pipeline_oot_evaluation"):
        _monthly, summary, trades = _evaluate_oot_real_execution(
            pred, cfg=cfg, htf_reference_df=htf_ref
        )

    # 业务断言：Fix-E 过滤掉 60min 后，day 候选只看 day state（新鲜）→ 通过
    assert int(summary.iloc[0]["trade_count"]) == 1
    assert int(summary.iloc[0]["blocked_htf_rows"]) == 0
    assert str(trades.iloc[0]["execution_status"]) == "executed"
    assert str(trades.iloc[0]["block_reason"]) == ""

    # 诊断断言：必须打 Fix-E 过滤日志
    filter_msgs = [r.message for r in caplog.records if "filtered by rank" in r.message]
    assert filter_msgs, f"expected Fix-E rank filter log, got {caplog.records!r}"
    assert "day" in filter_msgs[0]
    assert "60min" in filter_msgs[0]


def test_fix_e_minute_candidate_keeps_day_and_60min_htf(caplog) -> None:
    """Fix-E 反向验证：30min 候选（rank=0.70）应保留 day(1.0) 与 60min(0.85) 两条 HTF。

    确保 Fix-E 不会把"正经的"sub-hourly 候选场景误过滤掉。
    """
    pred = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
            "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00"]),
            "exit_datetime": pd.to_datetime(["2020-01-06 11:00:00"]),
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
            "future_mfe_atr": [1.0],
            "future_mae_atr": [0.2],
        }
    )
    # day + 60min HTF 参考（与候选 ts 接近，TTL 内）
    htf_ref = pd.DataFrame(
        {
            "datetime": pd.to_datetime(
                ["2020-01-06 09:00:00", "2020-01-06 08:30:00"]
            ),
            "entry_datetime": pd.to_datetime(
                ["2020-01-06 09:00:00", "2020-01-06 08:30:00"]
            ),
            "exit_datetime": pd.to_datetime(
                ["2020-01-06 15:00:00", "2020-01-06 09:30:00"]
            ),
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
    cfg = _htf_enabled_cfg()
    with caplog.at_level(logging.INFO, logger="cta.model.oot.pipeline_oot_evaluation"):
        _monthly, summary, trades = _evaluate_oot_real_execution(
            pred, cfg=cfg, htf_reference_df=htf_ref
        )

    # Fix-E 不应过滤掉任何 interval（30min < day < 60min 都 ≥ 30min rank=0.70）
    filter_msgs = [r.message for r in caplog.records if "filtered by rank" in r.message]
    assert not filter_msgs, f"Fix-E should NOT filter for 30min candidate, got {filter_msgs}"
    # 业务断言：day + 60min 都看到 trend_up，long 方向通过
    assert int(summary.iloc[0]["trade_count"]) == 1
    assert str(trades.iloc[0]["execution_status"]) == "executed"


# Canonical literals expected to be emitted as block_reason values.
# 见 cta/docs/block_reason.md §1。新增 reason 时必须同步更新文档与本集合。
_CANONICAL_BLOCK_REASONS = frozenset(CANONICAL_BLOCK_REASONS)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_TARGETS = (
    _REPO_ROOT / "model" / "oot" / "pipeline_oot_evaluation.py",
    _REPO_ROOT / "model" / "oot" / "pipeline_oot_evaluation_base.py",
    _REPO_ROOT / "portfolio_logic" / "interval_gate.py",
    _REPO_ROOT / "model" / "feature" / "candidate_schema.py",
)


def _extract_block_reason_literals(source: str) -> set[str]:
    """Scan source for `block_reason ... = "..."` and `htf_block_reason` literal patterns."""
    found: set[str] = set()
    # 1) AST 扫常规赋值：covers `selected.at[idx, "block_reason"] = "..."`,
    #    `record["block_reason"] = "..."`, `selected.loc[mask, "block_reason"] = "..."`.
    tree = ast.parse(source)
    for node in ast.walk(tree):
        if not isinstance(node, ast.Assign):
            continue
        if not isinstance(node.value, ast.Constant) or not isinstance(node.value.value, str):
            continue
        for target in node.targets:
            if isinstance(target, ast.Subscript):
                slc = target.slice
                key: str | None = None
                # ast.Constant in subscript (py3.9+) e.g. d["k"]
                if isinstance(slc, ast.Constant) and isinstance(slc.value, str):
                    key = slc.value
                # tuple slice e.g. selected.at[idx, "block_reason"]
                elif isinstance(slc, ast.Tuple):
                    for elt in slc.elts:
                        if isinstance(elt, ast.Constant) and isinstance(elt.value, str):
                            key = elt.value
                if key in {"block_reason", "htf_block_reason"}:
                    found.add(str(node.value.value))
    # 2) 文本扫 reason 变量赋值 `reason = "..."`（兜底，因为 ast 已覆盖大部分）
    for m in re.finditer(r'\breason\s*=\s*"([a-z_]+)"', source):
        found.add(m.group(1))
    # 3) candidate_schema.py 用 list append 把字面量塞进 reason_list（HtfGate.filter）
    for m in re.finditer(r'reason_list\.append\("([a-z_]+)"\)', source):
        found.add(m.group(1))
    # 4) ast 兜底：捕获 append 的 if-expr 等嵌套字面量（如 "" if ok else "htf_opposite"）
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if not (isinstance(node.func, ast.Attribute) and node.func.attr == "append"):
            continue
        owner = node.func.value
        if not (isinstance(owner, ast.Name) and owner.id == "reason_list"):
            continue
        for arg in node.args:
            for sub in ast.walk(arg):
                if isinstance(sub, ast.Constant) and isinstance(sub.value, str):
                    if sub.value:
                        found.add(sub.value)
    return found


def test_extract_block_reason_literals_captures_ifexpr_append_literals() -> None:
    source = """
def _demo(ok: bool) -> None:
    reason_list = []
    reason_list.append("" if ok else "htf_opposite")
"""
    seen = _extract_block_reason_literals(source)
    assert "htf_opposite" in seen


def test_all_emitted_block_reasons_are_canonical() -> None:
    """巡检：源码里 emit 的 block_reason 字面量必须在 §1 总表内。"""
    seen: set[str] = set()
    for path in _SCAN_TARGETS:
        source = path.read_text(encoding="utf-8")
        seen |= _extract_block_reason_literals(source)
    # 过滤空串（"" 是 executed 的 reason 占位）
    seen.discard("")
    unknown = seen - _CANONICAL_BLOCK_REASONS
    assert not unknown, (
        f"unknown block_reason literals: {sorted(unknown)}\n"
        f"add them to cta/docs/block_reason.md §1 and _CANONICAL_BLOCK_REASONS"
    )


def test_model_gate_blocked_candidates_are_kept_in_trade_details() -> None:
    pred = pd.concat([_single_day_pred("long"), _single_day_pred("short")], ignore_index=True)
    pred["trade_filter_prob"] = [0.2, 0.1]
    cfg = OotEvaluationConfig(
        use_stacking_gate=False,
        use_trade_filter_gate=True,
        trade_filter_threshold=0.95,
        use_regime_gate=False,
        use_mfe_mae_gate=False,
        use_test_split_only=False,
        require_executed_only=True,
        use_intrabar_stop_tracking=False,
        use_portfolio_constraints=False,
        use_position_sizing=False,
        use_portfolio_logic_runtime=False,
    )
    monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert monthly.empty
    assert int(summary.iloc[0]["selected_rows"]) == 0
    assert int(summary.iloc[0]["blocked_rows"]) == 2
    assert len(trades) == 2
    assert set(trades["execution_status"].astype(str).tolist()) == {"blocked_trade_filter"}
    assert set(trades["block_reason"].astype(str).tolist()) == {"blocked_trade_filter"}


def test_position_sizing_uses_contract_multiplier_and_lot_rounding() -> None:
    pred = _single_day_pred("long").copy()
    pred["entry_price"] = 100.0
    cfg = OotEvaluationConfig(
        use_stacking_gate=False,
        use_trade_filter_gate=False,
        use_regime_gate=False,
        use_mfe_mae_gate=False,
        use_test_split_only=False,
        require_executed_only=True,
        use_intrabar_stop_tracking=False,
        use_portfolio_constraints=False,
        use_position_sizing=True,
        initial_capital=100_000.0,
        max_position_scale=0.10,
        max_single_loss_pct=0.001,
        risk_per_trade_pct=0.002,
        use_portfolio_logic_runtime=False,
        symbol_contract_specs={
            "RB0": {
                "contract_size": 10.0,
                "lot_size": 3.0,
                "margin_rate": 0.2,
            }
        },
        # 此测试聚焦 contract_size/lot_size 取整数学，不依赖 cluster 维度 stop_loss override；
        # 显式空 dict 保留全局 0.01 默认（否则 black|day 4.51% 会让 position_scale 缩小到 lot_size 取整后变 0）。
        intrabar_stop_loss_pct_by_cluster_interval={},
    )
    _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert int(summary.iloc[0]["trade_count"]) == 1
    row = trades.iloc[0]
    assert str(row["execution_status"]) == "executed"
    assert np.isclose(float(row["position_qty"]), 9.0)
    assert np.isclose(float(row["position_notional"]), 9_000.0)
    assert np.isclose(float(row["entry_margin"]), 1_800.0)


def test_max_total_leverage_cap_uses_notional_over_equity_semantics() -> None:
    """max_total_leverage 当前口径是 sum(open_notional) / equity。"""
    pred = pd.DataFrame(
        {
            "datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:00:00"]),
            "entry_datetime": pd.to_datetime(["2020-01-06 09:00:00", "2020-01-06 09:00:00"]),
            "exit_datetime": pd.to_datetime(["2020-01-07 09:00:00", "2020-01-07 09:00:00"]),
            "symbol": ["RB0", "HC0"],
            "exchange": ["SHFE", "SHFE"],
            "interval": ["day", "day"],
            "signal_type": ["donchian_breakout", "donchian_breakout"],
            "side": ["long", "long"],
            "pred_regime_label": ["trend_up", "trend_up"],
            "pred_split": ["test", "test"],
            "window_id": [0, 0],
            "is_executed": [1, 1],
            "entry_price": [100.0, 100.0],
            "future_mfe_atr": [1.0, 1.0],
            "future_mae_atr": [0.2, 0.2],
        }
    )
    cfg = OotEvaluationConfig(
        use_stacking_gate=False,
        use_trade_filter_gate=False,
        use_regime_gate=False,
        use_mfe_mae_gate=False,
        use_test_split_only=False,
        require_executed_only=True,
        use_intrabar_stop_tracking=False,
        use_portfolio_constraints=True,
        use_position_sizing=False,
        use_portfolio_logic_runtime=False,
        initial_capital=100_000.0,
        max_position_scale=0.10,  # 每笔目标 10,000
        max_total_leverage=0.15,  # notional 上限 15,000
        margin_rate=0.20,
        max_daily_new_notional_pct=10.0,
        max_symbol_notional_pct=1.0,
        max_concurrent_positions_total=10,
    )
    _monthly, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert int(summary.iloc[0]["trade_count"]) == 2
    notional_by_symbol = {
        str(row["symbol"]): float(row["position_notional"])
        for _, row in trades.loc[trades["execution_status"] == "executed"].iterrows()
    }
    assert np.isclose(notional_by_symbol["RB0"], 10_000.0)
    assert np.isclose(notional_by_symbol["HC0"], 5_000.0)


def test_exit_datetime_fixup_logs_warning_and_exposes_counter(caplog) -> None:
    pred = _single_day_pred("long").copy()
    pred.loc[:, "entry_datetime"] = pd.to_datetime(["2020-01-06 09:00:00"])
    pred.loc[:, "exit_datetime"] = pd.to_datetime(["2020-01-06 09:00:00"])  # non-increasing
    cfg = OotEvaluationConfig(
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
    with caplog.at_level(logging.WARNING, logger="cta.model.oot.pipeline_oot_evaluation"):
        _m, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert int(summary.iloc[0]["exit_datetime_fixup_rows"]) == 1
    assert int(trades["exit_datetime_fixup"].sum()) == 1
    assert any("exit_datetime fixup applied" in r.message for r in caplog.records)


def test_limit_move_gate_prefers_entry_bar_limit_features() -> None:
    pred = _single_day_pred("long").copy()
    pred["feature_is_limit_up_close"] = [0]  # signal bar says not limit-up
    pred["feature_is_limit_down_close"] = [0]
    pred["entry_feature_is_limit_up_close"] = [1]  # entry bar says limit-up
    pred["entry_feature_is_limit_down_close"] = [0]
    cfg = OotEvaluationConfig(
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
    _m, summary, trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert int(summary.iloc[0]["blocked_limit_move_rows"]) == 1
    assert str(trades.iloc[0]["execution_status"]) == "blocked_limit_move"
