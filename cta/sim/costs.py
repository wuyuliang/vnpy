"""Sim-side cost helpers reusing OOT cost manifest logic (P0Δ-1)."""
from __future__ import annotations

import pandas as pd

from cta.config.model_oot_eval_config import OotEvaluationConfig
from cta.model.oot.pipeline_oot_evaluation_inputs import resolve_per_row_cost_pct


def resolve_trade_cost_pct(
    *,
    symbol: str,
    interval: str,
    cfg: OotEvaluationConfig,
) -> float:
    """Resolve per-trade cost pct for one (symbol, interval) cell.

    Uses the same implementation as OOT evaluator to avoid sim/OOT config drift.
    """
    row = pd.DataFrame([{"symbol": str(symbol).upper(), "interval": str(interval)}])
    return float(resolve_per_row_cost_pct(row, cfg)[0])


__all__ = ["resolve_trade_cost_pct"]

