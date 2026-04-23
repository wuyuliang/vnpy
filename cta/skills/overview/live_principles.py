"""§04 实盘原则 / Live Trading Principles —— 熔断 + 对账.

对应 cta/cta_skills/00_overview_methodology/04_live_trading_principles.md §6。

功能
----
1. LiveGateState 数据类：描述 "当前策略处于灰度哪个阶段、日内回撤多大、连亏
   几笔、信号一致率多少"
2. check_circuit(state, cfg): 根据配置判断是否应触发熔断，并给出建议 Action
3. reconcile_positions(strategy, broker): 返回持仓不一致的 vt_symbol 列表

三种熔断原因
------------
- daily_dd         : 当日净值回撤 ≥ cfg.daily_dd_hard （5% 默认）
- consecutive_loss : 连续亏损次数 ≥ cfg.consecutive_loss_cap （10 默认）
- signal_mismatch  : 实盘 vs 回测信号一致率 < cfg.signal_match_min （0.95 默认）

设计要点
--------
- 函数 pure：不碰 IO、不碰时间；由调用方（alerter / runner）决定通知渠道
- 每条熔断返回 CircuitAction(reason, severity, remediation)
- 多条同时触发时按 severity 排序返回列表
"""
from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Dict, Iterable, List, Mapping, Optional

logger = logging.getLogger(__name__)

# 默认熔断阈值（可被调用方覆盖）
DEFAULT_CIRCUIT_CFG: Dict[str, float] = {
    "daily_dd_warn":     0.03,   # 日内回撤 3% 预警
    "daily_dd_hard":     0.05,   # 日内回撤 5% 熔断
    "consecutive_loss_warn":  7,
    "consecutive_loss_cap":  10,
    "signal_match_warn":  0.98,
    "signal_match_min":   0.95,
}


# ---------------------------------------------------------------------------
# 数据类
# ---------------------------------------------------------------------------
@dataclass
class LiveGateState:
    """单一策略当日的实盘状态快照。"""
    stage: int = 1                              # 1..6 对应 md §5 六阶段
    trade_days_in_stage: int = 0
    consecutive_losses: int = 0
    signal_match_rate: float = 1.0              # [0, 1]
    daily_drawdown: float = 0.0                 # 正数，如 0.04 表示 -4%

    def __post_init__(self) -> None:
        if not 1 <= self.stage <= 6:
            raise ValueError(f"stage 必须 1..6，got {self.stage}")
        if not 0.0 <= self.signal_match_rate <= 1.0 + 1e-9:
            raise ValueError(
                f"signal_match_rate 必须 ∈ [0,1]，got {self.signal_match_rate}"
            )
        if self.daily_drawdown < 0:
            raise ValueError(
                f"daily_drawdown 应为非负数（表示亏损比例），got {self.daily_drawdown}"
            )
        if self.consecutive_losses < 0:
            raise ValueError(
                f"consecutive_losses 必须 ≥ 0，got {self.consecutive_losses}"
            )


@dataclass
class CircuitAction:
    """熔断建议。severity: 'warn' / 'halt' / 'stop'."""
    reason: str                                 # 'daily_dd' / 'consecutive_loss' / 'signal_mismatch'
    severity: str                               # 'warn' | 'halt' | 'stop'
    remediation: str                            # 文字建议，供 alerter 展示
    detail: Dict[str, float] = field(default_factory=dict)

    def as_row(self) -> Dict[str, str]:
        return {
            "reason": self.reason,
            "severity": self.severity,
            "remediation": self.remediation,
        }


_SEVERITY_ORDER = {"stop": 0, "halt": 1, "warn": 2}


