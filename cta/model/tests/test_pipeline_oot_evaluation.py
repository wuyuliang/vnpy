from __future__ import annotations

import ast
import logging
import re
from pathlib import Path

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.block_reasons import CANONICAL_BLOCK_REASONS, BR_LOG_EXECUTED_SENTINEL
from cta.model.pipeline_oot_evaluation import _evaluate_oot_real_execution
from cta.portfolio_logic.config import PortfolioLogicConfig


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


def test_htf_intervals_narrowed_when_only_day_available(caplog) -> None:
    """Fix-A：单 interval 跑批时 HTF gate 自动窄化到实际可用 interval。

    复现 20260517_GRP_CLUSTER_METAL_day 案例：pred 只有 day 行，
    默认 htf_intervals=("day","60min") 不再误判为 htf_missing。
    """
    pred = _single_day_pred(side="long")
    cfg = _htf_enabled_cfg()
    with caplog.at_level(logging.INFO, logger="cta.model.pipeline_oot_evaluation"):
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
    with caplog.at_level(logging.WARNING, logger="cta.model.pipeline_oot_evaluation"):
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
    with caplog.at_level(logging.INFO, logger="cta.model.pipeline_oot_evaluation"):
        _evaluate_oot_real_execution(pred, cfg=cfg)
    msgs = [r.message for r in caplog.records if "OOT block_reason distribution" in r.message]
    assert msgs, f"expected block_reason distribution log, got {caplog.records!r}"
    assert BR_LOG_EXECUTED_SENTINEL in msgs[0]
    assert "[path=main]" in msgs[0]


def test_block_reason_distribution_logs_early_return_path_marker(caplog) -> None:
    """M4: 全部被 block、无 executed 时也要打出 early_return path marker。"""
    pred = _single_day_pred(side="short")
    cfg = _htf_enabled_cfg()
    with caplog.at_level(logging.INFO, logger="cta.model.pipeline_oot_evaluation"):
        _monthly, summary, _trades = _evaluate_oot_real_execution(pred, cfg=cfg)
    assert int(summary.iloc[0]["trade_count"]) == 0
    msgs = [r.message for r in caplog.records if "OOT block_reason distribution" in r.message]
    assert msgs, f"expected block_reason distribution log, got {caplog.records!r}"
    assert any("[path=early_return]" in m for m in msgs), msgs


def test_htf_narrowed_to_single_interval_warns_self_consistency(caplog) -> None:
    pred = _single_day_pred(side="long")
    cfg = _htf_enabled_cfg()
    with caplog.at_level(logging.WARNING, logger="cta.model.pipeline_oot_evaluation"):
        _evaluate_oot_real_execution(pred, cfg=cfg)
    msgs = [r.message for r in caplog.records if "self-consistency gate" in r.message]
    assert msgs, f"expected self-consistency warning, got {caplog.records!r}"


# Canonical literals expected to be emitted as block_reason values.
# 见 cta/docs/block_reason.md §1。新增 reason 时必须同步更新文档与本集合。
_CANONICAL_BLOCK_REASONS = frozenset(CANONICAL_BLOCK_REASONS)

_REPO_ROOT = Path(__file__).resolve().parents[2]
_SCAN_TARGETS = (
    _REPO_ROOT / "model" / "pipeline_oot_evaluation_source.py.txt",
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
