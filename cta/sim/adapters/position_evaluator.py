"""Position-side exit evaluator for sim/live (P1-8/9/13).

按 design doc §3.4 串联：
  1. hard_stop（防爆仓） / intrabar_stop_loss_pct_by_cluster_interval（P1-13）
  2. trailing_take_profit（锁盈，P1-8）
  3. trailing_stop（防回撤，已有）
  4. horizon_exit（时间，按 profit_aware_horizon 调整，P1-9）
  5. mfe_mae_realtime_exit

本模块只串联前 4 个核心，trailing_stop / mfe_mae 留接口给后续 wire。
"""
from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

import numpy as np
import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.config.profit_aware_horizon_config import ProfitAwareHorizonConfig
from cta.config.trailing_take_profit_config import TrailingTakeProfitConfig
from cta.portfolio_logic.position_trend_state import PositionTrendState, compute_trend_score
from cta.portfolio_logic.profit_aware_horizon import resolve_max_holding_bars
from cta.portfolio_logic.trailing_take_profit import TrailingTakeProfitEvaluator
from cta.sim.adapters.state_provider import StateProvider

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class PositionExitDecision:
    """exit 决策（含归因 reason）。"""
    should_exit: bool
    exit_reason: str
    exit_price: float
    extra: dict[str, Any]


def _resolve_intrabar_stop_pct(
    *,
    cluster: str,
    interval: str,
    cfg: OotEvaluationConfig | None,
) -> float:
    """P1-13：按 cluster|interval 取 intrabar_stop_loss_pct；缺则回退 global。"""
    if cfg is None:
        return 0.01
    overrides = dict(getattr(cfg, "intrabar_stop_loss_pct_by_cluster_interval", {}) or {})
    key = f"{str(cluster or '').strip().lower()}|{str(interval or '').strip().lower()}"
    if key in overrides:
        return float(overrides[key])
    return float(getattr(cfg, "intrabar_stop_loss_pct", 0.01))


class PositionEvaluator:
    """sim/live 持仓阶段评估器，每 bar 调一次。"""

    def __init__(
        self,
        *,
        oot_cfg: OotEvaluationConfig | None = None,
        trailing_tp_cfg: TrailingTakeProfitConfig | None = None,
        profit_horizon_cfg: ProfitAwareHorizonConfig | None = None,
        state_provider: StateProvider | None = None,
    ) -> None:
        self.oot_cfg = oot_cfg
        self.trailing_tp_cfg = trailing_tp_cfg
        self.profit_horizon_cfg = profit_horizon_cfg
        self.state_provider = state_provider
        self._tp_evaluator = (
            TrailingTakeProfitEvaluator(trailing_tp_cfg)
            if trailing_tp_cfg is not None else None
        )

    def _build_state(
        self,
        position: dict[str, Any],
        bar: dict[str, Any] | pd.Series,
    ) -> PositionTrendState:
        """从 position + bar 构造 PositionTrendState，调 state_provider 取动态特征。"""
        row = dict(bar)
        symbol = str(position.get("symbol", "")).upper()
        side = str(position.get("side", "long")).strip().lower()
        entry_price = float(position.get("entry_price", float("nan")))
        current_price = float(row.get("close", row.get("price", float("nan"))))
        sign = -1.0 if side == "short" else 1.0
        if np.isfinite(entry_price) and entry_price > 0 and np.isfinite(current_price):
            pnl_pct = (current_price - entry_price) / entry_price * sign
        else:
            pnl_pct = float("nan")
        dt = pd.Timestamp(row.get("datetime", pd.Timestamp.now()))
        bars_held = int(position.get("bars_held", 0))

        if self.state_provider is not None:
            ma_alignment = int(self.state_provider.lookup_ma_alignment(symbol, dt))
            regime_label = str(self.state_provider.lookup_regime_label(symbol, dt))
            realized_vol = float(self.state_provider.lookup_realized_vol(symbol, dt))
        else:
            ma_alignment, regime_label, realized_vol = 0, "", 0.02

        trend_score = compute_trend_score(
            ma_alignment=ma_alignment,
            regime_label=regime_label,
            current_pnl_pct=pnl_pct if np.isfinite(pnl_pct) else 0.0,
        )
        return PositionTrendState(
            symbol=symbol,
            side=side,
            entry_price=entry_price,
            current_price=current_price,
            current_pnl_pct=pnl_pct,
            bars_held=bars_held,
            ma_alignment=ma_alignment,
            regime_label=regime_label,
            realized_vol_20d=realized_vol,
            trend_score=trend_score,
        )

    def evaluate(
        self,
        position: dict[str, Any],
        bar: dict[str, Any] | pd.Series,
        *,
        highwater_price: float,
        cluster: str,
        interval: str,
    ) -> PositionExitDecision:
        """每 bar 评估持仓是否应离场。"""
        state = self._build_state(position, bar)

        # ── 1. hard_stop（含 intrabar_stop_loss_pct_by_cluster_interval，P1-13） ──
        stop_pct = _resolve_intrabar_stop_pct(
            cluster=cluster, interval=interval, cfg=self.oot_cfg,
        )
        if state.is_valid() and stop_pct > 0:
            sign = -1.0 if state.side == "short" else 1.0
            stop_price = float(state.entry_price) * (1.0 - sign * stop_pct)
            if state.side == "long" and state.current_price <= stop_price:
                return PositionExitDecision(
                    should_exit=True,
                    exit_reason="hard_stop",
                    exit_price=float(state.current_price),
                    extra={"stop_price": stop_price, "stop_pct": stop_pct},
                )
            if state.side == "short" and state.current_price >= stop_price:
                return PositionExitDecision(
                    should_exit=True,
                    exit_reason="hard_stop",
                    exit_price=float(state.current_price),
                    extra={"stop_price": stop_price, "stop_pct": stop_pct},
                )

        # ── 2. trailing_take_profit（P1-8） ─────────────────────────────
        if self._tp_evaluator is not None:
            tp_decision = self._tp_evaluator.update(
                state, highwater_price=float(highwater_price),
                cluster=cluster, interval=interval,
            )
            if tp_decision is not None:
                return PositionExitDecision(
                    should_exit=True,
                    exit_reason=tp_decision.reason,
                    exit_price=float(tp_decision.price),
                    extra={
                        "trigger_price": tp_decision.trigger_price,
                        "trail_distance_pct": tp_decision.trail_distance_pct,
                        "highwater_price": tp_decision.highwater_price,
                    },
                )

        # ── 3. profit-aware horizon_exit（P1-9） ────────────────────────
        if self.profit_horizon_cfg is not None:
            max_bars = resolve_max_holding_bars(
                state, interval=interval,
                cfg=self.profit_horizon_cfg, cluster=cluster,
            )
            if state.bars_held >= max_bars:
                return PositionExitDecision(
                    should_exit=True,
                    exit_reason="horizon_exit",
                    exit_price=float(state.current_price),
                    extra={"max_holding_bars": max_bars, "bars_held": state.bars_held},
                )

        return PositionExitDecision(
            should_exit=False, exit_reason="", exit_price=float("nan"), extra={},
        )


__all__ = ["PositionEvaluator", "PositionExitDecision"]