# ---------------------------------------------------------------------------
# 熔断
# ---------------------------------------------------------------------------
def check_circuit(
    state: LiveGateState,
    cfg: Mapping[str, float] | None = None,
) -> List[CircuitAction]:
    """
    返回触发的熔断列表（按 severity 升序：stop > halt > warn）。
    列表为空代表一切正常。

    Parameters
    ----------
    state : 当前策略状态
    cfg   : 覆盖默认阈值。支持的 key 见 DEFAULT_CIRCUIT_CFG
    """
    c = dict(DEFAULT_CIRCUIT_CFG)
    if cfg:
        c.update(cfg)

    actions: List[CircuitAction] = []

    # 日回撤
    if state.daily_drawdown >= c["daily_dd_hard"]:
        actions.append(CircuitAction(
            reason="daily_dd",
            severity="halt",
            remediation=f"当日回撤 {state.daily_drawdown:.2%} ≥ "
                        f"{c['daily_dd_hard']:.2%}，停止开新仓，只管理已有持仓",
            detail={"daily_drawdown": state.daily_drawdown,
                    "threshold": c["daily_dd_hard"]},
        ))
    elif state.daily_drawdown >= c["daily_dd_warn"]:
        actions.append(CircuitAction(
            reason="daily_dd",
            severity="warn",
            remediation=f"当日回撤 {state.daily_drawdown:.2%} 达到预警线 "
                        f"{c['daily_dd_warn']:.2%}",
            detail={"daily_drawdown": state.daily_drawdown,
                    "threshold": c["daily_dd_warn"]},
        ))

    # 连亏
    if state.consecutive_losses >= c["consecutive_loss_cap"]:
        actions.append(CircuitAction(
            reason="consecutive_loss",
            severity="stop",
            remediation=f"连亏 {state.consecutive_losses} 笔 ≥ "
                        f"{int(c['consecutive_loss_cap'])}，策略下线 review",
            detail={"consecutive_losses": state.consecutive_losses,
                    "threshold": c["consecutive_loss_cap"]},
        ))
    elif state.consecutive_losses >= c["consecutive_loss_warn"]:
        actions.append(CircuitAction(
            reason="consecutive_loss",
            severity="warn",
            remediation=f"连亏 {state.consecutive_losses} 笔达到预警线 "
                        f"{int(c['consecutive_loss_warn'])}",
            detail={"consecutive_losses": state.consecutive_losses,
                    "threshold": c["consecutive_loss_warn"]},
        ))

    # 信号一致率
    if state.signal_match_rate < c["signal_match_min"]:
        actions.append(CircuitAction(
            reason="signal_mismatch",
            severity="stop",
            remediation=f"信号一致率 {state.signal_match_rate:.2%} < "
                        f"{c['signal_match_min']:.2%}，立即停策略并对齐代码",
            detail={"signal_match_rate": state.signal_match_rate,
                    "threshold": c["signal_match_min"]},
        ))
    elif state.signal_match_rate < c["signal_match_warn"]:
        actions.append(CircuitAction(
            reason="signal_mismatch",
            severity="warn",
            remediation=f"信号一致率 {state.signal_match_rate:.2%} 低于预警线 "
                        f"{c['signal_match_warn']:.2%}",
            detail={"signal_match_rate": state.signal_match_rate,
                    "threshold": c["signal_match_warn"]},
        ))

    # 按 severity 严格排序（stop 最严重）
    actions.sort(key=lambda a: _SEVERITY_ORDER.get(a.severity, 99))
    return actions


# ---------------------------------------------------------------------------
# 对账
# ---------------------------------------------------------------------------
def reconcile_positions(
    strategy_positions: Mapping[str, int],
    broker_positions: Mapping[str, int],
    ignore_zero: bool = True,
) -> List[str]:
    """
    返回不一致的 vt_symbol 列表。

    Parameters
    ----------
    strategy_positions : {vt_symbol: qty}，策略内部认为的持仓
    broker_positions   : {vt_symbol: qty}，券商回传的实盘持仓
    ignore_zero        : True 时忽略双方都是 0 的键
    """
    keys = set(strategy_positions) | set(broker_positions)
    diffs: List[str] = []
    for k in sorted(keys):
        s = int(strategy_positions.get(k, 0))
        b = int(broker_positions.get(k, 0))
        if ignore_zero and s == 0 and b == 0:
            continue
        if s != b:
            diffs.append(k)
    return diffs
