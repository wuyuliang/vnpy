"""Entry-side gate chain for sim/live (P1-10/11/12).

包装 OOT 的批处理 gate 函数（接收 DataFrame）成 sim 单条 candidate 接口：

    chain = EntryGateChain(cfg)
    decision = chain.evaluate(candidate, state_provider, dt)
    if decision.passed:
        send_order(...)

被包装的 gate（按串联顺序）：
1. ``apply_trade_filter_gate``（trend_aware delta + bypass_signal_types）
2. ``apply_ma_cross_gate``（多头排列禁 short / 空头排列禁 long）
3. ``apply_regime_short_filter``（trend_up regime 禁 short）

注意：本模块**不重复**这些 gate 的逻辑——直接调 OOT 函数，保证三层一致（roadmap §3.2）。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.oot_gates import apply_ma_cross_gate, apply_regime_short_filter

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateDecision:
    """单条 candidate 经 gate 链后的判定结果。"""
    passed: bool
    block_reason: str
    block_stage: str  # which gate stage blocked: "trade_filter" / "ma_cross" / "regime_short"


def _candidate_to_df(candidate: dict[str, Any], dt: pd.Timestamp | None = None) -> pd.DataFrame:
    """单条 candidate dict 转 1 行 DataFrame，给 OOT 批处理 gate 用。"""
    row = dict(candidate)
    if dt is not None and "datetime" not in row:
        row["datetime"] = pd.Timestamp(dt)
    return pd.DataFrame([row])


def _apply_trade_filter_inline(
    df: pd.DataFrame,
    *,
    cfg: OotEvaluationConfig,
    gate_by_legacy: pd.Series,
    model_block_reason: pd.Series,
) -> tuple[pd.DataFrame, pd.Series, pd.Series]:
    """Inline 调用 ``apply_trade_filter_gate``（lazy import 避免循环）。"""
    from cta.model.oot.oot_trade_filter_gate import apply_trade_filter_gate
    return apply_trade_filter_gate(
        df, cfg=cfg, gate_by_legacy=gate_by_legacy, model_block_reason=model_block_reason,
    )


class EntryGateChain:
    """sim/live 入场前 gate 链路 wrapper。"""

    def __init__(self, cfg: OotEvaluationConfig) -> None:
        self.cfg = cfg

    def evaluate(
        self,
        candidate: dict[str, Any],
        *,
        dt: pd.Timestamp | None = None,
    ) -> GateDecision:
        """评估单条 candidate 是否通过全部入场 gate。

        Args:
            candidate: 候选 dict，至少含 symbol, side, signal_type；可选含
                trade_filter_prob / trade_filter_prob_pctl / generic_ma_alignment /
                regime_label / cluster / interval。
            dt: 候选 timestamp（用于日志归因）。

        Returns:
            GateDecision(passed, block_reason, block_stage)
        """
        df = _candidate_to_df(candidate, dt=dt)
        n = len(df)
        gate_by_legacy = pd.Series([True] * n, index=df.index, dtype=bool)
        model_block_reason = pd.Series([""] * n, index=df.index, dtype=object)

        # ── Stage 1: trade_filter（含 trend_aware delta + bypass） ───────
        if bool(getattr(self.cfg, "use_trade_filter_gate", False)):
            try:
                df, gate_by_legacy, model_block_reason = _apply_trade_filter_inline(
                    df, cfg=self.cfg,
                    gate_by_legacy=gate_by_legacy, model_block_reason=model_block_reason,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("trade_filter gate failed: %s; pass-through", exc)
            if not bool(gate_by_legacy.iloc[0]):
                return GateDecision(
                    passed=False,
                    block_reason=str(model_block_reason.iloc[0]),
                    block_stage="trade_filter",
                )

        # ── Stage 2: ma_cross_gate ──────────────────────────────────────
        if bool(getattr(self.cfg, "use_ma_cross_gate", False)):
            try:
                df, gate_by_legacy, model_block_reason = apply_ma_cross_gate(
                    df, cfg=self.cfg,
                    gate_by_legacy=gate_by_legacy, model_block_reason=model_block_reason,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("ma_cross_gate failed: %s; pass-through", exc)
            if not bool(gate_by_legacy.iloc[0]):
                return GateDecision(
                    passed=False,
                    block_reason=str(model_block_reason.iloc[0]),
                    block_stage="ma_cross",
                )

        # ── Stage 3: regime_short_filter ────────────────────────────────
        if bool(getattr(self.cfg, "use_regime_short_filter", False)):
            try:
                df, gate_by_legacy, model_block_reason = apply_regime_short_filter(
                    df, cfg=self.cfg,
                    gate_by_legacy=gate_by_legacy, model_block_reason=model_block_reason,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("regime_short_filter failed: %s; pass-through", exc)
            if not bool(gate_by_legacy.iloc[0]):
                return GateDecision(
                    passed=False,
                    block_reason=str(model_block_reason.iloc[0]),
                    block_stage="regime_short",
                )

        return GateDecision(passed=True, block_reason="", block_stage="")


__all__ = ["EntryGateChain", "GateDecision"]
