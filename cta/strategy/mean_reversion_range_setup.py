"""Range mean-reversion setup generation.

Strategy hypothesis:
- In an explicit range regime, Bollinger-band extremes with weak ADX often
  revert toward the rolling mean.
- Entries are counter-trend market candidates on the next bar, with the mean
  as target and a band-plus-ATR stop as the initial risk reference.
- The setup is default-off and must be enabled by ``cluster|interval`` before
  it can alter candidate generation.
"""
from __future__ import annotations

from typing import Any

import numpy as np
import pandas as pd

from cta.config.mean_reversion_setup_config import MeanReversionSetupConfig
from cta.feature.mean_reversion import compute_mean_reversion_features


def _as_float(value: Any) -> float:
    num = pd.to_numeric(pd.Series([value]), errors="coerce").iloc[0]
    return float(num) if pd.notna(num) else float("nan")


def _infer_regime(row: pd.Series) -> str:
    label = str(row.get("regime_label", "") or "").strip().lower()
    if label:
        return label
    trend_dir = _as_float(row.get("trend_dir", np.nan))
    if np.isfinite(trend_dir):
        if trend_dir > 0:
            return "trend_up"
        if trend_dir < 0:
            return "trend_down"
    return "range"


class MeanReversionRangeSetupGenerator:
    """Generate counter-trend candidates from range extremes."""

    def __init__(self, cfg: MeanReversionSetupConfig) -> None:
        self.cfg = cfg

    def _with_features(self, bars: pd.DataFrame) -> pd.DataFrame:
        required = {"mr_sma", "mr_zscore", "mr_rsi", "mr_adx", "mr_bb_upper", "mr_bb_lower"}
        if required.issubset(bars.columns):
            return bars.copy()
        out = bars.copy()
        feat = compute_mean_reversion_features(
            out["close"],
            out["high"],
            out["low"],
            bb_window=self.cfg.bb_window,
            bb_std_mult=self.cfg.bb_std_mult,
            rsi_window=self.cfg.rsi_window,
            adx_window=self.cfg.adx_window,
        )
        for col in feat.columns:
            out[col] = feat[col]
        return out

    def candidate_from_row(
        self,
        row: pd.Series,
        *,
        cluster: str | None,
        interval: str,
    ) -> dict[str, Any] | None:
        """Build one candidate payload from a pre-featured signal bar."""
        if not self.cfg.is_enabled(cluster, interval):
            return None
        regime = _infer_regime(row)
        if bool(self.cfg.require_range_regime) and regime != "range":
            return None
        zscore = _as_float(row.get("mr_zscore", np.nan))
        rsi = _as_float(row.get("mr_rsi", np.nan))
        adx = _as_float(row.get("mr_adx", np.nan))
        close = _as_float(row.get("close", np.nan))
        sma = _as_float(row.get("mr_sma", np.nan))
        atr = _as_float(row.get("atr14", np.nan))
        if not all(np.isfinite(v) for v in (zscore, rsi, adx, close, sma)):
            return None
        if adx > float(self.cfg.adx_max):
            return None

        side = ""
        stop = float("nan")
        if zscore >= float(self.cfg.bb_std_mult) and rsi >= float(self.cfg.rsi_upper):
            side = "short"
            upper = _as_float(row.get("mr_bb_upper", np.nan))
            stop = upper + float(self.cfg.target_atr_mult_stop) * atr if np.isfinite(upper) and np.isfinite(atr) else upper
        elif zscore <= -float(self.cfg.bb_std_mult) and rsi <= float(self.cfg.rsi_lower):
            side = "long"
            lower = _as_float(row.get("mr_bb_lower", np.nan))
            stop = lower - float(self.cfg.target_atr_mult_stop) * atr if np.isfinite(lower) and np.isfinite(atr) else lower
        if side not in {"long", "short"} or not np.isfinite(stop):
            return None
        return {
            "side": side,
            "signal_type": "mean_reversion_range",
            "order_type": "market",
            "entry_price": close,
            "target_price": sma,
            "stop_price": float(stop),
            "regime_label_at_signal": regime,
            "mr_zscore": zscore,
            "mr_rsi": rsi,
            "mr_adx": adx,
            "mr_signal_strength": _as_float(row.get("mr_signal_strength", np.nan)),
        }

    def generate(
        self,
        bars: pd.DataFrame,
        *,
        cluster: str | None,
        interval: str,
    ) -> pd.DataFrame:
        """Return candidate rows for all eligible signal bars."""
        if bars.empty or not self.cfg.is_enabled(cluster, interval):
            return pd.DataFrame()
        frame = self._with_features(bars)
        rows: list[dict[str, Any]] = []
        dt = pd.to_datetime(frame.get("datetime", pd.Series(pd.NaT, index=frame.index)), errors="coerce")
        for idx, row in frame.iterrows():
            candidate = self.candidate_from_row(row, cluster=cluster, interval=interval)
            if candidate is None:
                continue
            candidate["signal_i"] = int(idx) if isinstance(idx, (int, np.integer)) else len(rows)
            candidate["signal_datetime"] = dt.loc[idx] if idx in dt.index else pd.NaT
            rows.append(candidate)
        return pd.DataFrame(rows)


__all__ = ["MeanReversionRangeSetupGenerator"]
