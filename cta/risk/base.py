"""Abstract interfaces for the CTA risk subsystem.

只放接口与轻量 dataclass；具体实现在 threshold/ sizing/ state/ guards/ 各子模块。

每个 candidate 走完 RiskOrchestrator.evaluate(ctx, original_lots) 后得到 AdjustedDecision：
  - passed=True：candidate 允许下单，按 adjusted_lots 报
  - passed=False：被 threshold（分数不够）或 sizing（缩到 0）拦截；block_reason 记原因

debug dict 保留中间过程（原始 lots / 各 scaler 输出 / threshold 链路），方便复盘归因。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Protocol

import pandas as pd


@dataclass(frozen=True)
class SignalContext:
    """单条 candidate 通过 risk 三层时携带的上下文。

    Attributes
    ----------
    candidate
        候选 dict。最少必含 ``cluster`` / ``symbol`` / ``interval`` / ``signal_type``；
        若需要触发分数阈值，还需 ``trade_filter_prob`` 与/或 ``trade_filter_prob_pctl``。
    portfolio
        投资组合层快照。至少建议含 ``equity`` / ``cumulative_dd_pct`` / ``weekly_dd_pct``。
        OOT 批处理时可暂时只传 ``equity``。
    bar_dt
        当前 bar 时间戳（Shanghai naive，与 OOT/sim/live 统一）。
    """

    candidate: dict
    portfolio: dict
    bar_dt: pd.Timestamp


@dataclass(frozen=True)
class AdjustedDecision:
    """RiskOrchestrator.evaluate 输出。"""

    passed: bool
    block_reason: str       # 拦截原因，例如 "below_threshold" / "lots_zero:bucket"
    block_stage: str        # "threshold" / "sizing" / "guard" / "" (passed)
    adjusted_lots: int      # original lots × Π(scalers)，floor=0
    effective_threshold: float  # threshold 链最终输出（pctl 或 raw 由 ctx 决定）
    debug: dict = field(default_factory=dict)


class ThresholdAdjuster(Protocol):
    """阈值调整插件接口。

    实现类应当：
    - 无状态或只读外部 manifest / config；
    - resolve 接收上一阶段的 ``base_threshold``，返回新阈值（单调非降建议但不强制）；
    - 输入异常或缺数据时 fail-open 返回 ``base_threshold``。
    """

    def resolve(self, ctx: SignalContext, base_threshold: float) -> float: ...


class PositionScaler(Protocol):
    """仓位缩放插件接口。

    实现类应当：
    - 输入 ``lots_so_far``（上一个 scaler 输出），返回 (new_lots, reason)；
    - reason 字符串用于在 debug 字典里追踪是谁缩了仓；
    - new_lots <= 0 时 orchestrator 会立刻 BLOCK，不再走后续 scaler。
    """

    def scale(self, ctx: SignalContext, lots_so_far: int) -> tuple[int, str]: ...


def extract_score(ctx: SignalContext, *, prefer_pctl: bool = True) -> float | None:
    """从 candidate 抽出当前用于阈值比较的分数。

    优先级：
        prefer_pctl=True：trade_filter_prob_pctl → trade_filter_prob × 100 → None
        prefer_pctl=False：trade_filter_prob → trade_filter_prob_pctl / 100 → None

    返回 None 表示 candidate 没有可比较的分数（caller 应 fail-open 放行）。
    """
    cand = ctx.candidate or {}
    if prefer_pctl:
        pctl = cand.get("trade_filter_prob_pctl")
        if pctl is not None:
            try:
                v = float(pctl)
                # 若值在 [0, 1] 视为 fraction → 乘 100
                return v * 100.0 if v <= 1.0 else v
            except (TypeError, ValueError):
                pass
        prob = cand.get("trade_filter_prob")
        if prob is not None:
            try:
                return float(prob) * 100.0
            except (TypeError, ValueError):
                return None
        return None
    prob = cand.get("trade_filter_prob")
    if prob is not None:
        try:
            return float(prob)
        except (TypeError, ValueError):
            pass
    pctl = cand.get("trade_filter_prob_pctl")
    if pctl is not None:
        try:
            v = float(pctl)
            return v / 100.0 if v > 1.0 else v
        except (TypeError, ValueError):
            return None
    return None


def normalize_cluster(cluster: Any) -> str:
    return str(cluster or "").strip().lower()


def normalize_symbol(symbol: Any) -> str:
    return str(symbol or "").strip().upper()


def normalize_interval(interval: Any) -> str:
    """与 cta.portfolio_logic.config.normalize_portfolio_interval 等价的轻量版。"""
    raw = str(interval or "").strip().lower()
    aliases = {
        "1d": "day", "d": "day", "daily": "day",
        "minute60": "60min", "60m": "60min", "1h": "60min", "hour": "60min",
        "minute30": "30min", "30m": "30min",
        "minute15": "15min", "15m": "15min",
        "minute5": "5min", "5m": "5min",
        "minute": "min", "1m": "min", "1min": "min",
    }
    return aliases.get(raw, raw)


__all__ = [
    "AdjustedDecision",
    "PositionScaler",
    "SignalContext",
    "ThresholdAdjuster",
    "extract_score",
    "normalize_cluster",
    "normalize_interval",
    "normalize_symbol",
]
