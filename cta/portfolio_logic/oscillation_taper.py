"""Range-bound position taper decisions shared by OOT, sim and live."""
from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping

import numpy as np
import pandas as pd

from cta.portfolio_logic.config import OscillationTaperConfig


@dataclass(frozen=True)
class TaperDecision:
    """One partial-exit decision for a position snapshot."""

    should_taper: bool
    target_ratio: float = 1.0
    exit_reason: str = ""
    upper_boundary: float = float("nan")
    lower_boundary: float = float("nan")


def add_oscillation_boundaries(bars: pd.DataFrame, cfg: OscillationTaperConfig) -> pd.DataFrame:
    """Attach boundary columns used by taper evaluation."""
    out = bars.copy()
    high = pd.to_numeric(out["high"], errors="coerce")
    low = pd.to_numeric(out["low"], errors="coerce")
    close = pd.to_numeric(out["close"], errors="coerce")
    don_hi = high.rolling(int(cfg.donchian_window), min_periods=1).max()
    don_lo = low.rolling(int(cfg.donchian_window), min_periods=1).min()
    sma = close.rolling(int(cfg.bb_window), min_periods=1).mean()
    std = close.rolling(int(cfg.bb_window), min_periods=1).std(ddof=0).fillna(0.0)
    bb_hi = sma + float(cfg.bb_std_mult) * std
    bb_lo = sma - float(cfg.bb_std_mult) * std
    if cfg.boundary_method == "donchian":
        upper, lower = don_hi, don_lo
    elif cfg.boundary_method == "bollinger":
        upper, lower = bb_hi, bb_lo
    else:
        upper = pd.concat([don_hi, bb_hi], axis=1).max(axis=1)
        lower = pd.concat([don_lo, bb_lo], axis=1).min(axis=1)
    out["oscillation_upper_boundary"] = upper
    out["oscillation_lower_boundary"] = lower
    return out


def _num(bar: Mapping[str, Any], name: str) -> float:
    value = pd.to_numeric(pd.Series([bar.get(name, np.nan)]), errors="coerce").iloc[0]
    return float(value) if pd.notna(value) else float("nan")


def _linear_ratio(distance: float) -> float:
    if distance >= 0.50:
        return 1.0
    if distance >= 0.20:
        return 0.60 + (distance - 0.20) * (0.40 / 0.30)
    if distance > 0.0:
        return 0.20 + distance * 2.0
    return 0.0


def _stepwise_ratio(distance: float) -> float:
    if distance >= 0.50:
        return 1.0
    if distance >= 0.30:
        return 0.75
    if distance >= 0.15:
        return 0.50
    if distance >= 0.05:
        return 0.25
    return 0.0


class OscillationUpperBandTaper:
    """Evaluate range-bound taper ratios near oscillation boundaries."""

    def __init__(self, cfg: OscillationTaperConfig) -> None:
        self.cfg = cfg

    def evaluate(
        self,
        *,
        side: str,
        entry_price: float,
        bar: Mapping[str, Any] | pd.Series,
        regime_label: str | None,
        cluster: str | None,
        interval: str,
    ) -> TaperDecision:
        """Return a partial exit decision from a position/bar snapshot."""
        if not self.cfg.is_enabled(cluster, interval):
            return TaperDecision(False)
        if str(regime_label or "").strip().lower() not in {str(x).lower() for x in self.cfg.taper_regimes}:
            return TaperDecision(False)
        payload = dict(bar)
        close = _num(payload, "close")
        upper = _num(payload, "oscillation_upper_boundary")
        lower = _num(payload, "oscillation_lower_boundary")
        entry = float(entry_price)
        if not all(np.isfinite(v) for v in (close, upper, lower, entry)) or upper <= lower or entry <= 0.0:
            return TaperDecision(False)

        side_l = str(side).strip().lower()
        profit_pct = (close - entry) / entry if side_l == "long" else (entry - close) / entry
        if bool(self.cfg.require_profit_to_taper) and profit_pct < float(self.cfg.min_profit_pct_to_taper):
            return TaperDecision(False)

        span = upper - lower
        if side_l == "long":
            distance = (upper - close) / span
            trigger = float(self.cfg.upper_taper_trigger)
        elif side_l == "short":
            distance = (close - lower) / span
            trigger = float(self.cfg.lower_taper_trigger)
        else:
            return TaperDecision(False)
        if distance > trigger:
            return TaperDecision(False)
        target = _linear_ratio(distance) if self.cfg.taper_curve == "linear" else _stepwise_ratio(distance)
        return TaperDecision(
            True,
            target_ratio=float(np.clip(target, 0.0, 1.0)),
            exit_reason="oscillation_upper_band_taper",
            upper_boundary=float(upper),
            lower_boundary=float(lower),
        )


__all__ = [
    "TaperDecision",
    "OscillationUpperBandTaper",
    "add_oscillation_boundaries",
]
