"""Score percentile calibration for cross-cluster comparability."""
from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import joblib
import numpy as np
import pandas as pd

from cta.portfolio_logic.config import normalize_portfolio_interval

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class CalibrationStats:
    """Calibration distribution for one (cluster, interval, model_kind)."""

    cluster: str
    interval: str
    model_kind: str
    sample_count: int
    train_window: tuple[str, str]
    percentile_values: np.ndarray
    edge_mean: float | None = None
    edge_std: float | None = None
    is_reliable: bool = True


class ScoreCalibrator:
    """Map raw model scores to empirical percentiles."""

    def __init__(self, calibrations: dict[tuple[str, str, str], CalibrationStats]) -> None:
        self.calibrations = calibrations

    @staticmethod
    def _fallback_percentile(raw_score: float) -> float:
        val = float(raw_score)
        if not np.isfinite(val):
            return float("nan")
        return float(np.clip(val * 100.0, 0.0, 100.0))

    @staticmethod
    def _clean_percentile_values(values: np.ndarray) -> np.ndarray:
        arr = np.asarray(values, dtype=float)
        arr = np.sort(arr[np.isfinite(arr)])
        return arr

    @classmethod
    def fit_for_holdout(
        cls,
        df: pd.DataFrame,
        *,
        score_column: str,
        model_kind: str,
        cluster_column: str = "cluster_name",
        interval_column: str = "interval",
        min_samples: int = 32,
    ) -> "ScoreCalibrator":
        """Fit calibration tables by (cluster, interval) on holdout predictions."""
        if df.empty or score_column not in df.columns:
            return cls({})
        work = df.copy()
        if cluster_column not in work.columns:
            work[cluster_column] = ""
        if interval_column not in work.columns:
            work[interval_column] = ""
        if "datetime" not in work.columns:
            work["datetime"] = pd.NaT
        work["_score"] = pd.to_numeric(work[score_column], errors="coerce")
        work = work.dropna(subset=["_score"])
        if work.empty:
            return cls({})

        cal: dict[tuple[str, str, str], CalibrationStats] = {}
        for (cluster, interval), grp in work.groupby([cluster_column, interval_column], dropna=False):
            values = cls._clean_percentile_values(grp["_score"].to_numpy(dtype=float))
            if values.size == 0:
                continue
            # 101 quantiles (0..100) for stable searchsorted percentile.
            if values.size >= 101:
                pct_values = np.quantile(values, np.linspace(0.0, 1.0, 101))
            else:
                pct_values = values
            start_ts = pd.to_datetime(grp["datetime"], errors="coerce").min()
            end_ts = pd.to_datetime(grp["datetime"], errors="coerce").max()
            stats = CalibrationStats(
                cluster=str(cluster),
                interval=normalize_portfolio_interval(interval),
                model_kind=str(model_kind),
                sample_count=int(len(grp)),
                train_window=(
                    str(start_ts.date()) if pd.notna(start_ts) else "",
                    str(end_ts.date()) if pd.notna(end_ts) else "",
                ),
                percentile_values=np.asarray(pct_values, dtype=float),
                edge_mean=float(np.nanmean(values)),
                edge_std=float(np.nanstd(values)) if np.nanstd(values) > 1e-12 else 1.0,
                is_reliable=bool(len(grp) >= int(min_samples)),
            )
            key = (str(cluster), normalize_portfolio_interval(interval), str(model_kind))
            cal[key] = stats
        return cls(cal)

    def save(self, path: str | Path) -> None:
        p = Path(path)
        p.parent.mkdir(parents=True, exist_ok=True)
        payload = {
            "calibrations": self.calibrations,
        }
        joblib.dump(payload, p)

    @classmethod
    def load(cls, path: str | Path) -> "ScoreCalibrator":
        payload = joblib.load(Path(path))
        calibrations = payload.get("calibrations", {})
        return cls(calibrations)

    def _lookup_stats(self, cluster: str, interval: str, model_kind: str) -> CalibrationStats | None:
        interval_key = normalize_portfolio_interval(interval)
        model_key = str(model_kind)
        cluster_key = str(cluster)
        stats = self.calibrations.get((cluster_key, interval_key, model_key))
        if stats is None:
            stats = self.calibrations.get((cluster_key, str(interval), model_key))
        return stats

    def to_percentile(self, cluster: str, interval: str, model_kind: str, raw_score: float) -> float:
        """Convert raw score to percentile (0-100)."""
        stats = self._lookup_stats(cluster, interval, model_kind)
        if stats is None or not bool(getattr(stats, "is_reliable", True)):
            return self._fallback_percentile(raw_score)
        values = self._clean_percentile_values(stats.percentile_values)
        if values.size == 0:
            return self._fallback_percentile(raw_score)
        raw = float(raw_score)
        if not np.isfinite(raw):
            return float("nan")
        idx = int(np.searchsorted(values, raw, side="right"))
        if values.size == 101:
            return float(np.clip(idx, 0, 100))
        return float(np.clip(idx / float(values.size) * 100.0, 0.0, 100.0))

    def to_edge_z(self, cluster: str, interval: str, model_kind: str, raw_score: float) -> float:
        """Map raw score to z-score using stored edge mean/std when available."""
        stats = self._lookup_stats(cluster, interval, model_kind)
        raw = float(raw_score)
        if stats is None or not np.isfinite(raw):
            return float("nan")
        mu = float(stats.edge_mean) if stats.edge_mean is not None else 0.0
        std = float(stats.edge_std) if stats.edge_std is not None else 1.0
        if not np.isfinite(std) or std <= 1e-12:
            std = 1.0
        return float((raw - mu) / std)

    def _resolve_cluster_column(self, df: pd.DataFrame) -> str | None:
        for col in ("cluster_name", "cluster", "symbol_cluster"):
            if col in df.columns:
                return col
        return None

    def transform(self, df: pd.DataFrame) -> pd.DataFrame:
        """Append percentile columns to a prediction table."""
        if df.empty:
            return df.copy()

        out = df.copy()
        cluster_col = self._resolve_cluster_column(out)
        if cluster_col is None:
            logger.warning("ScoreCalibrator fallback: no cluster column in dataframe.")
            out["__cluster"] = ""
        else:
            out["__cluster"] = out[cluster_col].astype(str)
        out["__interval"] = out.get("interval", pd.Series([""] * len(out), index=out.index)).astype(str)

        mapping: list[tuple[str, str]] = [
            ("trade_filter_prob", "trade_filter"),
            ("final_decision_score", "final_decision_stack"),
            ("pred_edge_atr", "mfe_mae_edge"),
        ]

        for score_col, model_kind in mapping:
            if score_col not in out.columns:
                continue
            pct_col = f"{score_col}_pctl"
            raw = pd.to_numeric(out[score_col], errors="coerce")
            out[pct_col] = np.nan
            for (cluster, interval), idx in out.groupby(["__cluster", "__interval"], dropna=False).groups.items():
                stats = self._lookup_stats(str(cluster), str(interval), model_kind)
                sub_raw = raw.loc[idx].to_numpy(dtype=float)
                if stats is None or not bool(getattr(stats, "is_reliable", True)):
                    out.loc[idx, pct_col] = np.clip(sub_raw * 100.0, 0.0, 100.0)
                    continue
                values = self._clean_percentile_values(stats.percentile_values)
                if values.size == 0:
                    out.loc[idx, pct_col] = np.clip(sub_raw * 100.0, 0.0, 100.0)
                    continue
                valid_mask = np.isfinite(sub_raw)
                pct = np.full(sub_raw.shape[0], np.nan, dtype=float)
                if np.any(valid_mask):
                    valid = sub_raw[valid_mask]
                    idxs = np.searchsorted(values, valid, side="right")
                    if values.size == 101:
                        pct_vals = np.clip(idxs, 0, 100).astype(float)
                    else:
                        pct_vals = np.clip(idxs / float(values.size) * 100.0, 0.0, 100.0)
                    pct[valid_mask] = pct_vals
                out.loc[idx, pct_col] = pct

        return out.drop(columns=["__cluster", "__interval"])

