"""Entry-side gate chain for sim/live (P1-10/11/12).

包装 OOT 的批处理 gate 函数（接收 DataFrame）成 sim 单条 candidate 接口：

    chain = EntryGateChain(cfg)
    decision = chain.evaluate(candidate, state_provider, dt)
    if decision.passed:
        send_order(...)

被包装的 gate（按串联顺序）：
1. ``apply_trade_filter_gate``（bypass_signal_types）
2. HTF gate（portfolio_logic runtime）
3. RiskOrchestrator（threshold + sizing）

注意：本模块**不重复**这些 gate 的逻辑——直接调 OOT 函数，保证三层一致（roadmap §3.2）。
（2026-05-29：ma_cross_gate / regime_short_filter / trend_aware delta 已删除。）
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.config.symbol_cluster_config import infer_symbol_cluster
from cta.live.risk import RiskContext
from cta.model.oot.block_reasons import BR_HTF_MISSING
from cta.portfolio_logic.interval_gate import HtfGate
from cta.portfolio_logic.config import normalize_portfolio_interval
from cta.risk import RiskOrchestrator, SignalContext
from cta.risk.guards.score_distribution_drift_guard import ScoreDistributionDriftGuard
from cta.risk.monitors.score_distribution_drift import ScoreDistributionDriftMonitor

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class GateDecision:
    """单条 candidate 经 gate 链后的判定结果。"""
    passed: bool
    block_reason: str
    block_stage: str  # which gate stage blocked: "trade_filter" / "htf" / "risk_threshold" / "risk_sizing"
    adjusted_lots: int = 0
    effective_threshold: float = float("nan")


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


def _enrich_candidate_with_state(
    candidate: dict[str, Any],
    *,
    state_provider: Any | None,
    dt: pd.Timestamp | None,
) -> dict[str, Any]:
    """Backfill ma/regime/vol columns from state_provider when candidate lacks them."""
    row = dict(candidate)
    if state_provider is None:
        return row
    symbol = str(row.get("symbol", "")).strip().upper()
    if not symbol:
        return row
    ts = pd.Timestamp(dt if dt is not None else row.get("datetime", pd.Timestamp.now()))
    try:
        ma_val = int(state_provider.lookup_ma_alignment(symbol, ts))
        if "generic_ma_alignment" not in row:
            row["generic_ma_alignment"] = ma_val
        if "ma_alignment" not in row:
            row["ma_alignment"] = ma_val
    except Exception as exc:  # noqa: BLE001
        logger.debug("state_provider ma_alignment lookup failed: %s", exc)
    try:
        regime = str(state_provider.lookup_regime_label(symbol, ts)).strip().lower()
        if regime and "regime_label" not in row:
            row["regime_label"] = regime
    except Exception as exc:  # noqa: BLE001
        logger.debug("state_provider regime lookup failed: %s", exc)
    try:
        vol_rank = float(state_provider.lookup_realized_vol(symbol, ts))
        if "realized_vol_rank" not in row:
            row["realized_vol_rank"] = vol_rank
    except Exception as exc:  # noqa: BLE001
        logger.debug("state_provider realized_vol lookup failed: %s", exc)
    return row


class EntryGateChain:
    """sim/live 入场前 gate 链路 wrapper。"""

    def __init__(self, cfg: OotEvaluationConfig) -> None:
        self.cfg = cfg
        pl_cfg = getattr(cfg, "portfolio_logic", None)
        use_pl_runtime = bool(getattr(cfg, "use_portfolio_logic_runtime", False))
        use_pl_htf = bool(
            use_pl_runtime and pl_cfg is not None and bool(getattr(pl_cfg, "enable_htf_gate", False))
        )
        self._htf_gate: HtfGate | None = HtfGate(pl_cfg.interval_gate) if use_pl_htf else None
        self._risk_orchestrator: RiskOrchestrator | None = None
        self._score_drift_guard: ScoreDistributionDriftGuard | None = None
        self._score_drift_monitor: ScoreDistributionDriftMonitor | None = None
        risk_cfg = getattr(cfg, "risk_system", None)
        if risk_cfg is not None:
            try:
                self._risk_orchestrator = RiskOrchestrator.from_config(
                    risk_cfg,
                    static_overrides=dict(
                        getattr(cfg, "trade_filter_percentile_threshold_by_cluster_interval", {}) or {}
                    ),
                    global_default_threshold=float(
                        getattr(cfg, "trade_filter_percentile_threshold", 70.0)
                    ),
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("build risk orchestrator failed: %s; disabled", exc)
                self._risk_orchestrator = None
        if bool(getattr(cfg, "use_oot_score_distribution_guard", False)):
            try:
                guard_cfg = getattr(cfg, "oot_score_distribution_guard")
                self._score_drift_monitor = ScoreDistributionDriftMonitor(guard_cfg)
                self._score_drift_guard = ScoreDistributionDriftGuard(
                    guard_cfg,
                    monitor=self._score_drift_monitor,
                )
            except Exception as exc:  # noqa: BLE001
                logger.warning("build score_distribution_drift guard failed: %s; disabled", exc)
                self._score_drift_guard = None
                self._score_drift_monitor = None

    def evaluate(
        self,
        candidate: dict[str, Any],
        *,
        dt: pd.Timestamp | None = None,
        state_provider: Any | None = None,
        htf_state: dict[tuple[str, str], dict[str, Any]] | None = None,
        original_lots: int = 1,
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
        row = _enrich_candidate_with_state(candidate, state_provider=state_provider, dt=dt)
        signal_type = str(row.get("signal_type", "")).strip().lower()
        if bool(signal_type) and bool(self.cfg.is_signal_type_blacklisted(signal_type)):
            return GateDecision(
                passed=False,
                block_reason="blocked_signal_type_blacklist",
                block_stage="signal_type_blacklist",
                adjusted_lots=0,
            )
        current_time = pd.Timestamp(
            dt
            if dt is not None
            else row.get("datetime", pd.Timestamp.now())
        )
        try:
            lots = max(1, int(original_lots))
        except (TypeError, ValueError):
            lots = 1
        df = _candidate_to_df(row, dt=dt)
        n = len(df)
        gate_by_legacy = pd.Series([True] * n, index=df.index, dtype=bool)
        model_block_reason = pd.Series([""] * n, index=df.index, dtype=object)

        # ── Stage 0: score distribution drift monitor (sim/live C5) ───────
        if self._score_drift_guard is not None and self._score_drift_monitor is not None:
            score = row.get("trade_filter_prob_pctl", row.get("trade_filter_prob"))
            self._score_drift_monitor.observe(score, current_time)
            drift_decision = self._score_drift_guard.check(
                {"offset": "open"},
                RiskContext(
                    pos={},
                    daily_pnl=0.0,
                    capital=float(getattr(self.cfg, "initial_capital", 0.0)),
                    now=current_time,
                ),
            )
            if not drift_decision.allowed:
                return GateDecision(
                    passed=False,
                    block_reason=str(drift_decision.reason),
                    block_stage="score_drift",
                    adjusted_lots=0,
                )

        # ── Stage 1: trade_filter（含 bypass） ───────
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
                    adjusted_lots=0,
                )

        # ── Stage 4: HTF gate（portfolio_logic runtime） ─────────────────
        if self._htf_gate is not None:
            try:
                if "direction" not in df.columns and "side" in df.columns:
                    df = df.copy()
                    df["direction"] = df["side"]
                htf_filtered = self._htf_gate.filter(
                    df,
                    htf_state or {},
                    current_time=current_time,
                )
                if not bool(htf_filtered.get("htf_allowed", pd.Series([True])).iloc[0]):
                    reason = str(
                        htf_filtered.get("htf_block_reason", pd.Series([""])).iloc[0]
                    ).strip() or BR_HTF_MISSING
                    return GateDecision(
                        passed=False,
                        block_reason=reason,
                        block_stage="htf",
                        adjusted_lots=0,
                    )
            except Exception as exc:  # noqa: BLE001
                logger.warning("htf_gate failed: %s; pass-through", exc)

        # ── Stage 5: RiskOrchestrator (threshold + sizing) ──────────────
        if self._risk_orchestrator is not None:
            row_for_risk = dict(row)
            symbol = str(row_for_risk.get("symbol", "")).strip().upper()
            cluster = str(row_for_risk.get("cluster", "")).strip().lower()
            if not cluster and symbol:
                cluster = str(infer_symbol_cluster(symbol) or "").strip().lower()
            if cluster:
                row_for_risk["cluster"] = cluster
                row_for_risk["cluster_name"] = cluster
            row_for_risk["interval"] = normalize_portfolio_interval(row_for_risk.get("interval", ""))
            portfolio_snapshot: dict[str, Any] = {}
            if state_provider is not None and hasattr(state_provider, "portfolio_snapshot"):
                try:
                    snap = state_provider.portfolio_snapshot(current_time)  # type: ignore[attr-defined]
                    portfolio_snapshot = dict(snap or {})
                except Exception as exc:  # noqa: BLE001
                    logger.debug("state_provider portfolio_snapshot failed: %s", exc)
            decision = self._risk_orchestrator.evaluate(
                SignalContext(
                    candidate=row_for_risk,
                    portfolio=portfolio_snapshot,
                    bar_dt=current_time,
                ),
                original_lots=lots,
            )
            if not decision.passed:
                return GateDecision(
                    passed=False,
                    block_reason=str(decision.block_reason),
                    block_stage=f"risk_{decision.block_stage}",
                    adjusted_lots=0,
                    effective_threshold=float(decision.effective_threshold),
                )
            lots = int(decision.adjusted_lots)
            return GateDecision(
                passed=True,
                block_reason="",
                block_stage="",
                adjusted_lots=lots,
                effective_threshold=float(decision.effective_threshold),
            )

        return GateDecision(passed=True, block_reason="", block_stage="", adjusted_lots=lots)


__all__ = ["EntryGateChain", "GateDecision"]
