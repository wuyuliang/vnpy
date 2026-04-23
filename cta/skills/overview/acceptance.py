"""§01 目标设定 / Objectives —— 策略准入门槛 (Gate A / Gate B) 判定.

对应 cta/cta_skills/00_overview_methodology/01_objectives.md §4-§6。

功能
----
1. REQUIRED_COLUMNS: 回测 summary 必须包含的列（约束所有策略的 reporter 输出）
2. load_acceptance_config(): 读 cta/skills/configs/acceptance.yaml
3. assert_passes_gate_a / gate_b: 对单行 summary 判定是否过门槛
4. assess_against_gates: 批量判定一个 summary 表, 返回 GateDecision 列表

设计要点
--------
- yaml 用 `{op, value}` 描述每个阈值；支持 `>= / <= / > / < / ==`，
  这样新增指标 / 调整方向都不用改代码
- 所有函数均 pure：输入 DataFrame/Series + config dict，输出判定结果
- 不依赖具体频率：gate_a / gate_b 由调用方根据 interval 选择
"""
from __future__ import annotations

import logging
import operator
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, Dict, List, Mapping

import pandas as pd
import yaml

from cta.skills import CONFIG_DIR

logger = logging.getLogger(__name__)

DEFAULT_ACCEPTANCE_YAML: Path = CONFIG_DIR / "acceptance.yaml"

# 提供给其它模块的静态常量（从 yaml 默认值同步；修改 yaml 后请重启进程）
REQUIRED_COLUMNS: List[str] = [
    "symbol", "interval",
    "total_return", "annual_return",
    "max_drawdown", "sharpe", "calmar",
    "win_rate", "profit_factor", "avg_holding_bars",
    "trade_count", "turnover",
]

