"""Score distribution drift monitor (W9)."""
from __future__ import annotations

import json
from dataclasses import dataclass
from math import log
from pathlib import Path
from typing import TYPE_CHECKING

import pandas as pd

from cta.risk.state.score_distribution_tracker import (
    ScoreDistributionTracker,
    normalize_score_value,
)

if TYPE_CHECKING:
    from cta.risk.guards.config import ScoreDistributionDriftConfig


@dataclass(frozen=True)
class DriftAssessment:
    severity: str  # insufficient / normal / warning / critical / emergency
    value: float
    metric: str
    sample_count: int


class ScoreDistributionDriftMonitor:
    """Compute rolling distribution drift against train reference scores."""

    def __init__(
        self,
        cfg: "ScoreDistributionDriftConfig | None" = None,
        *,
        reference_scores: list[float] | None = None,
        bins: int = 20,
    ) -> None:
        if cfg is None:
            from cta.risk.guards.config import ScoreDistributionDriftConfig

            cfg = ScoreDistributionDriftConfig()
        self.cfg = cfg
        self.tracker = ScoreDistributionTracker(
            rolling_window_hours=self.cfg.rolling_window_hours,
        )
        self.bins = int(max(5, bins))
        self._reference_scores = self._load_reference_scores(reference_scores)

    def observe(self, score: float | int | None, dt: pd.Timestamp) -> bool:
        return self.tracker.observe(score, dt)

    def assess(self, now: pd.Timestamp | None = None) -> DriftAssessment:
        current_scores = self.tracker.values(now=now)
        if (
            len(current_scores) < self.cfg.min_samples_for_assessment
            or len(self._reference_scores) < self.cfg.min_samples_for_assessment
        ):
            return DriftAssessment(
                severity="insufficient",
                value=0.0,
                metric=self.cfg.drift_metric,
                sample_count=len(current_scores),
            )
        value = self._compute_metric(self._reference_scores, current_scores)
        severity = self._severity_for_value(value)
        return DriftAssessment(
            severity=severity,
            value=float(value),
            metric=self.cfg.drift_metric,
            sample_count=len(current_scores),
        )

    # ── internals ───────────────────────────────────────────────────

    def _severity_for_value(self, value: float) -> str:
        if value >= self.cfg.emergency_threshold:
            return "emergency"
        if value >= self.cfg.critical_threshold:
            return "critical"
        if value >= self.cfg.warning_threshold:
            return "warning"
        return "normal"

    def _compute_metric(self, ref_scores: list[float], cur_scores: list[float]) -> float:
        ref_hist = _histogram(ref_scores, bins=self.bins)
        cur_hist = _histogram(cur_scores, bins=self.bins)
        metric = self.cfg.drift_metric
        if metric == "ks":
            return _ks_distance(ref_hist, cur_hist)
        if metric == "wasserstein":
            return _wasserstein_1d(ref_hist, cur_hist)
        return _kl_divergence(cur_hist, ref_hist)

    def _load_reference_scores(self, scores: list[float] | None) -> list[float]:
        raw = scores
        if raw is None and self.cfg.train_distribution_path:
            p = Path(self.cfg.train_distribution_path)
            if p.exists():
                try:
                    payload = json.loads(p.read_text(encoding="utf-8"))
                except Exception:  # noqa: BLE001
                    payload = {}
                if isinstance(payload, dict):
                    v = payload.get("scores")
                    if not isinstance(v, list):
                        v = payload.get("reference_scores")
                    if isinstance(v, list):
                        raw = v
        normalized: list[float] = []
        for item in raw or []:
            value = normalize_score_value(item)
            if value is not None:
                normalized.append(value)
        return normalized


def _histogram(scores: list[float], *, bins: int) -> list[float]:
    counts = [0.0] * int(bins)
    if not scores:
        return counts
    for value in scores:
        idx = int(value * bins)
        if idx >= bins:
            idx = bins - 1
        if idx < 0:
            idx = 0
        counts[idx] += 1.0
    total = sum(counts)
    if total <= 0.0:
        return [0.0] * bins
    return [c / total for c in counts]


def _kl_divergence(p_hist: list[float], q_hist: list[float], eps: float = 1e-9) -> float:
    val = 0.0
    for p, q in zip(p_hist, q_hist):
        p1 = max(float(p), eps)
        q1 = max(float(q), eps)
        val += p1 * log(p1 / q1)
    return float(val)


def _ks_distance(p_hist: list[float], q_hist: list[float]) -> float:
    p_cum = 0.0
    q_cum = 0.0
    max_gap = 0.0
    for p, q in zip(p_hist, q_hist):
        p_cum += float(p)
        q_cum += float(q)
        gap = abs(p_cum - q_cum)
        if gap > max_gap:
            max_gap = gap
    return float(max_gap)


def _wasserstein_1d(p_hist: list[float], q_hist: list[float]) -> float:
    # Discrete 1D Wasserstein distance approximation using CDF gaps.
    p_cum = 0.0
    q_cum = 0.0
    distance = 0.0
    n = max(1, len(p_hist))
    step = 1.0 / float(n)
    for p, q in zip(p_hist, q_hist):
        p_cum += float(p)
        q_cum += float(q)
        distance += abs(p_cum - q_cum) * step
    return float(distance)


__all__ = ["DriftAssessment", "ScoreDistributionDriftMonitor"]