_OPS: Dict[str, Callable[[float, float], bool]] = {
    ">=": operator.ge,
    "<=": operator.le,
    ">":  operator.gt,
    "<":  operator.lt,
    "==": operator.eq,
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class GateDecision:
    """单行 summary 对某个 gate 的判定结果。"""
    gate: str                                 # 'A' | 'B'
    passed: bool
    failed_checks: List[str] = field(default_factory=list)
    missing_columns: List[str] = field(default_factory=list)
    detail: Dict[str, Any] = field(default_factory=dict)

    def as_row(self) -> Dict[str, Any]:
        return {
            "gate": self.gate,
            "passed": int(self.passed),
            "failed_checks": ";".join(self.failed_checks),
            "missing_columns": ";".join(self.missing_columns),
        }


# ---------------------------------------------------------------------------
# 配置读取
# ---------------------------------------------------------------------------
def load_acceptance_config(path: Path | str | None = None) -> Dict[str, Any]:
    """读 acceptance.yaml。不传 path 则走默认路径。"""
    p = Path(path) if path else DEFAULT_ACCEPTANCE_YAML
    if not p.exists():
        raise FileNotFoundError(f"acceptance config 不存在: {p}")
    with open(p, "r", encoding="utf-8") as fh:
        cfg = yaml.safe_load(fh) or {}
    # 基本校验
    for k in ("gate_a", "gate_b"):
        if k not in cfg:
            raise ValueError(f"{p.name} 缺少 '{k}' 小节")
        if "thresholds" not in cfg[k] or not cfg[k]["thresholds"]:
            raise ValueError(f"{p.name}::{k} 缺少 thresholds")
    return cfg


# ---------------------------------------------------------------------------
# 核心判定
# ---------------------------------------------------------------------------
def _compare(value: float, op: str, threshold: float) -> bool:
    if op not in _OPS:
        raise ValueError(f"不支持的比较符: {op!r}（合法值: {list(_OPS)}）")
    return bool(_OPS[op](float(value), float(threshold)))


def _check_thresholds(
    row: Mapping[str, Any],
    thresholds: Mapping[str, Mapping[str, Any]],
) -> tuple[List[str], List[str]]:
    """返回 (failed_checks, missing_columns)。"""
    failed: List[str] = []
    missing: List[str] = []
    for metric, rule in thresholds.items():
        if metric not in row or pd.isna(row[metric]):
            missing.append(metric)
            continue
        op = str(rule.get("op", ">=")).strip()
        val = rule.get("value")
        if val is None:
            raise ValueError(f"阈值配置缺 value: {metric}")
        if not _compare(row[metric], op, float(val)):
            failed.append(f"{metric}{op}{val}(actual={row[metric]:.4f})")
    return failed, missing


def _check_gate(
    row: Mapping[str, Any],
    gate_cfg: Mapping[str, Any],
    gate_name: str,
) -> GateDecision:
    failed, missing = _check_thresholds(row, gate_cfg["thresholds"])
    passed = (not failed) and (not missing)
    return GateDecision(
        gate=gate_name,
        passed=passed,
        failed_checks=failed,
        missing_columns=missing,
        detail={"description": gate_cfg.get("description", "")},
    )


def assert_passes_gate_a(
    row: Mapping[str, Any] | pd.Series,
    config: Mapping[str, Any] | None = None,
) -> GateDecision:
    """Gate A（中频趋势）判定。不抛异常，返回 GateDecision."""
    cfg = config if config is not None else load_acceptance_config()
    return _check_gate(row, cfg["gate_a"], "A")


def assert_passes_gate_b(
    row: Mapping[str, Any] | pd.Series,
    config: Mapping[str, Any] | None = None,
) -> GateDecision:
    """Gate B（日内反转/震荡）判定。"""
    cfg = config if config is not None else load_acceptance_config()
    return _check_gate(row, cfg["gate_b"], "B")


# ---------------------------------------------------------------------------
# 批量判定
# ---------------------------------------------------------------------------
# 周期 → 推荐 gate 的映射（根据 02_research_boundary.md §5 的周期配比）
INTERVAL_TO_GATE: Dict[str, str] = {
    "day":      "A",
    "minute60": "A",
    "minute30": "A",
    "minute15": "B",
    "minute5":  "B",
    "minute":   "B",
}


def assess_against_gates(
    summary: pd.DataFrame,
    config: Mapping[str, Any] | None = None,
    gate_override: Mapping[str, str] | None = None,
) -> pd.DataFrame:
    """
    对 summary（每行一个 (symbol, interval) 组合）批量判定准入。

    Parameters
    ----------
    summary : 包含至少 `interval` + thresholds 所需指标列的 DataFrame
    config  : 可选，预加载的 acceptance 配置
    gate_override : 可选，{interval: 'A'|'B'} 强制指定某些 interval 走哪条 gate

    Returns
    -------
    DataFrame, 与 summary 同长度，新增列:
        gate, passed, failed_checks, missing_columns
    不修改入参。
    """
    if summary is None or summary.empty:
        return pd.DataFrame(
            columns=list(summary.columns if summary is not None else [])
            + ["gate", "passed", "failed_checks", "missing_columns"]
        )
    cfg = config if config is not None else load_acceptance_config()
    override = dict(gate_override or {})

    # 缺 interval 列：抛更友好的错误
    if "interval" not in summary.columns:
        raise KeyError("summary 缺少 'interval' 列，无法按频率自动选 gate")

    out_rows = []
    for _, row in summary.iterrows():
        itv = str(row["interval"]).strip()
        gate = override.get(itv) or INTERVAL_TO_GATE.get(itv, "A")
        decision = (
            assert_passes_gate_a(row, cfg)
            if gate == "A" else
            assert_passes_gate_b(row, cfg)
        )
        out_rows.append(decision.as_row())

    aug = summary.copy()
    aug = pd.concat(
        [aug.reset_index(drop=True), pd.DataFrame(out_rows)],
        axis=1,
    )
    return aug
